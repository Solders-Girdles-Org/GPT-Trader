"""Base strategy protocol and abstract types.

This module defines the interface that all trading strategies must implement.

Includes:
- StrategyProtocol: Core interface for trading decisions
- RehydratableStrategy: Protocol for crash recovery
- StatefulStrategy: Protocol for strategies with O(1) incremental updates
- BaseStrategy: Abstract base class for stateless strategies
- StatefulStrategyBase: Abstract base class for stateful strategies
- MarketDataContext: Optional enhanced market data for advanced strategies
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from gpt_trader.core import Product

if TYPE_CHECKING:
    from gpt_trader.features.brokerages.coinbase.market_data_features import DepthSnapshot

    from .baseline.strategy import Decision


@dataclass(slots=True)
class MarketDataContext:
    """Optional enhanced market data for advanced strategies.

    Contains order book depth and trade flow statistics that strategies
    can use for more sophisticated decision-making. All fields are optional
    to support graceful degradation when data is unavailable.

    Attributes:
        orderbook_snapshot: Latest order book depth with bid/ask levels
        trade_volume_stats: Rolling trade statistics (VWAP, avg_size, aggressor_ratio)
        spread_bps: Current bid-ask spread in basis points
    """

    orderbook_snapshot: "DepthSnapshot | None" = field(default=None)
    """Latest order book snapshot with bid/ask depth."""

    trade_volume_stats: dict[str, Any] | None = field(default=None)
    """Rolling trade statistics: vwap, avg_size, aggressor_ratio, etc."""

    spread_bps: Decimal | None = field(default=None)
    """Current bid-ask spread in basis points."""

    @property
    def has_orderbook(self) -> bool:
        """Check if orderbook data is available."""
        return self.orderbook_snapshot is not None

    @property
    def has_trade_stats(self) -> bool:
        """Check if trade statistics are available."""
        return self.trade_volume_stats is not None


@runtime_checkable
class StrategyProtocol(Protocol):
    """Protocol defining the interface for all trading strategies.

    Any class implementing this protocol can be used as a trading strategy
    in the system. The protocol ensures strategies provide the core `decide`
    method that generates trading decisions.
    """

    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Product | None,
        market_data: MarketDataContext | None = None,
    ) -> "Decision":
        """Generate a trading decision based on market data.

        Args:
            symbol: Trading pair symbol (e.g., "BTC-USD")
            current_mark: Current mark/spot price
            position_state: Current position info, or None if no position
            recent_marks: Historical prices (oldest first)
            equity: Account equity for position sizing
            product: Product specification from exchange
            market_data: Optional enhanced market data (orderbook depth, trade flow)

        Returns:
            Decision with action, reason, confidence, and indicator state
        """
        ...


@runtime_checkable
class RehydratableStrategy(Protocol):
    """Protocol for strategies that support state recovery.

    Strategies implementing this protocol can restore their internal state
    from persisted events after a restart.
    """

    def rehydrate(self, events: Sequence[dict[str, Any]]) -> int:
        """Restore strategy state from historical events.

        Args:
            events: List of persisted events (oldest first)

        Returns:
            Number of events processed
        """
        ...


class BaseStrategy(ABC):
    """Abstract base class for trading strategies.

    Provides a template for implementing trading strategies with
    common structure. Subclasses must implement the `decide` method.
    """

    @abstractmethod
    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Product | None,
        market_data: MarketDataContext | None = None,
    ) -> "Decision":
        """Generate a trading decision based on market data."""
        ...

    def rehydrate(self, events: Sequence[dict[str, Any]]) -> int:
        """Restore strategy state from historical events.

        Default implementation does nothing. Override in subclasses
        that need state recovery.

        Args:
            events: List of persisted events (oldest first)

        Returns:
            Number of events processed
        """
        return 0


@runtime_checkable
class StatefulStrategy(Protocol):
    """Protocol for strategies with O(1) incremental state updates.

    Stateful strategies maintain internal state (indicator values, statistics)
    that updates incrementally with each price tick, rather than recalculating
    from the full price history each cycle.

    Benefits:
    - O(1) per-tick updates instead of O(n) recalculation
    - Reduced memory usage (no need to store full history)
    - Better crash recovery through state serialization
    """

    def update(self, symbol: str, price: Decimal) -> None:
        """Update internal state with new price. O(1) operation.

        Args:
            symbol: Trading pair symbol
            price: Latest mark/spot price
        """
        ...

    def serialize_state(self) -> dict[str, Any]:
        """Serialize all indicator state for persistence.

        Returns:
            Dictionary of serializable state that can be stored
            and later passed to deserialize_state().
        """
        ...

    def deserialize_state(self, state: dict[str, Any]) -> None:
        """Restore indicator state from serialized data.

        Args:
            state: Previously serialized state from serialize_state()
        """
        ...


class StatefulStrategyBase(BaseStrategy):
    """Abstract base class for stateful trading strategies.

    Extends BaseStrategy with incremental state management using
    O(1) algorithms like Welford's for running statistics.

    Subclasses should:
    1. Initialize indicator bundles in __init__
    2. Implement update() to feed prices to indicators
    3. Implement serialize_state() and deserialize_state() for crash recovery
    4. Use indicator values in decide() instead of recalculating
    """

    def update(self, symbol: str, price: Decimal) -> None:
        """Update internal state with new price.

        Default implementation does nothing. Override to feed prices
        to stateful indicators.

        Args:
            symbol: Trading pair symbol
            price: Latest mark/spot price
        """
        pass

    def serialize_state(self) -> dict[str, Any]:
        """Serialize all indicator state for persistence.

        Default returns empty dict. Override to persist indicator state.

        Returns:
            Dictionary of serializable indicator state
        """
        return {}

    def deserialize_state(self, state: dict[str, Any]) -> None:
        """Restore indicator state from serialized data.

        Default does nothing. Override to restore indicator state.

        Args:
            state: Previously serialized state
        """
        pass

    def rehydrate(self, events: Sequence[dict[str, Any]]) -> int:
        """Restore strategy state from historical events.

        For stateful strategies, this:
        1. Looks for a state snapshot event (most recent)
        2. Deserializes the snapshot if found
        3. Processes any price events after the snapshot

        Args:
            events: List of persisted events (oldest first)

        Returns:
            Number of events processed
        """
        processed = 0

        # Look for most recent state snapshot (iterate backwards)
        for event in reversed(events):
            if event.get("type") == "strategy_state_snapshot":
                state_data = event.get("data", {}).get("state", {})
                if state_data:
                    self.deserialize_state(state_data)
                    processed += 1
                    break

        # Process price events after state snapshot
        for event in events:
            if event.get("type") == "price_tick":
                data = event.get("data", {})
                symbol = data.get("symbol")
                price_str = data.get("price")
                if symbol and price_str:
                    self.update(symbol, Decimal(price_str))
                    processed += 1

        return processed


__all__ = [
    "BaseStrategy",
    "MarketDataContext",
    "RehydratableStrategy",
    "StatefulStrategy",
    "StatefulStrategyBase",
    "StrategyProtocol",
]
