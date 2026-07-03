"""
Price tick persistence and state recovery.

Owned by the recorder slice so recording state can be constructed by the
composition root and shared beyond the trading engine:
- Persist price ticks to EventStore for crash recovery
- Rehydrate price history from persisted events on startup
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.app.protocols import EventStoreProtocol

logger = get_logger(__name__, component="price_tick_store")

# Event type for price ticks
EVENT_PRICE_TICK = "price_tick"

# Maximum number of prices to retain per symbol
MAX_PRICE_HISTORY = 20


class PriceTickStore:
    """
    Manages price tick persistence and recovery.

    Responsibilities:
    - Record price ticks to EventStore for crash recovery
    - Rehydrate price history from EventStore on startup
    - Maintain bounded price history per symbol
    """

    def __init__(
        self,
        event_store: EventStoreProtocol | None,
        symbols: list[str],
        bot_id: str,
    ) -> None:
        """
        Initialize the price tick store.

        Args:
            event_store: EventStore for persistence (can be None)
            symbols: List of symbols to track
            bot_id: Bot identifier for event tagging
        """
        self._event_store = event_store
        self._symbols = set(symbols)
        self._bot_id = bot_id
        self._price_history: dict[str, deque[Decimal]] = defaultdict(
            lambda: deque(maxlen=MAX_PRICE_HISTORY)
        )

    @property
    def price_history(self) -> dict[str, deque[Decimal]]:
        """Access the price history dictionary."""
        return self._price_history

    def rehydrate(self, strategy_rehydrate_callback: Any = None) -> int:
        """
        Restore price history from persisted events.

        Args:
            strategy_rehydrate_callback: Optional callback for strategy-specific
                rehydration (receives list of events)

        Returns:
            Number of price ticks restored
        """
        if self._event_store is None:
            logger.debug("No event store configured - skipping rehydration")
            return 0

        get_recent_by_type = getattr(type(self._event_store), "get_recent_by_type", None)
        if callable(get_recent_by_type):
            events = self._event_store.get_recent_by_type(EVENT_PRICE_TICK, count=1000)
        else:
            events = self._event_store.get_recent(count=1000)
        restored = 0

        for event in events:
            if event.get("type") != EVENT_PRICE_TICK:
                continue

            data = event.get("data", {})
            symbol = data.get("symbol")
            price_str = data.get("price")

            if not symbol or not price_str:
                continue

            # Only restore prices for symbols we're trading
            if symbol not in self._symbols:
                continue

            try:
                price = Decimal(str(price_str))
                # Create deque with maxlen if first price for symbol
                if symbol not in self._price_history:
                    self._price_history[symbol] = deque(maxlen=MAX_PRICE_HISTORY)
                self._price_history[symbol].append(price)
                restored += 1
            except (ValueError, ArithmeticError) as e:
                logger.warning(f"Failed to parse price from event: {e}")

        if restored > 0:
            logger.info(f"Rehydrated {restored} price ticks from EventStore")
            for symbol, prices in self._price_history.items():
                logger.info(f"  {symbol}: {len(prices)} prices")

        # Call strategy rehydration if callback provided
        if strategy_rehydrate_callback is not None:
            strategy_rehydrate_callback(events)

        return restored

    def record_price_tick(self, symbol: str, price: Decimal) -> None:
        """
        Persist price tick to EventStore for crash recovery.

        Also updates the in-memory price history.

        Args:
            symbol: Trading symbol
            price: Current price
        """
        # Update in-memory history (deque auto-discards oldest when full)
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=MAX_PRICE_HISTORY)
        self._price_history[symbol].append(price)

        # Persist to event store
        if self._event_store is None:
            return

        self._event_store.store(
            {
                "type": EVENT_PRICE_TICK,
                "data": {
                    "symbol": symbol,
                    "price": str(price),
                    "timestamp": time.time(),
                    "bot_id": self._bot_id,
                },
            }
        )

    async def record_price_tick_async(self, symbol: str, price: Decimal) -> None:
        """Persist price tick, offloading the SQLite write to a thread.

        Updates in-memory price history immediately, then offloads the
        blocking EventStore.store() call via asyncio.to_thread to avoid
        stalling the async trading loop.
        """
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=MAX_PRICE_HISTORY)
        self._price_history[symbol].append(price)

        if self._event_store is None:
            return

        event = {
            "type": EVENT_PRICE_TICK,
            "data": {
                "symbol": symbol,
                "price": str(price),
                "timestamp": time.time(),
                "bot_id": self._bot_id,
            },
        }
        await asyncio.to_thread(self._event_store.store, event)
