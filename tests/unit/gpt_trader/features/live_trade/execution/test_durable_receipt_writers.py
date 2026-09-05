"""Existing WS/REST order writers preserve admitted intent and failure honesty."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from gpt_trader.features.brokerages.coinbase.user_event_handler import CoinbaseUserEventHandler
from gpt_trader.features.brokerages.coinbase.ws_events import FillEvent, OrderUpdateEvent
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.persistence.durability import WriteError, WriteResult
from gpt_trader.persistence.orders_store import OrderStatus
from tests.unit.gpt_trader.features.live_trade.execution.test_durable_order_intents import (
    request,
    submitter,
)


def pending(tmp_path):
    broker = DeterministicBroker()
    broker.place_order = Mock(side_effect=SystemExit("acknowledgment lost"))
    worker = submitter(tmp_path, broker)
    with pytest.raises(SystemExit):
        request(worker)
    handler = CoinbaseUserEventHandler(
        broker=None,
        orders_store=worker.orders_store,
        event_store=None,
        bot_id="test",
        market_data_service=None,
        symbols=["BTC-USD"],
    )
    handler._process_fill_for_pnl = Mock()
    handler._emit_event = Mock()
    return worker, handler


def fill():
    return FillEvent(
        "broker-close",
        "stable-close",
        "BTC-USD",
        "SELL",
        Decimal("60000"),
        Decimal("0.05"),
        Decimal("0"),
        Decimal("0"),
        123,
        datetime.now(timezone.utc),
    )


def test_ws_order_update_retains_intent_and_blocks_changed_retry(tmp_path):
    worker, handler = pending(tmp_path)
    original = worker.orders_store.get_order_by_client_order_id("stable-close")
    handler.handle_order_update(
        OrderUpdateEvent(
            "broker-close",
            "stable-close",
            "BTC-USD",
            "OPEN",
            "SELL",
            "MARKET",
            Decimal("0.125"),
            Decimal("0"),
            None,
            None,
            datetime.now(timezone.utc),
        )
    )
    updated = worker.orders_store.get_order_by_client_order_id("stable-close")
    assert updated.metadata["intent"] == original.metadata["intent"]
    assert updated.metadata["record_checksum_version"] == 2 and updated.checksum_is_valid()
    assert updated.created_at == original.created_at
    assert request(submitter(tmp_path, DeterministicBroker()), reduce_only=False).rejected


@pytest.mark.parametrize("failure", ["sqlite", "failed_result"])
def test_failed_ws_fill_never_updates_pnl_and_identical_retry_can_persist(
    tmp_path, monkeypatch, failure
):
    worker, handler = pending(tmp_path)
    store = worker.orders_store
    event = fill()
    original = store.upsert_by_client_id
    if failure == "sqlite":
        store._get_connection().execute("PRAGMA query_only=ON")
    else:
        monkeypatch.setattr(
            store, "upsert_by_client_id", Mock(return_value=WriteResult.fail("unavailable"))
        )
    with pytest.raises(WriteError):
        handler.handle_fill(event)
    assert store.get_order_by_client_order_id("stable-close").filled_quantity == 0
    handler._process_fill_for_pnl.assert_not_called()
    handler._emit_event.assert_not_called()
    store._get_connection().execute("PRAGMA query_only=OFF")
    monkeypatch.setattr(store, "upsert_by_client_id", original)
    handler.handle_fill(event)
    handler.handle_fill(event)
    updated = store.get_order_by_client_order_id("stable-close")
    assert updated.filled_quantity == Decimal("0.05") and updated.checksum_is_valid()
    assert updated.metadata["intent"]["reduce_only"] is True
    handler._process_fill_for_pnl.assert_called_once()


def test_rest_order_refresh_preserves_intent_and_refuses_conflicting_observation(tmp_path):
    from dataclasses import replace

    worker, handler = pending(tmp_path)
    broker = DeterministicBroker()
    order = broker.place_order(
        symbol="BTC-USD",
        side="sell",
        order_type="market",
        quantity=Decimal("0.125"),
        client_id="stable-close",
    )
    assert handler._upsert_order_from_rest(order)
    stored = worker.orders_store.get_order_by_client_order_id("stable-close")
    assert stored.status is OrderStatus.FILLED and stored.metadata["intent"]["reduce_only"] is True
    assert not handler._upsert_order_from_rest(replace(order, symbol="ETH-USD"))
    assert worker.orders_store.get_order_by_client_order_id("stable-close") == stored


def test_rest_fill_write_failure_does_not_consume_dedupe_or_advance_watermark(tmp_path):
    worker, handler = pending(tmp_path)
    event = fill()
    payload = {
        "fill_id": "rest-fill-1",
        "order_id": event.order_id,
        "client_order_id": event.client_order_id,
        "product_id": event.product_id,
        "side": event.side,
        "price": str(event.fill_price),
        "size": str(event.fill_size),
        "trade_time": event.timestamp.isoformat(),
    }
    store = worker.orders_store
    store._get_connection().execute("PRAGMA query_only=ON")
    with pytest.raises(WriteError):
        handler._apply_rest_fill(payload)
    assert handler._fill_watermark is None
    handler._process_fill_for_pnl.assert_not_called()
    store._get_connection().execute("PRAGMA query_only=OFF")
    assert handler._apply_rest_fill(payload)[0]
    assert not handler._apply_rest_fill(payload)[0]
    assert store.get_order_by_client_order_id("stable-close").filled_quantity == Decimal("0.05")
    handler._process_fill_for_pnl.assert_called_once()
