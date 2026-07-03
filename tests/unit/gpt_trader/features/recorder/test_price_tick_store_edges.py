from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.features.recorder import (
    EVENT_PRICE_TICK,
    MAX_PRICE_HISTORY,
    PriceTickStore,
)


def test_rehydrate_without_event_store_returns_zero() -> None:
    store = PriceTickStore(event_store=None, symbols=["BTC-USD"], bot_id="bot-1")
    callback = MagicMock()

    restored = store.rehydrate(strategy_rehydrate_callback=callback)

    assert restored == 0
    callback.assert_not_called()


def test_rehydrate_skips_non_ticks_and_invalid_events() -> None:
    event_store = MagicMock()
    event_store.get_recent.return_value = [
        {"type": "other", "data": {"symbol": "BTC-USD", "price": "1"}},
        {"type": EVENT_PRICE_TICK, "data": {"price": "1"}},
        {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-USD"}},
        {"type": EVENT_PRICE_TICK, "data": {"symbol": "ETH-USD", "price": "2"}},
        {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-USD", "price": "1"}},
    ]
    store = PriceTickStore(event_store=event_store, symbols=["BTC-USD"], bot_id="bot-1")

    restored = store.rehydrate()

    assert restored == 1
    assert list(store.price_history["BTC-USD"]) == [Decimal("1")]


def test_rehydrate_is_bot_id_agnostic_across_mixed_writers() -> None:
    # Deliberate semantics (#1158): the store filters by symbol only, so an
    # engine restart picks up ticks the standalone recorder collected while
    # execution was halted — including legacy events with an empty bot_id.
    event_store = MagicMock()
    event_store.get_recent.return_value = [
        {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-USD", "price": "1", "bot_id": ""}},
        {
            "type": EVENT_PRICE_TICK,
            "data": {"symbol": "BTC-USD", "price": "2", "bot_id": "recorder:dev"},
        },
        {
            "type": EVENT_PRICE_TICK,
            "data": {"symbol": "BTC-USD", "price": "3", "bot_id": "some-engine"},
        },
    ]
    store = PriceTickStore(event_store=event_store, symbols=["BTC-USD"], bot_id="some-engine")

    restored = store.rehydrate()

    assert restored == 3
    assert list(store.price_history["BTC-USD"]) == [Decimal("1"), Decimal("2"), Decimal("3")]


def test_rehydrate_skips_invalid_price_values() -> None:
    event_store = MagicMock()
    event_store.get_recent.return_value = [
        {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-USD", "price": "bad"}},
        {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-USD", "price": "1"}},
    ]
    store = PriceTickStore(event_store=event_store, symbols=["BTC-USD"], bot_id="bot-1")

    restored = store.rehydrate()

    assert restored == 1
    assert list(store.price_history["BTC-USD"]) == [Decimal("1")]


def test_rehydrate_calls_strategy_callback_even_with_no_restores() -> None:
    event_store = MagicMock()
    event_store.get_recent.return_value = [
        {"type": "other", "data": {"symbol": "BTC-USD", "price": "1"}}
    ]
    store = PriceTickStore(event_store=event_store, symbols=["BTC-USD"], bot_id="bot-1")
    callback = MagicMock()

    restored = store.rehydrate(strategy_rehydrate_callback=callback)

    assert restored == 0
    callback.assert_called_once_with(event_store.get_recent.return_value)


def test_record_price_tick_persists_and_updates_history(monkeypatch) -> None:
    event_store = MagicMock()
    store = PriceTickStore(event_store=event_store, symbols=["BTC-USD"], bot_id="bot-1")
    monkeypatch.setattr("gpt_trader.features.recorder.price_tick_store.time.time", lambda: 123.0)

    store.record_price_tick("BTC-USD", Decimal("10"))

    assert list(store.price_history["BTC-USD"]) == [Decimal("10")]
    event_store.store.assert_called_once()
    payload = event_store.store.call_args.args[0]
    assert payload["type"] == EVENT_PRICE_TICK
    assert payload["data"]["symbol"] == "BTC-USD"
    assert payload["data"]["price"] == "10"
    assert payload["data"]["timestamp"] == 123.0
    assert payload["data"]["bot_id"] == "bot-1"


def test_record_price_tick_without_event_store_updates_history() -> None:
    store = PriceTickStore(event_store=None, symbols=["BTC-USD"], bot_id="bot-1")

    store.record_price_tick("BTC-USD", Decimal("10"))

    assert list(store.price_history["BTC-USD"]) == [Decimal("10")]


def test_record_price_tick_enforces_max_history() -> None:
    store = PriceTickStore(event_store=None, symbols=["BTC-USD"], bot_id="bot-1")

    for i in range(MAX_PRICE_HISTORY + 5):
        store.record_price_tick("BTC-USD", Decimal(str(i)))

    assert len(store.price_history["BTC-USD"]) == MAX_PRICE_HISTORY
    assert list(store.price_history["BTC-USD"])[-1] == Decimal(str(MAX_PRICE_HISTORY + 4))


@pytest.mark.asyncio
async def test_record_price_tick_async_offloads_store_to_thread(monkeypatch) -> None:
    event_store = MagicMock()
    store = PriceTickStore(event_store=event_store, symbols=["BTC-USD"], bot_id="bot-1")
    monkeypatch.setattr("gpt_trader.features.recorder.price_tick_store.time.time", lambda: 456.0)

    await store.record_price_tick_async("BTC-USD", Decimal("42000"))

    assert list(store.price_history["BTC-USD"]) == [Decimal("42000")]
    event_store.store.assert_called_once()
    payload = event_store.store.call_args.args[0]
    assert payload["type"] == EVENT_PRICE_TICK
    assert payload["data"]["symbol"] == "BTC-USD"
    assert payload["data"]["price"] == "42000"
    assert payload["data"]["timestamp"] == 456.0
    assert payload["data"]["bot_id"] == "bot-1"


@pytest.mark.asyncio
async def test_record_price_tick_async_without_event_store() -> None:
    store = PriceTickStore(event_store=None, symbols=["BTC-USD"], bot_id="bot-1")

    await store.record_price_tick_async("BTC-USD", Decimal("42000"))

    assert list(store.price_history["BTC-USD"]) == [Decimal("42000")]
