"""MarketDataRecorder: standalone polling loop behavior."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from gpt_trader.features.recorder import (
    EVENT_PRICE_TICK,
    MarketDataRecorder,
    MarketDataRecorderConfig,
    PriceTickStore,
    derive_recorder_bot_id,
)


class FakeEventStore:
    def __init__(self) -> None:
        self.stored: list[dict[str, Any]] = []

    def store(self, event: dict[str, Any]) -> None:
        self.stored.append(event)


class BatchBroker:
    def __init__(self, tickers: dict[str, dict[str, Any]]) -> None:
        self.tickers = tickers
        self.batch_calls = 0
        self.single_calls: list[str] = []

    def get_tickers(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        self.batch_calls += 1
        return self.tickers

    def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        self.single_calls.append(symbol)
        return self.tickers.get(symbol)


class SingleOnlyBroker:
    def __init__(self, tickers: dict[str, dict[str, Any]]) -> None:
        self.tickers = tickers
        self.single_calls: list[str] = []

    def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        self.single_calls.append(symbol)
        return self.tickers.get(symbol)


def _recorder(
    broker: Any,
    symbols: tuple[str, ...] = ("BTC-USD", "ETH-USD"),
    interval_seconds: float = 0.01,
) -> tuple[MarketDataRecorder, PriceTickStore, FakeEventStore]:
    event_store = FakeEventStore()
    tick_store = PriceTickStore(
        event_store=event_store,
        symbols=list(symbols),
        bot_id="recorder:test",
    )
    recorder = MarketDataRecorder(
        broker=broker,
        tick_store=tick_store,
        config=MarketDataRecorderConfig(
            symbols=symbols,
            interval_seconds=interval_seconds,
        ),
    )
    return recorder, tick_store, event_store


@pytest.mark.asyncio
async def test_record_once_uses_batch_tickers_and_persists() -> None:
    broker = BatchBroker(
        {
            "BTC-USD": {"price": "50000.5"},
            "ETH-USD": {"price": "2500"},
        }
    )
    recorder, tick_store, event_store = _recorder(broker)

    recorded = await recorder.record_once()

    assert recorded == 2
    assert broker.batch_calls == 1
    assert broker.single_calls == []
    assert list(tick_store.price_history["BTC-USD"]) == [Decimal("50000.5")]
    assert [event["type"] for event in event_store.stored] == [EVENT_PRICE_TICK] * 2
    assert {event["data"]["bot_id"] for event in event_store.stored} == {"recorder:test"}


@pytest.mark.asyncio
async def test_record_once_falls_back_to_single_ticker() -> None:
    broker = SingleOnlyBroker({"BTC-USD": {"price": "50000"}, "ETH-USD": {"price": "2500"}})
    recorder, _tick_store, event_store = _recorder(broker)

    recorded = await recorder.record_once()

    assert recorded == 2
    assert broker.single_calls == ["BTC-USD", "ETH-USD"]
    assert len(event_store.stored) == 2


@pytest.mark.asyncio
async def test_record_once_skips_missing_and_invalid_prices() -> None:
    broker = BatchBroker(
        {
            "BTC-USD": {"price": "not-a-price"},
            "ETH-USD": {"price": "0"},
        }
    )
    recorder, tick_store, event_store = _recorder(broker)

    recorded = await recorder.record_once()

    assert recorded == 0
    assert event_store.stored == []
    assert tick_store.price_history == {}


@pytest.mark.asyncio
async def test_record_once_survives_broker_errors_per_symbol() -> None:
    class FlakyBroker:
        def get_ticker(self, symbol: str) -> dict[str, Any]:
            if symbol == "BTC-USD":
                raise RuntimeError("ticker down")
            return {"price": "2500"}

    recorder, _tick_store, event_store = _recorder(FlakyBroker())

    recorded = await recorder.record_once()

    assert recorded == 1
    assert event_store.stored[0]["data"]["symbol"] == "ETH-USD"


@pytest.mark.asyncio
async def test_run_polls_until_stopped_and_survives_poll_failures() -> None:
    polls = 0

    class CountingBroker:
        def get_tickers(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
            nonlocal polls
            polls += 1
            if polls == 1:
                raise RuntimeError("transient outage")
            return {symbol: {"price": "100"} for symbol in symbols}

    recorder, _tick_store, event_store = _recorder(CountingBroker(), interval_seconds=0.01)

    async def _stop_after_ticks() -> None:
        while not event_store.stored:
            await asyncio.sleep(0.005)
        recorder.stop()

    await asyncio.wait_for(
        asyncio.gather(recorder.run(), _stop_after_ticks()),
        timeout=5.0,
    )

    assert polls >= 2  # first poll failed, later polls recorded
    assert event_store.stored
    assert recorder.running is False


def test_recorder_config_validates_inputs() -> None:
    with pytest.raises(ValueError, match="at least one symbol"):
        MarketDataRecorderConfig(symbols=(), interval_seconds=1.0)
    with pytest.raises(ValueError, match="interval must be positive"):
        MarketDataRecorderConfig(symbols=("BTC-USD",), interval_seconds=0)


def test_derive_recorder_bot_id_handles_enum_and_str_profiles() -> None:
    class FakeProfile:
        value = "prod"

    assert derive_recorder_bot_id(FakeProfile()) == "recorder:prod"
    assert derive_recorder_bot_id("Dev") == "recorder:dev"
    assert derive_recorder_bot_id(None) == "recorder:default"
    assert derive_recorder_bot_id("") == "recorder:default"
