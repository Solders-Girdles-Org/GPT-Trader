from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.core import OrderStatus
from gpt_trader.persistence.orders_store import OrderRecord
from gpt_trader.persistence.orders_store import OrderStatus as PersistedOrderStatus


@pytest.mark.asyncio
async def test_audit_orders_normalizes_submit_id_to_broker_order_id(engine, monkeypatch) -> None:
    async def direct_call(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(engine, "_broker_calls", direct_call)
    engine._cycle_count = 1
    engine.context.risk_manager.config.unfilled_order_alert_seconds = 0

    submit_id = "live_123"
    broker_order_id = "ORDER-1"
    engine._open_orders[:] = [submit_id]

    orders_store = MagicMock()
    orders_store.get_pending_orders.return_value = [
        OrderRecord(
            order_id=submit_id,
            client_order_id=submit_id,
            symbol="BTC-USD",
            side="buy",
            order_type="market",
            quantity=Decimal("0.01"),
            price=None,
            status=PersistedOrderStatus.PENDING,
            filled_quantity=Decimal("0"),
            average_fill_price=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            bot_id="live",
        )
    ]
    engine._orders_store = orders_store

    engine.context.broker.list_orders.return_value = {
        "orders": [
            {
                "order_id": broker_order_id,
                "client_order_id": submit_id,
                "product_id": "BTC-USD",
                "side": "BUY",
                "status": "OPEN",
                "created_time": "2024-01-01T00:00:00Z",
            }
        ]
    }

    await engine._audit_orders()

    assert engine._open_orders == [broker_order_id]
    orders_store.upsert_by_client_id.assert_called()
    updated = orders_store.upsert_by_client_id.call_args[0][0]
    assert updated.order_id == broker_order_id
    assert updated.client_order_id == submit_id
    assert updated.status == PersistedOrderStatus.OPEN


@pytest.mark.asyncio
async def test_audit_orders_recovers_unknown_bot_owned_orders(engine, monkeypatch) -> None:
    async def direct_call(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(engine, "_broker_calls", direct_call)
    engine._cycle_count = 1
    engine.context.risk_manager.config.unfilled_order_alert_seconds = 0

    orders_store = MagicMock()
    orders_store.get_pending_orders.return_value = []
    engine._orders_store = orders_store
    engine._open_orders[:] = []

    ghost_client_id = "live_ghost"
    ghost_order_id = "ORDER-GHOST"
    engine.context.broker.list_orders.return_value = {
        "orders": [
            {
                "order_id": ghost_order_id,
                "client_order_id": ghost_client_id,
                "product_id": "BTC-USD",
                "side": "BUY",
                "status": "OPEN",
                "created_time": "2024-01-01T00:00:00Z",
            }
        ]
    }
    engine.context.broker.cancel_order.return_value = True

    await engine._audit_orders()
    assert engine._open_orders == [ghost_order_id]
    orders_store.upsert_by_client_id.assert_called()
    engine.context.broker.cancel_order.assert_not_called()
    engine.context.risk_manager.set_reduce_only_mode.assert_not_called()


@pytest.mark.asyncio
async def test_audit_orders_escalates_without_orders_store(engine, monkeypatch) -> None:
    async def direct_call(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(engine, "_broker_calls", direct_call)
    engine._cycle_count = 1
    engine.context.risk_manager.config.unfilled_order_alert_seconds = 0

    engine._orders_store = None
    engine._open_orders[:] = []

    ghost_client_id = "live_ghost"
    ghost_order_id = "ORDER-GHOST"
    engine.context.broker.list_orders.return_value = {
        "orders": [
            {
                "order_id": ghost_order_id,
                "client_order_id": ghost_client_id,
                "product_id": "BTC-USD",
                "side": "BUY",
                "status": "OPEN",
                "created_time": "2024-01-01T00:00:00Z",
            }
        ]
    }
    engine.context.broker.cancel_order.return_value = True

    await engine._audit_orders()
    await engine._audit_orders()
    assert engine.context.broker.cancel_order.call_count == 0

    await engine._audit_orders()
    engine.context.risk_manager.set_reduce_only_mode.assert_called()
    engine.context.broker.cancel_order.assert_called_once_with(ghost_order_id)
    assert engine._degradation.get_status()["global_paused"] is True


@pytest.mark.asyncio
async def test_audit_orders_refreshes_missing_persisted_order(engine, monkeypatch) -> None:
    async def direct_call(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(engine, "_broker_calls", direct_call)
    engine._cycle_count = 1
    engine.context.risk_manager.config.unfilled_order_alert_seconds = 0

    record = OrderRecord(
        order_id="order-missing",
        client_order_id="client-missing",
        symbol="BTC-USD",
        side="buy",
        order_type="market",
        quantity=Decimal("0.01"),
        price=None,
        status=PersistedOrderStatus.PENDING,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        bot_id="live",
    )
    orders_store = MagicMock()
    orders_store.get_pending_orders.return_value = [record]
    engine._orders_store = orders_store
    engine._open_orders[:] = [
        "order-missing",
        "client-missing",
        "order-filled",
        "client-filled",
    ]

    engine.context.broker.list_orders.return_value = {"orders": []}
    refreshed_order = MagicMock()
    refreshed_order.order_id = None
    refreshed_order.client_order_id = None
    refreshed_order.id = "order-filled"
    refreshed_order.client_id = "client-filled"
    refreshed_order.symbol = "BTC-USD"
    refreshed_order.side = "BUY"
    refreshed_order.type = "MARKET"
    refreshed_order.quantity = Decimal("0.01")
    refreshed_order.price = Decimal("50000")
    refreshed_order.status = OrderStatus.FILLED
    refreshed_order.filled_quantity = Decimal("0.01")
    refreshed_order.avg_fill_price = Decimal("50000")
    refreshed_order.submitted_at = datetime.now(UTC)
    refreshed_order.updated_at = datetime.now(UTC)
    engine.context.broker.get_order.return_value = refreshed_order

    await engine._audit_orders()

    orders_store.upsert_by_client_id.assert_called()
    updated = orders_store.upsert_by_client_id.call_args[0][0]
    assert updated.status == PersistedOrderStatus.FILLED
    assert "order-missing" not in engine._open_orders
    assert "client-missing" not in engine._open_orders
    assert "order-filled" not in engine._open_orders
    assert "client-filled" not in engine._open_orders


@pytest.mark.asyncio
async def test_audit_orders_reports_refreshed_open_order_snapshot(engine, monkeypatch) -> None:
    async def direct_call(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(engine, "_broker_calls", direct_call)
    engine._cycle_count = 1
    engine.context.risk_manager.config.unfilled_order_alert_seconds = 1

    record = OrderRecord(
        order_id="order-missing",
        client_order_id="client-missing",
        symbol="BTC-USD",
        side="buy",
        order_type="market",
        quantity=Decimal("0.01"),
        price=None,
        status=PersistedOrderStatus.PENDING,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        bot_id="live",
    )
    orders_store = MagicMock()
    orders_store.get_pending_orders.return_value = [record]
    engine._orders_store = orders_store
    engine._open_orders[:] = ["order-missing"]
    engine._status_reporter.update_orders = MagicMock()

    refreshed_order = {
        "order_id": "order-open",
        "client_order_id": "client-missing",
        "product_id": "BTC-USD",
        "side": "BUY",
        "status": "OPEN",
        "created_time": time.time() - 10,
        "size": "0.01",
        "price": "50000",
    }
    engine.context.broker.list_orders.return_value = {"orders": []}
    engine.context.broker.get_order.return_value = refreshed_order

    await engine._audit_orders()

    alert_events = [
        e for e in engine._event_store.list_events() if e.get("type") == "unfilled_order_alert"
    ]
    assert len(alert_events) == 1
    assert alert_events[0]["data"]["order_id"] == "order-open"
    engine._status_reporter.update_orders.assert_called_once_with([refreshed_order])
