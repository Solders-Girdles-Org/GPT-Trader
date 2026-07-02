"""
Core package for shared types and protocols.

This package contains domain primitives that are used across all feature slices.
It has ZERO dependencies on feature slices - only stdlib.
"""

# Trading enums
# Account types
from gpt_trader.core.account import (
    Balance,
    Order,
    Position,
)

# Errors
from gpt_trader.core.errors import (
    AuthError,
    BrokerageError,
    InsufficientFunds,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

# Market data types
from gpt_trader.core.market import (
    Candle,
    Product,
    Quote,
)

# Broker protocols
from gpt_trader.core.protocols import (
    BrokerProtocol,
    ExtendedBrokerProtocol,
    MarketDataProtocol,
    TickerFreshnessProvider,
    TickerFreshnessProviderSource,
)

# Strategy types
from gpt_trader.core.strategy import (
    Action,
    Decision,
)
from gpt_trader.core.trading import (
    MarketType,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

__all__ = [
    # Trading enums
    "OrderSide",
    "OrderType",
    "TimeInForce",
    "MarketType",
    "OrderStatus",
    # Market data
    "Candle",
    "Quote",
    "Product",
    # Account
    "Order",
    "Position",
    "Balance",
    # Strategy
    "Action",
    "Decision",
    # Broker protocols
    "BrokerProtocol",
    "ExtendedBrokerProtocol",
    "MarketDataProtocol",
    "TickerFreshnessProvider",
    "TickerFreshnessProviderSource",
    # Errors
    "InvalidRequestError",
    "InsufficientFunds",
    "NotFoundError",
    "AuthError",
    "BrokerageError",
    "RateLimitError",
    "PermissionDeniedError",
]
