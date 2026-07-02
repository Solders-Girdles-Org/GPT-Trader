"""
Protocol definitions for broker abstractions.

These protocols define the expected interfaces for broker implementations,
enabling structural typing across different broker backends (live, mock, simulated).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from gpt_trader.core import (
        Balance,
        Candle,
        Order,
        Position,
        Product,
        Quote,
    )


@runtime_checkable
class BrokerProtocol(Protocol):
    """
    Core protocol for broker implementations.

    Defines the minimal interface required for trading operations.
    Implemented by: CoinbaseRestService, DeterministicBroker.
    Partially implemented by: SimulatedBroker (backtesting only, missing place_order/cancel_order).
    """

    def get_product(self, symbol: str) -> Product | None:
        """Get product metadata for a symbol."""
        ...

    def get_quote(self, symbol: str) -> Quote | None:
        """Get current quote for a symbol."""
        ...

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        """Get ticker data for a product."""
        ...

    def list_positions(self) -> list[Position]:
        """List all current positions."""
        ...

    def list_balances(self) -> list[Balance]:
        """List all account balances."""
        ...

    def place_order(
        self,
        symbol: str,
        side: Any = None,
        order_type: Any = None,
        quantity: Decimal | None = None,
        **kwargs: Any,
    ) -> Order:
        """Place a trading order."""
        ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order."""
        ...

    def get_candles(self, symbol: str, **kwargs: Any) -> list[Candle]:
        """Get historical candle data."""
        ...


@runtime_checkable
class ExtendedBrokerProtocol(BrokerProtocol, Protocol):
    """
    Extended broker protocol with additional methods.

    Used by components that require mark price tracking and position risk info.
    """

    def get_mark_price(self, symbol: str) -> Decimal | None:
        """Get current mark price for a symbol."""
        ...

    def get_market_snapshot(self, symbol: str) -> dict[str, Any]:
        """Get a snapshot of market data for a symbol."""
        ...

    def get_position_pnl(self, symbol: str) -> dict[str, Decimal]:
        """Get PnL data for a position."""
        ...

    def get_position_risk(self, symbol: str) -> dict[str, Any]:
        """Get risk metrics for a position."""
        ...


@runtime_checkable
class MarketDataProtocol(Protocol):
    """
    Protocol for market data streaming.

    Used by components that need to start/stop market data subscriptions.
    """

    def start_market_data(self, symbols: list[str]) -> None:
        """Start streaming market data for symbols."""
        ...

    def stop_market_data(self) -> None:
        """Stop streaming market data."""
        ...

    def is_connected(self) -> bool:
        """Check if market data connection is active."""
        ...

    def is_stale(self, symbol: str, threshold_seconds: int = 10) -> bool:
        """Check if market data for a symbol is stale."""
        ...


@runtime_checkable
class TickerFreshnessProvider(Protocol):
    """
    Protocol for checking ticker freshness.

    Implemented by ticker caches or services that can report staleness for a symbol.
    """

    def is_stale(self, symbol: str) -> bool:
        """Return True when ticker data for the symbol is stale or missing."""
        ...


@runtime_checkable
class TickerFreshnessProviderSource(Protocol):
    """Protocol for exposing a ticker freshness provider."""

    def get_ticker_freshness_provider(self) -> TickerFreshnessProvider | None:
        """Return a ticker freshness provider if available."""
        ...


__all__ = [
    "BrokerProtocol",
    "ExtendedBrokerProtocol",
    "MarketDataProtocol",
    "TickerFreshnessProvider",
    "TickerFreshnessProviderSource",
]
