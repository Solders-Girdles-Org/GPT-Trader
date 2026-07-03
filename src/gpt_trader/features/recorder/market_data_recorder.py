"""Standalone market-data recording loop (REST ticker polling).

The recorder role from the five-role composition: observation that runs
regardless of execution state. The loop polls read-only tickers from an
injected broker and records price ticks through ``PriceTickStore``, so tick
history keeps accumulating while the trading engine is halted or paused.

Cadence is injected — nothing here decides a recurrence beyond sleeping the
configured interval between polls, per the frequency-headroom directive.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Any

from gpt_trader.features.recorder.price_tick_store import PriceTickStore
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="market_data_recorder")


def derive_recorder_bot_id(profile: Any) -> str:
    """Stable identity stamped into recorder-written events.

    Ticks written by the standalone recorder carry a real identity (unlike
    the engine flow's historical empty ``bot_id``) so recorded data is
    attributable. Rehydration stays deliberately bot_id-agnostic: the store
    filters by symbol only, so engine restarts pick up recorder-collected
    history and vice versa.
    """
    profile_name = profile.value if hasattr(profile, "value") else str(profile or "")
    profile_name = profile_name.strip().lower() or "default"
    return f"recorder:{profile_name}"


@dataclass(frozen=True)
class MarketDataRecorderConfig:
    """One recording run: which symbols to poll and how often."""

    symbols: tuple[str, ...]
    interval_seconds: float

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("MarketDataRecorder requires at least one symbol")
        if self.interval_seconds <= 0:
            raise ValueError("MarketDataRecorder interval must be positive")


class MarketDataRecorder:
    """Poll tickers on an injected cadence and persist price ticks.

    Read-only by construction: the only broker methods used are
    ``get_tickers``/``get_ticker``, and the only write path is the tick
    store's EventStore persistence.
    """

    def __init__(
        self,
        *,
        broker: Any,
        tick_store: PriceTickStore,
        config: MarketDataRecorderConfig,
    ) -> None:
        self._broker = broker
        self._tick_store = tick_store
        self._config = config
        self._running = False
        self._stop_event: asyncio.Event | None = None

    @property
    def running(self) -> bool:
        return self._running

    def stop(self) -> None:
        """Request a graceful stop; safe to call from a signal handler."""
        self._running = False
        stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()

    async def run(self) -> None:
        """Poll until stopped. Poll failures are logged and do not end the run."""
        self._running = True
        self._stop_event = asyncio.Event()
        logger.info(
            "Market-data recording started",
            symbols=list(self._config.symbols),
            interval_seconds=self._config.interval_seconds,
            operation="record",
            stage="start",
        )
        try:
            while self._running:
                try:
                    await self.record_once()
                except Exception:
                    logger.exception("Recording poll failed", operation="record")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.interval_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            self._running = False
            self._stop_event = None
            logger.info(
                "Market-data recording stopped",
                operation="record",
                stage="stop",
            )

    async def record_once(self) -> int:
        """Poll each configured symbol once; returns the number of ticks recorded."""
        tickers = await self._fetch_batch_tickers()
        recorded = 0
        for symbol in self._config.symbols:
            ticker = tickers.get(symbol)
            if ticker is None:
                ticker = await self._fetch_single_ticker(symbol)
            price = _extract_price(ticker)
            if price is None:
                logger.warning(
                    "No usable ticker price; skipping symbol this poll",
                    symbol=symbol,
                    operation="record",
                )
                continue
            await self._tick_store.record_price_tick_async(symbol, price)
            recorded += 1
        return recorded

    async def _fetch_batch_tickers(self) -> dict[str, dict[str, Any]]:
        get_tickers = getattr(self._broker, "get_tickers", None)
        if not callable(get_tickers):
            return {}
        try:
            result = await asyncio.to_thread(get_tickers, list(self._config.symbols))
        except Exception as exc:
            logger.warning(
                "Batch ticker poll failed; falling back to per-symbol fetch",
                error=str(exc),
                operation="record",
            )
            return {}
        return result if isinstance(result, dict) else {}

    async def _fetch_single_ticker(self, symbol: str) -> dict[str, Any] | None:
        try:
            ticker = await asyncio.to_thread(self._broker.get_ticker, symbol)
        except Exception as exc:
            logger.warning(
                "Ticker poll failed",
                symbol=symbol,
                error=str(exc),
                operation="record",
            )
            return None
        return ticker if isinstance(ticker, dict) else None


def _extract_price(ticker: dict[str, Any] | None) -> Decimal | None:
    if not ticker:
        return None
    raw_price = ticker.get("price")
    if raw_price is None:
        return None
    try:
        price = Decimal(str(raw_price))
    except (DecimalException, ValueError):
        return None
    return price if price > 0 else None
