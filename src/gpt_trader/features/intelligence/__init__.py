"""
Intelligence feature slice for market regime detection and position sizing.

This module provides:
- Market regime detection (trend/volatility classification)
- Regime-aware position sizing

Usage:
    from gpt_trader.features.intelligence import (
        MarketRegimeDetector,
        PositionSizer,
        RegimeType,
    )
"""

from gpt_trader.features.intelligence.regime import (
    MarketRegimeDetector,
    RegimeConfig,
    RegimeState,
    RegimeType,
)
from gpt_trader.features.intelligence.sizing import (
    PositionSizer,
    PositionSizingConfig,
    SizingResult,
)

__all__ = [
    # Regime detection
    "MarketRegimeDetector",
    "RegimeConfig",
    "RegimeState",
    "RegimeType",
    # Position sizing
    "PositionSizer",
    "PositionSizingConfig",
    "SizingResult",
]
