"""Transitional re-export shim; the canonical module is ``strategies.baseline.strategy``.

Preserves the pre-rename deep import path
(``...strategies.perps_baseline.strategy``) for not-yet-migrated callers.
See the package ``__init__`` for the rename rationale.
"""

from __future__ import annotations

from gpt_trader.features.live_trade.strategies.baseline.strategy import (
    Action,
    BaselinePerpsStrategy,
    BaseStrategyConfig,
    Decision,
    IndicatorState,
    PerpsStrategy,
    PerpsStrategyConfig,
    SpotStrategy,
    SpotStrategyConfig,
)

__all__ = [
    "Action",
    "BaselinePerpsStrategy",
    "BaseStrategyConfig",
    "Decision",
    "IndicatorState",
    "PerpsStrategy",
    "PerpsStrategyConfig",
    "SpotStrategy",
    "SpotStrategyConfig",
]
