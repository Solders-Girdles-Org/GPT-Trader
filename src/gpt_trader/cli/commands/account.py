"""Account-related CLI commands."""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from decimal import Decimal
from typing import Any

from gpt_trader.cli import options, services
from gpt_trader.cli.response import CliErrorCode, CliResponse

SNAPSHOT_COMMAND_NAME = "account snapshot"
DIAGNOSE_COMMAND_NAME = "account diagnose"


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("account", help="Account utilities")
    options.add_profile_option(parser, allow_missing_default=True)
    account_subparsers = parser.add_subparsers(dest="account_command", required=True)

    snapshot = account_subparsers.add_parser("snapshot", help="Print an account snapshot")
    options.add_profile_option(snapshot, inherit_from_parent=True)
    options.add_output_options(snapshot, include_quiet=False)
    snapshot.set_defaults(handler=_handle_snapshot, subcommand="snapshot")

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
