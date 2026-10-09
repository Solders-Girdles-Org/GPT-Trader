"""Unit tests for the pure order-record mapping helpers.

These exercise the logic extracted from TradingEngine into
``engines/order_record_mapping.py`` directly, independent of the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.features.live_trade.engines.order_record_mapping import (
    build_record_from_broker_order,
    get_order_field,
    merge_metadata,
    normalize_persisted_status,
    parse_decimal,
    parse_decimal_optional,
    parse_timestamp,
)
from gpt_trader.persistence.orders_store import OrderStatus


@dataclass
class _Order:
    order_id: str | None = None
    side: str | None = None


class TestGetOrderField:
    def test_returns_first_present_key_from_dict(self) -> None:
        assert get_order_field({"id": "x", "order_id": "y"}, "order_id", "id") == "y"

    def test_skips_none_values(self) -> None:
        assert get_order_field({"order_id": None, "id": "y"}, "order_id", "id") == "y"

    def test_reads_object_attributes(self) -> None:
        assert get_order_field(_Order(order_id="abc"), "order_id", "id") == "abc"

    def test_missing_returns_none(self) -> None:
        assert get_order_field({"foo": 1}, "order_id", "id") is None
        assert get_order_field(_Order(), "order_id") is None


class TestNormalizePersistedStatus:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("open", OrderStatus.OPEN),
            ("submitted", OrderStatus.OPEN),
            ("partially_filled", OrderStatus.PARTIALLY_FILLED),
            ("filled", OrderStatus.FILLED),
            ("canceled", OrderStatus.CANCELLED),
            ("cancelled", OrderStatus.CANCELLED),
            ("rejected", OrderStatus.REJECTED),
            ("expired", OrderStatus.EXPIRED),
            ("failed", OrderStatus.FAILED),
        ],
    )
    def test_known_statuses(self, raw: str, expected: OrderStatus) -> None:
        assert normalize_persisted_status(raw) is expected

    def test_unwraps_enum_like_value(self) -> None:
        assert normalize_persisted_status(OrderStatus.FILLED) is OrderStatus.FILLED

    def test_unknown_defaults_to_open(self) -> None:
        assert normalize_persisted_status("who-knows") is OrderStatus.OPEN


class TestParseDecimal:
    def test_valid_value(self) -> None:
        assert parse_decimal("1.5", Decimal("0")) == Decimal("1.5")

    def test_none_returns_default(self) -> None:
        assert parse_decimal(None, Decimal("7")) == Decimal("7")

    def test_invalid_returns_default(self) -> None:
        assert parse_decimal("not-a-number", Decimal("3")) == Decimal("3")

    def test_decimal_passthrough(self) -> None:
        value = Decimal("2.25")
        assert parse_decimal(value, Decimal("0")) is value

    def test_optional_none_and_invalid_return_none(self) -> None:
        assert parse_decimal_optional(None) is None
        assert parse_decimal_optional("bad") is None
        assert parse_decimal_optional("4") == Decimal("4")

    def test_non_finite_returns_default(self) -> None:
        # NaN/Infinity must never be persisted as an order quantity/price.
        assert parse_decimal(Decimal("NaN"), Decimal("0")) == Decimal("0")
        assert parse_decimal(Decimal("Infinity"), Decimal("0")) == Decimal("0")
        assert parse_decimal("nan", Decimal("0")) == Decimal("0")
        assert parse_decimal("inf", Decimal("0")) == Decimal("0")

    def test_optional_non_finite_returns_none(self) -> None:
        assert parse_decimal_optional(Decimal("NaN")) is None
        assert parse_decimal_optional(Decimal("-Infinity")) is None
        assert parse_decimal_optional("inf") is None


class TestMergeMetadata:
    def test_both_none_returns_none(self) -> None:
        assert merge_metadata(None, None) is None

    def test_base_only(self) -> None:
        assert merge_metadata({"a": 1}, None) == {"a": 1}

    def test_update_wins(self) -> None:
        assert merge_metadata({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


class TestParseTimestamp:
    def test_numeric(self) -> None:
        assert parse_timestamp(123) == 123.0

    def test_datetime(self) -> None:
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        assert parse_timestamp(dt) == dt.timestamp()

    def test_iso_string(self) -> None:
        assert parse_timestamp("2026-01-01T00:00:00+00:00") == pytest.approx(
            datetime(2026, 1, 1, tzinfo=UTC).timestamp()
        )

    def test_none_and_invalid_fall_back_to_now(self) -> None:
        assert parse_timestamp(None) > 0
        assert parse_timestamp("definitely-not-a-date") > 0

    def test_non_finite_falls_back_to_now(self) -> None:
        # inf/nan would raise in datetime.fromtimestamp; fall back instead.
        assert parse_timestamp(float("inf")) > 0
        assert parse_timestamp(float("-inf")) > 0
        assert parse_timestamp(float("nan")) > 0


class TestBuildRecordFromBrokerOrder:
    def test_builds_full_record(self) -> None:
        now = datetime(2026, 6, 28, tzinfo=UTC)
        order = {
            "order_id": "o1",
            "client_order_id": "c1",
            "product_id": "BTC-USD",
            "side": "BUY",
            "order_type": "LIMIT",
            "size": "2",
            "price": "100.5",
            "filled_size": "1",
            "average_filled_price": "100.0",
            "status": "open",
            "created_time": "2026-06-01T00:00:00+00:00",
            "tif": "GTC",
        }

        record = build_record_from_broker_order(order, bot_id="bot-x", now=now)

        assert record is not None
        assert record.order_id == "o1"
        assert record.client_order_id == "c1"
        assert record.symbol == "BTC-USD"
        assert record.side == "buy"
        assert record.order_type == "limit"
        assert record.quantity == Decimal("2")
        assert record.price == Decimal("100.5")
        assert record.filled_quantity == Decimal("1")
        assert record.average_fill_price == Decimal("100.0")
        assert record.status is OrderStatus.OPEN
        assert record.updated_at == now
        assert record.bot_id == "bot-x"
        assert record.metadata == {"source": "order_reconciliation", "raw_status": "open"}

    def test_client_order_id_falls_back_to_order_id(self) -> None:
        record = build_record_from_broker_order(
            {"order_id": "o2", "status": "filled"},
            bot_id="b",
            now=datetime.now(UTC),
        )
        assert record is not None
        assert record.client_order_id == "o2"

    def test_missing_order_id_returns_none(self) -> None:
        assert (
            build_record_from_broker_order(
                {"status": "open"}, bot_id="b", now=datetime.now(UTC)
            )
            is None
        )
