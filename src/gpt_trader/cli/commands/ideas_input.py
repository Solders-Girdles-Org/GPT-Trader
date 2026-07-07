"""Operator input parsing for the trade-idea CLI.

JSON payload / fixture parsing and validation for the ``gpt-trader ideas``
commands: loads trade ideas, market snapshots, and replay candle fixtures into
domain objects, raising InputPayloadError subclasses on malformed input. The
command handlers in ideas.py delegate to these parsers.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from gpt_trader.core import Candle
from gpt_trader.features.trade_ideas import (
    MarketSnapshot,
    SnapshotIntegrityError,
    SymbolSeries,
    TradeIdea,
)


class InputPayloadError(ValueError):
    """Raised when a JSON CLI payload cannot be parsed."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class IdeaInputError(InputPayloadError):
    """Raised when a JSON trade-idea payload cannot be parsed."""


class SnapshotInputError(InputPayloadError):
    """Raised when a JSON market snapshot payload cannot be parsed."""


class CandleInputError(InputPayloadError):
    """Raised when a replay candle fixture cannot be parsed."""


class SnapshotBuildInputError(InputPayloadError):
    """Raised when snapshot build CLI input cannot be parsed."""


def _load_trade_idea(args: Namespace) -> TradeIdea:
    try:
        if getattr(args, "file", None):
            raw_payload = args.file.read_text(encoding="utf-8")
        else:
            raw_payload = sys.stdin.read()
    except OSError as error:
        raise IdeaInputError(f"Could not read trade idea input: {error}") from error

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as error:
        raise IdeaInputError(f"Malformed trade idea JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise IdeaInputError("Trade idea input must be a JSON object")

    try:
        return TradeIdea.from_dict(payload)
    except KeyError as error:
        field = str(error).strip("'")
        raise IdeaInputError(f"Missing required trade idea field: {field}", field=field) from error
    except (InvalidOperation, TypeError, ValueError) as error:
        raise IdeaInputError(f"Invalid trade idea field: {error}") from error


def _load_market_snapshot(path: Path) -> MarketSnapshot:
    try:
        raw_payload = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SnapshotInputError(f"Could not read market snapshot input: {error}") from error

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as error:
        raise SnapshotInputError(f"Malformed market snapshot JSON: {error.msg}") from error
    if not isinstance(payload, Mapping):
        raise SnapshotInputError("Market snapshot input must be a JSON object")

    try:
        return _market_snapshot_from_payload(payload)
    except SnapshotInputError:
        raise
    except SnapshotIntegrityError as error:
        context_field = _error_context(error).get("field")
        raise SnapshotInputError(
            f"Invalid market snapshot field: {error}",
            field=context_field if isinstance(context_field, str) else None,
        ) from error


def _market_snapshot_from_payload(payload: Mapping[str, Any]) -> MarketSnapshot:
    series_payloads = _required_payload_array(payload, "series", "series")
    return MarketSnapshot(
        as_of=_required_payload_datetime(payload, "as_of", "as_of"),
        source=_required_payload_string(payload, "source", "source"),
        series=tuple(
            _symbol_series_from_payload(series_payload, index)
            for index, series_payload in enumerate(series_payloads)
        ),
    )


def _symbol_series_from_payload(payload: Any, index: int) -> SymbolSeries:
    field_prefix = f"series[{index}]"
    series_payload = _payload_object(payload, field_prefix)
    candle_payloads = _required_payload_array(
        series_payload,
        "candles",
        f"{field_prefix}.candles",
    )
    return SymbolSeries(
        symbol=_required_payload_string(series_payload, "symbol", f"{field_prefix}.symbol"),
        granularity=_required_payload_string(
            series_payload,
            "granularity",
            f"{field_prefix}.granularity",
        ),
        candles=tuple(
            _candle_from_payload(candle_payload, f"{field_prefix}.candles[{candle_index}]")
            for candle_index, candle_payload in enumerate(candle_payloads)
        ),
    )


def _candle_from_payload(payload: Any, field_prefix: str) -> Candle:
    candle_payload = _payload_object(payload, field_prefix)
    return Candle(
        ts=_required_payload_datetime(candle_payload, "ts", f"{field_prefix}.ts"),
        open=_required_payload_decimal(candle_payload, "open", f"{field_prefix}.open"),
        high=_required_payload_decimal(candle_payload, "high", f"{field_prefix}.high"),
        low=_required_payload_decimal(candle_payload, "low", f"{field_prefix}.low"),
        close=_required_payload_decimal(candle_payload, "close", f"{field_prefix}.close"),
        volume=_required_payload_decimal(candle_payload, "volume", f"{field_prefix}.volume"),
    )


def _payload_object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotInputError(f"{field} must be a JSON object", field=field)
    return value


def _required_payload_value(payload: Mapping[str, Any], key: str, field: str) -> Any:
    try:
        return payload[key]
    except KeyError as error:
        raise SnapshotInputError(
            f"Missing required market snapshot field: {field}",
            field=field,
        ) from error


def _required_payload_array(
    payload: Mapping[str, Any],
    key: str,
    field: str,
) -> list[Any]:
    value = _required_payload_value(payload, key, field)
    if not isinstance(value, list):
        raise SnapshotInputError(f"{field} must be a JSON array", field=field)
    return value


def _required_payload_string(payload: Mapping[str, Any], key: str, field: str) -> str:
    value = _required_payload_value(payload, key, field)
    if not isinstance(value, str) or not value.strip():
        raise SnapshotInputError(f"{field} must be a non-empty string", field=field)
    return value


def _required_payload_decimal(payload: Mapping[str, Any], key: str, field: str) -> Decimal:
    value = _required_payload_value(payload, key, field)
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise SnapshotInputError(f"{field} must be a decimal value", field=field) from error
    if not parsed.is_finite():
        raise SnapshotInputError(f"{field} must be finite", field=field)
    return parsed


def _required_payload_datetime(payload: Mapping[str, Any], key: str, field: str) -> datetime:
    value = _required_payload_value(payload, key, field)
    if not isinstance(value, str):
        raise SnapshotInputError(f"{field} must be an ISO datetime string", field=field)
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise SnapshotInputError(f"{field} must be an ISO datetime string", field=field) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotInputError(f"{field} must include a timezone", field=field)
    return parsed


def _load_candle_fixture(
    path: Path,
    *,
    symbol: str | None = None,
    granularity: str | None = None,
) -> tuple[Candle, ...]:
    """Load replay candles from a bare fixture or a recorded market snapshot.

    Accepts either a JSON object with a top-level ``candles`` array or a
    recorded ``MarketSnapshot`` payload (``ideas snapshot build`` output and
    the cycle's persisted per-run snapshots), so replay can run over recorded
    windows without hand-built fixtures. Snapshot payloads need ``symbol`` to
    pick the series; ``granularity`` disambiguates multi-granularity series.
    """
    try:
        raw_payload = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CandleInputError(f"Could not read candle fixture: {error}") from error

    try:
        payload = json.loads(raw_payload, parse_float=Decimal, parse_int=Decimal)
    except json.JSONDecodeError as error:
        raise CandleInputError(f"Malformed candle fixture JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise CandleInputError("Candle fixture must be a JSON object", field="candles")

    if "series" in payload and "candles" not in payload:
        return _candles_from_snapshot_payload(payload, symbol=symbol, granularity=granularity)

    raw_candles = payload.get("candles")
    if not isinstance(raw_candles, list):
        raise CandleInputError("Candle fixture must contain a candles array", field="candles")

    return tuple(_parse_candle(row, index) for index, row in enumerate(raw_candles))


def _candles_from_snapshot_payload(
    payload: Mapping[str, Any],
    *,
    symbol: str | None,
    granularity: str | None,
) -> tuple[Candle, ...]:
    if symbol is None:
        raise CandleInputError(
            "A recorded snapshot payload needs a symbol to pick its candle series",
            field="symbol",
        )
    snapshot = _market_snapshot_from_payload(payload)
    matches = [series for series in snapshot.series if series.symbol == symbol]
    if granularity is not None and len(matches) > 1:
        matches = [series for series in matches if series.granularity == granularity]
    if not matches:
        available = ", ".join(f"{series.symbol}:{series.granularity}" for series in snapshot.series)
        raise CandleInputError(
            f"Recorded snapshot has no candle series for '{symbol}'"
            + (f" at granularity '{granularity}'" if granularity is not None else "")
            + (f" (available: {available})" if available else ""),
            field="symbol",
        )
    if len(matches) > 1:
        raise CandleInputError(
            f"Recorded snapshot has multiple candle series for '{symbol}'; "
            "pass a granularity to disambiguate",
            field="granularity",
        )
    return matches[0].candles


def _parse_candle(row: Any, index: int) -> Candle:
    if not isinstance(row, dict):
        raise CandleInputError(
            f"candle at index {index} must be a JSON object",
            field=f"candles[{index}]",
        )
    candle = Candle(
        ts=_parse_candle_timestamp(_required_candle_field(row, "ts", index), index),
        open=_parse_candle_decimal(_required_candle_field(row, "open", index), index, "open"),
        high=_parse_candle_decimal(_required_candle_field(row, "high", index), index, "high"),
        low=_parse_candle_decimal(_required_candle_field(row, "low", index), index, "low"),
        close=_parse_candle_decimal(_required_candle_field(row, "close", index), index, "close"),
        volume=_parse_candle_decimal(_required_candle_field(row, "volume", index), index, "volume"),
    )
    _validate_candle_semantics(candle, index)
    return candle


def _required_candle_field(row: dict[str, Any], field_name: str, index: int) -> Any:
    if field_name not in row:
        raise CandleInputError(
            f"candle at index {index} is missing required field '{field_name}'",
            field=f"candles[{index}].{field_name}",
        )
    return row[field_name]


def _parse_candle_timestamp(value: Any, index: int) -> datetime:
    field = f"candles[{index}].ts"
    if not isinstance(value, str):
        raise CandleInputError("candle ts must be an ISO-8601 string", field=field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CandleInputError(f"Invalid candle timestamp: {value}", field=field) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_candle_decimal(value: Any, index: int, field_name: str) -> Decimal:
    field = f"candles[{index}].{field_name}"
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise CandleInputError(
            f"Invalid decimal candle value for {field_name}: {value}",
            field=field,
        ) from error
    if not parsed.is_finite():
        raise CandleInputError(
            f"Candle decimal value must be finite for {field_name}: {value}",
            field=field,
        )
    return parsed


def _validate_candle_semantics(candle: Candle, index: int) -> None:
    for field_name in ("open", "high", "low", "close"):
        if getattr(candle, field_name) <= 0:
            raise CandleInputError(
                f"candle at index {index} has non-positive {field_name}",
                field=f"candles[{index}].{field_name}",
            )
    if candle.high < candle.low:
        raise CandleInputError(
            f"candle at index {index} has high below low",
            field=f"candles[{index}].high",
        )
    if not candle.low <= candle.open <= candle.high:
        raise CandleInputError(
            f"candle at index {index} has open outside low/high range",
            field=f"candles[{index}].open",
        )
    if not candle.low <= candle.close <= candle.high:
        raise CandleInputError(
            f"candle at index {index} has close outside low/high range",
            field=f"candles[{index}].close",
        )
    if candle.volume < 0:
        raise CandleInputError(
            f"candle at index {index} has negative volume",
            field=f"candles[{index}].volume",
        )


def _error_context(error: Exception) -> dict[str, Any]:
    context = getattr(error, "context", None)
    if isinstance(context, dict):
        return context
    return {}
