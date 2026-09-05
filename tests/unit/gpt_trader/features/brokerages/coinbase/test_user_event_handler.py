"""Tests for Coinbase user-event handler integration."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

from gpt_trader.features.brokerages.coinbase.user_event_handler import (
    CoinbaseUserEventHandler,
)
from gpt_trader.features.brokerages.coinbase.ws_events import FillEvent, OrderUpdateEvent
from gpt_trader.persistence.orders_store import OrderRecord, OrdersStore, OrderStatus


def _make_handler(
    tmp_path,
    *,
    rest_service: object | None = None,
    dedupe_limit: int = 1000,
) -> tuple[CoinbaseUserEventHandler, OrdersStore]:
    store = OrdersStore(tmp_path)
    store.initialize()
    handler = CoinbaseUserEventHandler(
        broker=None,
        orders_store=store,
        event_store=None,
        bot_id="test-bot",
        market_data_service=None,
        symbols=["BTC-USD"],
        rest_service=rest_service,
        dedupe_limit=dedupe_limit,
    )
    return handler, store


def test_order_update_upserts_orders_store(tmp_path) -> None:
    handler, store = _make_handler(tmp_path)
    now = datetime.now(timezone.utc)
    event = OrderUpdateEvent(
        order_id="order-123",
        client_order_id="client-123",
        product_id="BTC-USD",
        status="OPEN",
        side="BUY",
        order_type="LIMIT",
        size=Decimal("1.5"),
        filled_size=Decimal("0"),
        price=Decimal("45000"),
        avg_price=None,
        timestamp=now,
    )

    handler.handle_order_update(event)

    record = store.get_order("order-123")
    assert record is not None
    assert record.client_order_id == "client-123"
    assert record.status == OrderStatus.OPEN
    assert record.symbol == "BTC-USD"


def test_fill_event_idempotent(tmp_path) -> None:
    handler, store = _make_handler(tmp_path)
    now = datetime.now(timezone.utc)
    order = OrderRecord(
        order_id="order-456",
        client_order_id="client-456",
        symbol="BTC-USD",
        side="buy",
        order_type="limit",
        quantity=Decimal("2"),
        price=Decimal("45000"),
        status=OrderStatus.OPEN,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        created_at=now,
        updated_at=now,
        bot_id="test-bot",
        time_in_force="GTC",
        metadata=None,
    )
    store.save_order(order)

    event = FillEvent(
        order_id="order-456",
        client_order_id="client-456",
        product_id="BTC-USD",
        side="BUY",
        fill_price=Decimal("45010"),
        fill_size=Decimal("1"),
        fee=Decimal("0"),
        commission=Decimal("0"),
        sequence=123,
        timestamp=now,
    )

    handler._process_fill_for_pnl = Mock()
    handler.handle_fill(event)
    handler.handle_fill(event)

    record = store.get_order("order-456")
    assert record is not None
    assert record.filled_quantity == Decimal("1")
    assert record.status == OrderStatus.PARTIALLY_FILLED
    handler._process_fill_for_pnl.assert_not_called()
    assert "identified_fills_missing:order-456" in store.accounting.projections()["BTC-USD"].reasons


def test_backfill_deduplicates_rest_fills(tmp_path) -> None:
    rest_service = Mock()
    rest_service.list_orders.return_value = []
    rest_service.list_fills.return_value = [
        {
            "fill_id": "fill-789",
            "trade_time": "2026-09-01T12:00:00+00:00",
            "order_id": "order-789",
            "client_order_id": "client-789",
            "product_id": "BTC-USD",
            "side": "BUY",
            "price": "50000",
            "size": "1",
        },
        {
            "fill_id": "fill-789",
            "trade_time": "2026-09-01T12:00:00+00:00",
            "order_id": "order-789",
            "client_order_id": "client-789",
            "product_id": "BTC-USD",
            "side": "BUY",
            "price": "50000",
            "size": "1",
        },
    ]

    handler, store = _make_handler(tmp_path, rest_service=rest_service)
    handler._process_fill_for_pnl = Mock()

    handler.request_backfill(reason="sequence_gap")

    # No order receipt was supplied: retain the identified fact without inventing an order size/status.
    assert store.get_order("order-789") is None
    projection = store.accounting.projections()["BTC-USD"]
    assert projection.fill_count == 1
    assert projection.realized_pnl is None
    handler._process_fill_for_pnl.assert_not_called()


def test_dedupe_limit_evicts_oldest_key(tmp_path) -> None:
    handler, _ = _make_handler(tmp_path, dedupe_limit=2)

    assert handler._record_fill_key("fill-1") is True
    assert handler._record_fill_key("fill-2") is True
    assert handler._record_fill_key("fill-3") is True

    assert handler._record_fill_key("fill-1") is True
