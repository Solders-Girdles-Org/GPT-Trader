"""Account-related CLI commands."""

from __future__ import annotations

import asyncio
import json
from argparse import ArgumentTypeError, Namespace
from decimal import Decimal, InvalidOperation
from typing import Any

from gpt_trader.app.container import ApplicationContainer, create_application_container
from gpt_trader.cli import options, services
from gpt_trader.cli.response import CliErrorCode, CliResponse
from gpt_trader.features.brokerages.accounts import PreviewRequest
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
from gpt_trader.utilities.logging_patterns import get_logger

SNAPSHOT_COMMAND_NAME = "account snapshot"
OBSERVE_COMMAND_NAME = "account observe"
PREVIEW_COMMAND_NAME = "account preview"
DIAGNOSE_COMMAND_NAME = "account diagnose"

logger = get_logger(__name__, component="account_cli")


def _positive_decimal_value(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ArgumentTypeError("must be a decimal number") from None
    if not value.is_finite() or value <= 0:
        raise ArgumentTypeError("must be a positive finite decimal")
    return value


def _coinbase_error_response(
    *,
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
        (CoinbaseAccountViolation, CoinbasePreviewViolation, PermissionDeniedError),
    ):
        code = CliErrorCode.POLICY_VIOLATION
    else:
        code = CliErrorCode.API_ERROR
    return CliResponse.error_response(
        command=command,
        code=code,
        message=f"Coinbase {failure_label} failed: {exc}",
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
    observe.add_argument("--provider", required=True, choices=("coinbase",))
    options.add_output_options(observe, include_quiet=False)
    observe.set_defaults(handler=_handle_observe, subcommand="observe")

    preview = account_subparsers.add_parser(
        "preview",
        help="Request a non-binding provider order preview",
    )
    options.add_profile_option(preview, inherit_from_parent=True)
    preview.add_argument("--provider", required=True, choices=("coinbase",))
    preview.add_argument("--instrument", required=True)
    preview.add_argument("--side", required=True, choices=("buy", "sell"))
    preview.add_argument("--quantity", required=True, type=_positive_decimal_value)
    preview.add_argument("--order-type", required=True, choices=("market", "limit"))
    preview.add_argument("--limit-price", type=_positive_decimal_value)
    options.add_output_options(preview, include_quiet=False)
    preview.set_defaults(handler=_handle_preview, subcommand="preview")

    diagnose = account_subparsers.add_parser(
        "diagnose",
        help="Diagnose Coinbase credentials, permissions, and market-data access",
    )
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
    try:
        container = getattr(args, "application_container", None)
        config = (
            container.config
            if container is not None
            else services.build_config_from_args(args, skip={"account_command", "provider"})
        )
        access = _build_coinbase_access(config, container=container)
    except Exception as exc:  # noqa: BLE001
        return CliResponse.error_response(
            command=OBSERVE_COMMAND_NAME,
            code=CliErrorCode.CONFIG_INVALID,
            message=f"Failed to initialize Coinbase account access: {exc}",
        )

    try:
        observation = access.reader.read_account()
        return CliResponse.success_response(
            command=OBSERVE_COMMAND_NAME,
            data=observation.to_dict(),
            warnings=[*access.warnings, *observation.warnings],
        )
    except Exception as exc:  # noqa: BLE001
        return _coinbase_error_response(
            command=OBSERVE_COMMAND_NAME,
            failure_label="account observation",
            exc=exc,
            warnings=access.warnings,
        )
    finally:
        _close_coinbase_access(access)


def _handle_preview(args: Namespace) -> CliResponse:
    try:
        request = PreviewRequest(
            instrument=args.instrument,
            side=args.side,
            quantity=args.quantity,
            order_type=args.order_type,
            limit_price=args.limit_price,
        )
    except ValueError as exc:
        return CliResponse.error_response(
            command=PREVIEW_COMMAND_NAME,
            code=CliErrorCode.INVALID_ARGUMENT,
            message=str(exc),
        )

    try:
        container = getattr(args, "application_container", None)
        config = (
            container.config
            if container is not None
            else services.build_config_from_args(args, skip={"account_command", "provider"})
        )
        access = _build_coinbase_access(config, container=container)
    except Exception as exc:  # noqa: BLE001
        return CliResponse.error_response(
            command=PREVIEW_COMMAND_NAME,
            code=CliErrorCode.CONFIG_INVALID,
            message=f"Failed to initialize Coinbase account access: {exc}",
        )

    try:
        result = access.preview_provider.preview(request)
        if result.errors:
            return CliResponse.error_response(
                command=PREVIEW_COMMAND_NAME,
                code=CliErrorCode.OPERATION_FAILED,
                message="Coinbase rejected the preview request",
                details={"provider_errors": list(result.errors)},
                warnings=[*access.warnings, *result.warnings],
            )
        return CliResponse.success_response(
            command=PREVIEW_COMMAND_NAME,
            data=result.to_dict(),
            warnings=[*access.warnings, *result.warnings],
            was_noop=True,
        )
    except Exception as exc:  # noqa: BLE001
        return _coinbase_error_response(
            command=PREVIEW_COMMAND_NAME,
            failure_label="preview",
            exc=exc,
            warnings=access.warnings,
        )
    finally:
        _close_coinbase_access(access)


def _build_coinbase_access(
    config: Any,
    *,
    container: ApplicationContainer | None = None,
) -> CoinbaseReadPreviewAccess:
    composition_root = container or create_application_container(config)
    return composition_root.create_coinbase_read_preview_access()


def _close_coinbase_access(access: Any) -> None:
    try:
        close = getattr(access, "close", None)
        if callable(close):
            close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to close Coinbase account access",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def _handle_diagnose(args: Namespace) -> CliResponse | int:
    output_format = getattr(args, "output_format", "text")

    from gpt_trader.features.brokerages.coinbase.auth import SimpleAuth
    from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient
    from gpt_trader.features.brokerages.coinbase.credentials import resolve_coinbase_credentials

    creds = resolve_coinbase_credentials()
    if not creds:
        message = (
            "Coinbase credentials not found. Set COINBASE_CREDENTIALS_FILE to a JSON key file, "
            "or set COINBASE_CDP_API_KEY + COINBASE_CDP_PRIVATE_KEY."
        )
        if output_format == "json":
            return CliResponse.error_response(
                command=DIAGNOSE_COMMAND_NAME,
                code=CliErrorCode.CONFIG_INVALID,
                message=message,
            )
        print(message)
        return 1

    diag: dict[str, Any] = {
        "credential_source": creds.source,
        "masked_key_name": creds.masked_key_name,
        "warnings": list(creds.warnings),
    }

    auth = SimpleAuth(key_name=creds.key_name, private_key=creds.private_key)
    client = CoinbaseClient(base_url="https://api.coinbase.com", auth=auth, api_mode="advanced")

    try:
        # 1) Key permissions (auth-required)
        try:
            permissions = client.get_key_permissions() or {}
            diag["key_permissions"] = {
                "can_view": bool(permissions.get("can_view")),
                "can_trade": bool(permissions.get("can_trade")),
                "portfolio_type": str(permissions.get("portfolio_type") or ""),
                "portfolio_uuid": str(permissions.get("portfolio_uuid") or ""),
            }
        except Exception as exc:  # noqa: BLE001
            diag["key_permissions_error"] = f"{type(exc).__name__}: {exc}"

        # 2) Accounts (auth-required)
        try:
            accounts_payload = client.get_accounts() or {}
            accounts = accounts_payload.get("accounts", [])
            non_zero = []
            for a in accounts or []:
                try:
                    currency = str(a.get("currency") or "")
                    avail = str(a.get("available_balance", {}).get("value", "0"))
                    hold = str(a.get("hold", {}).get("value", "0"))
                    total = Decimal(avail) + Decimal(hold)
                    if total > 0:
                        non_zero.append({"asset": currency, "total": str(total)})
                except Exception:  # noqa: BLE001
                    continue

            diag["accounts"] = {
                "count": len(accounts) if isinstance(accounts, list) else 0,
                "non_zero_count": len(non_zero),
                "non_zero_sample": non_zero[:10],
            }
        except Exception as exc:  # noqa: BLE001
            diag["accounts_error"] = f"{type(exc).__name__}: {exc}"

        # 3) Market ticker + candles (public)
        try:
            # Prefer normalized market ticker so we always surface a usable price
            # even when the raw public payload omits a top-level "price" field.
            normalized = client.get_ticker("BTC-USD") or {}
            diag["market_ticker"] = {
                "product_id": "BTC-USD",
                "price": str(normalized.get("price") or ""),
                "bid": str(normalized.get("bid") or ""),
                "ask": str(normalized.get("ask") or ""),
            }

            # If the price still isn't available, include raw keys for debugging
            # (safe: market data is public and contains no secrets).
            if not diag["market_ticker"]["price"] or diag["market_ticker"]["price"] == "0":
                raw_public = client.get_market_product_ticker("BTC-USD") or {}
                if isinstance(raw_public, dict):
                    diag["market_ticker"]["raw_keys"] = sorted(list(raw_public.keys()))[:50]
        except Exception as exc:  # noqa: BLE001
            diag["market_ticker_error"] = f"{type(exc).__name__}: {exc}"

        try:
            candles = client.get_market_product_candles("BTC-USD", "ONE_MINUTE", limit=2) or {}
            candle_count = len(candles.get("candles", []) or []) if isinstance(candles, dict) else 0
            diag["market_candles"] = {"product_id": "BTC-USD", "count": candle_count}
        except Exception as exc:  # noqa: BLE001
            diag["market_candles_error"] = f"{type(exc).__name__}: {exc}"

        if output_format == "json":
            return CliResponse.success_response(command=DIAGNOSE_COMMAND_NAME, data=diag)

        print(json.dumps(diag, indent=2, default=str))
        return 0

    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
