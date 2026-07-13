"""Account-related CLI commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
from argparse import ArgumentTypeError, Namespace
from decimal import Decimal, InvalidOperation
from typing import Any

from gpt_trader.app.container import ApplicationContainer, create_application_container
from gpt_trader.cli import options, services
from gpt_trader.cli.response import CliErrorCode, CliResponse
from gpt_trader.features.brokerages.accounts import AccountObservation, PreviewRequest
from gpt_trader.features.brokerages.coinbase.account_access import (
    CoinbaseAccountViolation,
)
from gpt_trader.features.brokerages.coinbase.errors import (
    AuthError,
    PermissionDeniedError,
    RateLimitError,
    TransientBrokerError,
)
from gpt_trader.features.brokerages.coinbase.preview_access import (
    CoinbasePreviewViolation,
)
from gpt_trader.features.brokerages.coinbase.read_preview_access import (
    CoinbaseReadPreviewAccess,
)
from gpt_trader.features.brokerages.robinhood.agentic.errors import (
    RobinhoodAgenticViolation,
)
from gpt_trader.features.brokerages.robinhood.agentic.models import (
    RobinhoodAgenticOptionReviewEvidence,
    RobinhoodAgenticOptionReviewRequest,
)
from gpt_trader.features.brokerages.robinhood.crypto.account_access import (
    RobinhoodCryptoViolation,
)
from gpt_trader.utilities.logging_patterns import get_logger

SNAPSHOT_COMMAND_NAME = "account snapshot"
OBSERVE_COMMAND_NAME = "account observe"
PREVIEW_COMMAND_NAME = "account preview"
DIAGNOSE_COMMAND_NAME = "account diagnose"
PROVIDER_CHOICES = ("coinbase", "robinhood-crypto", "robinhood-agentic")
_PROVIDER_LABELS = {
    "coinbase": "Coinbase",
    "robinhood-crypto": "Robinhood Crypto",
    "robinhood-agentic": "Robinhood Agentic",
}

logger = get_logger(__name__, component="account_cli")


def _positive_decimal_value(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ArgumentTypeError("must be a decimal number") from None
    if not value.is_finite() or value <= 0:
        raise ArgumentTypeError("must be a positive finite decimal")
    return value


def _provider_error_response(
    *,
    provider: str,
    command: str,
    failure_label: str,
    exc: Exception,
    warnings: tuple[str, ...] = (),
) -> CliResponse:
    if isinstance(exc, AuthError):
        code = CliErrorCode.AUTHENTICATION_FAILED
    elif isinstance(exc, RateLimitError):
        code = CliErrorCode.RATE_LIMITED
    elif isinstance(exc, TransientBrokerError):
        code = CliErrorCode.NETWORK_ERROR
    elif isinstance(
        exc,
        (
            CoinbaseAccountViolation,
            CoinbasePreviewViolation,
            PermissionDeniedError,
            RobinhoodCryptoViolation,
            RobinhoodAgenticViolation,
        ),
    ):
        code = CliErrorCode.POLICY_VIOLATION
    else:
        code = CliErrorCode.API_ERROR
    return CliResponse.error_response(
        command=command,
        code=code,
        message=f"{_PROVIDER_LABELS[provider]} {failure_label} failed: {exc}",
        warnings=list(warnings),
    )


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("account", help="Account utilities")
    options.add_profile_option(parser, allow_missing_default=True)
    account_subparsers = parser.add_subparsers(dest="account_command", required=True)

    snapshot = account_subparsers.add_parser("snapshot", help="Print an account snapshot")
    options.add_profile_option(snapshot, inherit_from_parent=True)
    options.add_output_options(snapshot, include_quiet=False)
    snapshot.set_defaults(handler=_handle_snapshot, subcommand="snapshot")

    observe = account_subparsers.add_parser(
        "observe",
        help="Read a provider-attested real-account observation",
    )
    options.add_profile_option(observe, inherit_from_parent=True)
    observe.add_argument("--provider", required=True, choices=PROVIDER_CHOICES)
    options.add_output_options(observe, include_quiet=False)
    observe.set_defaults(handler=_handle_observe, subcommand="observe")

    preview = account_subparsers.add_parser(
        "preview",
        help="Request a non-binding provider order preview",
    )
    options.add_profile_option(preview, inherit_from_parent=True)
    preview.add_argument("--provider", required=True, choices=PROVIDER_CHOICES)
    preview.add_argument(
        "--product-type",
        choices=("equity", "option"),
        default="equity",
        help="Preview shape; option is accepted only by Robinhood Agentic",
    )
    preview.add_argument(
        "--instrument",
        required=True,
        help="Symbol for equity/crypto previews or provider option id for option reviews",
    )
    preview.add_argument("--side", required=True, choices=("buy", "sell"))
    preview.add_argument("--quantity", required=True, type=_positive_decimal_value)
    preview.add_argument(
        "--order-type",
        required=True,
        choices=("market", "limit", "stop_limit", "stop_market"),
    )
    preview.add_argument("--limit-price", type=_positive_decimal_value)
    preview.add_argument("--stop-price", type=_positive_decimal_value)
    preview.add_argument("--position-effect", choices=("open", "close"))
    preview.add_argument(
        "--time-in-force",
        dest="preview_time_in_force",
        choices=("gfd", "gtc"),
        default="gfd",
    )
    preview.add_argument(
        "--market-hours",
        choices=(
            "regular_hours",
            "regular_curb_hours",
            "regular_curb_overnight_hours",
        ),
        default="regular_hours",
    )
    preview.add_argument("--chain-symbol")
    preview.add_argument("--underlying-type", choices=("equity", "index"))
    options.add_output_options(preview, include_quiet=False)
    preview.set_defaults(handler=_handle_preview, subcommand="preview")

    diagnose = account_subparsers.add_parser(
        "diagnose",
        help="Diagnose provider-attested account observation access",
    )
    options.add_profile_option(diagnose, inherit_from_parent=True)
    diagnose.add_argument("--provider", choices=PROVIDER_CHOICES, default="coinbase")
    options.add_output_options(diagnose, include_quiet=False)
    diagnose.set_defaults(handler=_handle_diagnose, subcommand="diagnose")


def _handle_snapshot(args: Namespace) -> CliResponse | int:
    output_format = getattr(args, "output_format", "text")

    try:
        config = services.build_config_from_args(args, skip={"account_command"})
        bot = services.instantiate_bot(config)
    except Exception as e:
        if output_format == "json":
            return CliResponse.error_response(
                command=SNAPSHOT_COMMAND_NAME,
                code=CliErrorCode.CONFIG_INVALID,
                message=f"Failed to initialize: {e}",
            )
        raise

    try:
        # Wired by TradingBot from the container broker (account-snapshot ADR
        # Option A, docs/decisions/account-snapshot-wire-or-remove.md); only a
        # broker-less container leaves it unset.
        telemetry = getattr(bot, "account_telemetry", None)
        if telemetry is None or not telemetry.supports_snapshots():
            message = "Account snapshot is unavailable: no broker is wired for this profile"
            if output_format == "json":
                return CliResponse.error_response(
                    command=SNAPSHOT_COMMAND_NAME,
                    code=CliErrorCode.OPERATION_FAILED,
                    message=message,
                )
            raise RuntimeError(message)

        snapshot = telemetry.collect_snapshot()

        if output_format == "json":
            return CliResponse.success_response(
                command=SNAPSHOT_COMMAND_NAME,
                data=snapshot,
            )

        print(json.dumps(snapshot, indent=2, default=str))
        return 0

    finally:
        asyncio.run(bot.shutdown())


def _handle_observe(args: Namespace) -> CliResponse:
    return _handle_account_read(args, command=OBSERVE_COMMAND_NAME, diagnose=False)


def _handle_preview(args: Namespace) -> CliResponse:
    try:
        request = _preview_request_from_args(args)
    except ValueError as exc:
        return CliResponse.error_response(
            command=PREVIEW_COMMAND_NAME,
            code=CliErrorCode.INVALID_ARGUMENT,
            message=str(exc),
        )

    if args.provider == "robinhood-agentic":
        return asyncio.run(_handle_agentic_preview(args, request))
    if isinstance(request, RobinhoodAgenticOptionReviewRequest):
        raise AssertionError("option requests must route through Robinhood Agentic")
    return _handle_sync_preview(args, request)


def _preview_request_from_args(
    args: Namespace,
) -> PreviewRequest | RobinhoodAgenticOptionReviewRequest:
    product_type = getattr(args, "product_type", "equity")
    if product_type == "option":
        if args.provider != "robinhood-agentic":
            raise ValueError("option reviews are accepted only by Robinhood Agentic")
        if getattr(args, "position_effect", None) is None:
            raise ValueError("--position-effect is required for option reviews")
        integral_quantity = args.quantity.to_integral_value()
        if args.quantity != integral_quantity:
            raise ValueError("option review quantity must be a whole number")
        return RobinhoodAgenticOptionReviewRequest(
            option_id=args.instrument,
            side=args.side,
            position_effect=args.position_effect,
            quantity=int(integral_quantity),
            order_type=args.order_type,
            price=args.limit_price,
            stop_price=getattr(args, "stop_price", None),
            time_in_force=getattr(args, "preview_time_in_force", "gfd"),
            market_hours=getattr(args, "market_hours", "regular_hours"),
            chain_symbol=getattr(args, "chain_symbol", None),
            underlying_type=getattr(args, "underlying_type", None),
        )

    option_only_values = (
        getattr(args, "position_effect", None),
        getattr(args, "stop_price", None),
        getattr(args, "chain_symbol", None),
        getattr(args, "underlying_type", None),
    )
    option_only_override = (
        getattr(args, "preview_time_in_force", "gfd") != "gfd"
        or getattr(args, "market_hours", "regular_hours") != "regular_hours"
    )
    if any(value is not None for value in option_only_values) or option_only_override:
        raise ValueError("option-specific arguments require --product-type option")
    return PreviewRequest(
        instrument=args.instrument,
        side=args.side,
        quantity=args.quantity,
        order_type=args.order_type,
        limit_price=args.limit_price,
    )


def _resolve_access_inputs(args: Namespace) -> tuple[Any, ApplicationContainer | None]:
    container = getattr(args, "application_container", None)
    config = (
        container.config
        if container is not None
        else services.build_config_from_args(args, skip={"account_command", "provider"})
    )
    return config, container


def _build_sync_access(
    provider: str,
    config: Any,
    *,
    container: ApplicationContainer | None = None,
) -> Any:
    if provider == "coinbase":
        return _build_coinbase_access(config, container=container)
    if provider == "robinhood-crypto":
        composition_root = container or create_application_container(config)
        return composition_root.create_robinhood_crypto_read_preview_access()
    raise ValueError(f"unsupported synchronous account provider: {provider}")


async def _build_agentic_access(
    config: Any,
    *,
    container: ApplicationContainer | None = None,
) -> Any:
    composition_root = container or create_application_container(config)
    return await composition_root.create_robinhood_agentic_read_review_access()


def _handle_sync_preview(args: Namespace, request: PreviewRequest) -> CliResponse:
    try:
        config, container = _resolve_access_inputs(args)
        access = _build_sync_access(args.provider, config, container=container)
    except Exception as exc:  # noqa: BLE001
        return _initialization_error(args.provider, PREVIEW_COMMAND_NAME, exc)

    try:
        result = access.preview_provider.preview(request)
        return _preview_response(
            provider=args.provider,
            data=result.to_dict(),
            errors=result.errors,
            warnings=(*getattr(access, "warnings", ()), *result.warnings),
        )
    except Exception as exc:  # noqa: BLE001
        return _provider_error_response(
            provider=args.provider,
            command=PREVIEW_COMMAND_NAME,
            failure_label="preview",
            exc=exc,
            warnings=tuple(getattr(access, "warnings", ())),
        )
    finally:
        _close_sync_access(args.provider, access)


async def _handle_agentic_preview(
    args: Namespace,
    request: PreviewRequest | RobinhoodAgenticOptionReviewRequest,
) -> CliResponse:
    try:
        config, container = _resolve_access_inputs(args)
        access = await _build_agentic_access(config, container=container)
    except Exception as exc:  # noqa: BLE001
        return _initialization_error(args.provider, PREVIEW_COMMAND_NAME, exc)

    try:
        if isinstance(request, RobinhoodAgenticOptionReviewRequest):
            evidence = await access.review_option_order(request)
            data = _option_review_to_dict(evidence)
            errors = (
                ()
                if not evidence.errors
                else (
                    "provider order checks: " f"{data['provider_evidence']['order_checks_json']}",
                )
            )
            warnings: tuple[str, ...] = ()
        else:
            evidence = await access.review_equity_order(request)
            order_checks_json, order_checks_sha256 = _sanitize_provider_json(
                evidence.order_checks_json
            )
            quote_json, quote_sha256 = _sanitize_provider_json(evidence.quote_json)
            data = {
                **evidence.preview.to_dict(),
                "provider_evidence": {
                    "order_checks_json": order_checks_json,
                    "order_checks_sha256": order_checks_sha256,
                    "quote_json": quote_json,
                    "quote_sha256": quote_sha256,
                    "market_data_disclosure": evidence.market_data_disclosure,
                },
            }
            errors = (
                ()
                if not evidence.preview.errors
                else (f"provider order checks: {order_checks_json}",)
            )
            warnings = evidence.preview.warnings
        return _preview_response(
            provider=args.provider,
            data=data,
            errors=errors,
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001
        return _provider_error_response(
            provider=args.provider,
            command=PREVIEW_COMMAND_NAME,
            failure_label="review",
            exc=exc,
        )
    finally:
        await _close_agentic_access(access)


def _option_review_to_dict(evidence: RobinhoodAgenticOptionReviewEvidence) -> dict[str, Any]:
    request = evidence.request
    order_checks_json, order_checks_sha256 = _sanitize_provider_json(evidence.order_checks_json)
    quotes_json, quotes_sha256 = _sanitize_provider_json(evidence.quotes_json)
    collateral_json, collateral_sha256 = _sanitize_provider_json(evidence.collateral_json)
    return {
        "provider": "robinhood-agentic",
        "kind": "provider_simulation",
        "generated_at": evidence.generated_at.isoformat(),
        "identity_fingerprint": evidence.identity_fingerprint,
        "request": {
            "product_type": "option",
            "option_id": request.option_id,
            "side": request.side.value.lower(),
            "position_effect": request.position_effect,
            "quantity": request.quantity,
            "order_type": request.order_type,
            "price": None if request.price is None else str(request.price),
            "stop_price": None if request.stop_price is None else str(request.stop_price),
            "time_in_force": request.time_in_force,
            "market_hours": request.market_hours,
            "chain_symbol": request.chain_symbol,
            "underlying_type": request.underlying_type,
        },
        "estimated_fee": None if evidence.total_fee is None else str(evidence.total_fee),
        "errors": list(evidence.errors),
        "non_binding": True,
        "provider_evidence": {
            "order_checks_json": order_checks_json,
            "order_checks_sha256": order_checks_sha256,
            "quotes_json": quotes_json,
            "quotes_sha256": quotes_sha256,
            "collateral_json": collateral_json,
            "collateral_sha256": collateral_sha256,
        },
    }


_SENSITIVE_EVIDENCE_KEYS = frozenset(
    {"account_id", "account_number", "account_uuid", "portfolio_id", "portfolio_uuid"}
)


def _sanitize_provider_json(encoded: str) -> tuple[str, str]:
    """Redact provider identifiers while retaining immutable evidence integrity."""
    payload = json.loads(encoded)

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ("<redacted>" if key.lower() in _SENSITIVE_EVIDENCE_KEYS else redact(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    sanitized = json.dumps(redact(payload), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
    return sanitized, digest


def _preview_response(
    *,
    provider: str,
    data: dict[str, Any],
    errors: tuple[str, ...],
    warnings: tuple[str, ...],
) -> CliResponse:
    if errors:
        details: dict[str, Any] = {
            "provider_errors": list(errors),
            "non_binding": True,
            "evidence": data,
        }
        return CliResponse.error_response(
            command=PREVIEW_COMMAND_NAME,
            code=CliErrorCode.OPERATION_FAILED,
            message=f"{_PROVIDER_LABELS[provider]} rejected the preview request",
            details=details,
            warnings=list(warnings),
        )
    return CliResponse.success_response(
        command=PREVIEW_COMMAND_NAME,
        data=data,
        warnings=list(warnings),
        was_noop=True,
    )


def _build_coinbase_access(
    config: Any,
    *,
    container: ApplicationContainer | None = None,
) -> CoinbaseReadPreviewAccess:
    composition_root = container or create_application_container(config)
    return composition_root.create_coinbase_read_preview_access()


def _close_sync_access(provider: str, access: Any) -> None:
    try:
        close = getattr(access, "close", None)
        if callable(close):
            close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to close account access",
            provider=provider,
            error_type=type(exc).__name__,
        )


async def _close_agentic_access(access: Any) -> None:
    try:
        await access.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to close account access",
            provider="robinhood-agentic",
            error_type=type(exc).__name__,
        )


def _handle_diagnose(args: Namespace) -> CliResponse:
    return _handle_account_read(args, command=DIAGNOSE_COMMAND_NAME, diagnose=True)


def _handle_account_read(
    args: Namespace,
    *,
    command: str,
    diagnose: bool,
) -> CliResponse:
    if args.provider == "robinhood-agentic":
        return asyncio.run(_handle_agentic_account_read(args, command=command, diagnose=diagnose))

    try:
        config, container = _resolve_access_inputs(args)
        access = _build_sync_access(args.provider, config, container=container)
    except Exception as exc:  # noqa: BLE001
        return _initialization_error(args.provider, command, exc)

    try:
        observation = access.reader.read_account()
        warnings = (*getattr(access, "warnings", ()), *observation.warnings)
        return _observation_response(command, observation, warnings=warnings, diagnose=diagnose)
    except Exception as exc:  # noqa: BLE001
        return _provider_error_response(
            provider=args.provider,
            command=command,
            failure_label="account observation",
            exc=exc,
            warnings=tuple(getattr(access, "warnings", ())),
        )
    finally:
        _close_sync_access(args.provider, access)


async def _handle_agentic_account_read(
    args: Namespace,
    *,
    command: str,
    diagnose: bool,
) -> CliResponse:
    try:
        config, container = _resolve_access_inputs(args)
        access = await _build_agentic_access(config, container=container)
    except Exception as exc:  # noqa: BLE001
        return _initialization_error(args.provider, command, exc)

    try:
        observation = await access.read_account()
        return _observation_response(
            command,
            observation,
            warnings=observation.warnings,
            diagnose=diagnose,
        )
    except Exception as exc:  # noqa: BLE001
        return _provider_error_response(
            provider=args.provider,
            command=command,
            failure_label="account observation",
            exc=exc,
        )
    finally:
        await _close_agentic_access(access)


def _observation_response(
    command: str,
    observation: AccountObservation,
    *,
    warnings: tuple[str, ...],
    diagnose: bool,
) -> CliResponse:
    if diagnose:
        data: dict[str, Any] = {
            "provider": observation.identity.provider.value,
            "status": "available",
            "identity": observation.identity.to_dict(),
            "observation_schema_version": observation.to_dict()["schema_version"],
            "balance_count": len(observation.balances),
            "position_count": len(observation.positions),
            "option_position_count": len(observation.option_positions),
            "buying_power_dimensions": sorted(observation.buying_power),
        }
    else:
        data = observation.to_dict()
    return CliResponse.success_response(command=command, data=data, warnings=list(warnings))


def _initialization_error(provider: str, command: str, exc: Exception) -> CliResponse:
    return CliResponse.error_response(
        command=command,
        code=CliErrorCode.CONFIG_INVALID,
        message=f"Failed to initialize {_PROVIDER_LABELS[provider]} account access: {exc}",
    )
