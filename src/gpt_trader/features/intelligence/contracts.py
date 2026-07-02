"""
Stable interfaces and shared types for intelligence integrations.

This module centralizes small contracts used across intelligence modules to
avoid ad-hoc imports from other slices.
"""

from gpt_trader.core import Product
from gpt_trader.features.live_trade.interfaces import TradingStrategy
from gpt_trader.features.live_trade.strategies.base import MarketDataContext
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision

__all__ = [
    "Action",
    "Decision",
    "MarketDataContext",
    "Product",
    "TradingStrategy",
]
