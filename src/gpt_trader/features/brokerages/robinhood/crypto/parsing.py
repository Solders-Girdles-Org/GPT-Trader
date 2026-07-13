"""Strict Robinhood Crypto v2 response parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from gpt_trader.features.brokerages.robinhood.crypto.errors import (
    RobinhoodCryptoClientViolation,
)
from gpt_trader.features.brokerages.robinhood.crypto.models import (
    RobinhoodCryptoAccount,
    RobinhoodCryptoEstimate,
    RobinhoodCryptoHolding,
    RobinhoodCryptoOrder,
    RobinhoodCryptoQuote,
    RobinhoodCryptoTradingPair,
)


def result_rows(
    payload: Mapping[str, Any], label: str, *, paginated: bool = False
) -> list[dict[str, Any]]:
    expected_keys = {"next", "previous", "results"} if paginated else {"results"}
    _exact_keys(payload, expected_keys, label)
    rows = payload.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {label} results are malformed")
    return rows


def _exact_keys(
    row: Mapping[str, Any], required: set[str], label: str, *, optional: set[str] | None = None
) -> None:
    optional = optional or set()
    keys = set(row)
    missing = required - keys
    unknown = keys - required - optional
    if missing or unknown:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {label} schema is unsupported")


def _string(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} is missing")
    return value


def _boolean(row: Mapping[str, Any], name: str) -> bool:
    value = row.get(name)
    if type(value) is not bool:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} is malformed")
    return value


def _enum(row: Mapping[str, Any], name: str, allowed: set[str]) -> str:
    value = _string(row, name)
    if value not in allowed:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} is unsupported")
    return value


def _decimal(row: Mapping[str, Any], name: str, *, optional: bool = False) -> Decimal | None:
    value = row.get(name)
    if optional and value in (None, ""):
        return None
    if value in (None, "") or type(value) is bool:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} is missing")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} is malformed") from None
    if not parsed.is_finite():
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} must be finite")
    return parsed


def _required_decimal(row: Mapping[str, Any], name: str) -> Decimal:
    value = _decimal(row, name)
    assert value is not None
    return value


def _nonnegative_decimal(row: Mapping[str, Any], name: str) -> Decimal:
    value = _required_decimal(row, name)
    if value < 0:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} must be nonnegative")
    return value


def _positive_decimal(row: Mapping[str, Any], name: str) -> Decimal:
    value = _required_decimal(row, name)
    if value <= 0:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} must be positive")
    return value


def parse_account(row: Mapping[str, Any]) -> RobinhoodCryptoAccount:
    _exact_keys(
        row,
        {
            "account_number",
            "status",
            "buying_power",
            "buying_power_currency",
            "account_type",
            "is_api_tradable",
        },
        "account",
        optional={"fee_tier_status"},
    )
    fee_tier_status = row.get("fee_tier_status")
    if fee_tier_status is not None and not isinstance(fee_tier_status, dict):
        raise RobinhoodCryptoClientViolation("Robinhood Crypto fee_tier_status is malformed")
    if isinstance(fee_tier_status, dict):
        _exact_keys(
            fee_tier_status,
            {"fee_ratio", "thirty_day_volume"},
            "fee tier status",
            optional={"next_fee_tier_ratio", "next_fee_tier_threshold"},
        )
    return RobinhoodCryptoAccount(
        account_number=_string(row, "account_number"),
        status=_enum(row, "status", {"active", "deactivated", "sell_only"}),
        buying_power=_nonnegative_decimal(row, "buying_power"),
        buying_power_currency=_string(row, "buying_power_currency"),
        account_type=_string(row, "account_type"),
        is_api_tradable=_boolean(row, "is_api_tradable"),
        fee_ratio=(
            None if fee_tier_status is None else _nonnegative_decimal(fee_tier_status, "fee_ratio")
        ),
        thirty_day_volume=(
            None
            if fee_tier_status is None
            else _nonnegative_decimal(fee_tier_status, "thirty_day_volume")
        ),
        next_fee_tier_ratio=(
            None
            if fee_tier_status is None
            else _optional_nonnegative_decimal(fee_tier_status, "next_fee_tier_ratio")
        ),
        next_fee_tier_threshold=(
            None
            if fee_tier_status is None
            else _optional_nonnegative_decimal(fee_tier_status, "next_fee_tier_threshold")
        ),
    )


def parse_holding(row: Mapping[str, Any]) -> RobinhoodCryptoHolding:
    _exact_keys(
        row,
        {"account_number", "asset_code", "total_quantity", "quantity_available_for_trading"},
        "holding",
    )
    return RobinhoodCryptoHolding(
        account_number=_string(row, "account_number"),
        asset_code=_string(row, "asset_code"),
        total_quantity=_nonnegative_decimal(row, "total_quantity"),
        available_quantity=_nonnegative_decimal(row, "quantity_available_for_trading"),
    )


def parse_order(row: Mapping[str, Any]) -> RobinhoodCryptoOrder:
    configuration_names = {
        "market_order_config",
        "limit_order_config",
        "stop_loss_order_config",
        "stop_limit_order_config",
    }
    _exact_keys(
        row,
        {
            "id",
            "account_number",
            "symbol",
            "client_order_id",
            "side",
            "executions",
            "type",
            "state",
            "average_price",
            "filled_asset_quantity",
            "created_at",
            "updated_at",
            "fee_charged",
            "estimated_fee_remaining",
        },
        "order",
        optional=configuration_names,
    )
    order_type = _string(row, "type")
    configurations = {name: row[name] for name in configuration_names if row.get(name) is not None}
    executions = row.get("executions")
    if not isinstance(executions, list) or any(not isinstance(item, dict) for item in executions):
        raise RobinhoodCryptoClientViolation("Robinhood Crypto executions are malformed")
    if not configurations or any(not isinstance(value, dict) for value in configurations.values()):
        raise RobinhoodCryptoClientViolation("Robinhood Crypto order configuration is malformed")
    if set(configurations) != {f"{order_type}_order_config"}:
        raise RobinhoodCryptoClientViolation(
            "Robinhood Crypto order configuration does not match its type"
        )
    _validate_order_configuration(order_type, configurations[f"{order_type}_order_config"])
    for execution in executions:
        _exact_keys(execution, {"effective_price", "quantity", "timestamp"}, "execution")
        _positive_decimal(execution, "effective_price")
        _positive_decimal(execution, "quantity")
        _timestamp_string(execution, "timestamp")
    try:
        executions_json = json.dumps(executions, sort_keys=True, separators=(",", ":"))
        configuration_json = json.dumps(configurations, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RobinhoodCryptoClientViolation(
            "Robinhood Crypto order evidence is malformed"
        ) from exc
    return RobinhoodCryptoOrder(
        order_id=_string(row, "id"),
        account_number=_string(row, "account_number"),
        symbol=_string(row, "symbol"),
        client_order_id=_string(row, "client_order_id"),
        side=_enum(row, "side", {"buy", "sell"}),
        order_type=order_type,
        state=_enum(row, "state", {"open", "canceled", "filled", "failed", "pending"}),
        average_price=_optional_positive_decimal(row, "average_price"),
        filled_asset_quantity=_nonnegative_decimal(row, "filled_asset_quantity"),
        fee_charged=_nonnegative_decimal(row, "fee_charged"),
        estimated_fee_remaining=_nonnegative_decimal(row, "estimated_fee_remaining"),
        created_at=_timestamp_string(row, "created_at"),
        updated_at=_timestamp_string(row, "updated_at"),
        executions_json=executions_json,
        configuration_json=configuration_json,
    )


def parse_trading_pair(row: Mapping[str, Any]) -> RobinhoodCryptoTradingPair:
    _exact_keys(
        row,
        {
            "symbol",
            "asset_code",
            "quote_code",
            "asset_increment",
            "quote_increment",
            "max_order_size",
            "min_order_amount",
            "status",
            "is_api_tradable",
        },
        "trading pair",
    )
    return RobinhoodCryptoTradingPair(
        symbol=_string(row, "symbol"),
        asset_code=_string(row, "asset_code"),
        quote_code=_string(row, "quote_code"),
        asset_increment=_positive_decimal(row, "asset_increment"),
        quote_increment=_positive_decimal(row, "quote_increment"),
        max_order_size=_positive_decimal(row, "max_order_size"),
        min_order_amount=_positive_decimal(row, "min_order_amount"),
        status=_string(row, "status"),
        is_api_tradable=_boolean(row, "is_api_tradable"),
    )


def parse_quote(row: Mapping[str, Any]) -> RobinhoodCryptoQuote:
    _exact_keys(row, {"symbol", "bid", "ask"}, "quote")
    quote = RobinhoodCryptoQuote(
        symbol=_string(row, "symbol"),
        bid=_positive_decimal(row, "bid"),
        ask=_positive_decimal(row, "ask"),
    )
    if quote.ask < quote.bid:
        raise RobinhoodCryptoClientViolation("Robinhood Crypto quote spread is inverted")
    return quote


def parse_estimate(row: Mapping[str, Any]) -> RobinhoodCryptoEstimate:
    _exact_keys(
        row,
        {
            "symbol",
            "side",
            "quantity",
            "timestamp",
            "bid",
            "ask",
            "fee_ratio",
            "est_fee",
            "est_total_cost",
            "est_total_credit",
        },
        "estimated price",
    )
    timestamp_value = _timestamp_string(row, "timestamp")
    timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
    estimate = RobinhoodCryptoEstimate(
        symbol=_string(row, "symbol"),
        side=_string(row, "side"),
        quantity=_positive_decimal(row, "quantity"),
        timestamp=timestamp.astimezone(UTC),
        bid=_positive_decimal(row, "bid"),
        ask=_positive_decimal(row, "ask"),
        fee_ratio=_nonnegative_decimal(row, "fee_ratio"),
        estimated_fee=_nonnegative_decimal(row, "est_fee"),
        estimated_total_cost=_nonnegative_decimal(row, "est_total_cost"),
        estimated_total_credit=_nonnegative_decimal(row, "est_total_credit"),
    )
    if estimate.ask < estimate.bid:
        raise RobinhoodCryptoClientViolation("Robinhood Crypto estimated-price spread is inverted")
    return estimate


def _optional_positive_decimal(row: Mapping[str, Any], name: str) -> Decimal | None:
    value = _decimal(row, name, optional=True)
    if value is not None and value <= 0:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} must be positive")
    return value


def _optional_nonnegative_decimal(row: Mapping[str, Any], name: str) -> Decimal | None:
    value = _decimal(row, name, optional=True)
    if value is not None and value < 0:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} must be nonnegative")
    return value


def _validate_order_configuration(order_type: str, configuration: Mapping[str, Any]) -> None:
    schemas = {
        "market": ({"asset_quantity"}, set()),
        "limit": ({"limit_price"}, {"asset_quantity", "quote_amount"}),
        "stop_loss": ({"stop_price", "time_in_force"}, {"asset_quantity", "quote_amount"}),
        "stop_limit": (
            {"limit_price", "stop_price", "time_in_force"},
            {"asset_quantity", "quote_amount"},
        ),
    }
    if order_type not in schemas:
        raise RobinhoodCryptoClientViolation("Robinhood Crypto order type is unsupported")
    required, optional = schemas[order_type]
    _exact_keys(configuration, required, f"{order_type} order configuration", optional=optional)
    if not ({"asset_quantity", "quote_amount"} & set(configuration)):
        raise RobinhoodCryptoClientViolation("Robinhood Crypto order configuration has no quantity")
    for name in ("asset_quantity", "quote_amount", "limit_price", "stop_price"):
        if name in configuration:
            _positive_decimal(configuration, name)
    if "time_in_force" in configuration and _string(configuration, "time_in_force") != "gtc":
        raise RobinhoodCryptoClientViolation("Robinhood Crypto order time_in_force is unsupported")


def _timestamp_string(row: Mapping[str, Any], name: str) -> str:
    timestamp_value = _string(row, name)
    try:
        timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
    except ValueError:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} is malformed") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise RobinhoodCryptoClientViolation(f"Robinhood Crypto {name} must include a timezone")
    return timestamp_value


__all__ = [
    "parse_account",
    "parse_estimate",
    "parse_holding",
    "parse_order",
    "parse_quote",
    "parse_trading_pair",
    "result_rows",
]
