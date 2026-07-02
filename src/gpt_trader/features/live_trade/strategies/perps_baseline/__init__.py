"""Transitional re-export shim; the package was renamed to ``strategies.baseline``.

Renamed 2026-07-02: the baseline strategy is spot-first ("perps" was INTX-era
naming; see docs/decisions/intx-default-derivatives-venue.md). All in-repo
imports use ``gpt_trader.features.live_trade.strategies.baseline``; this shim
exists only for not-yet-migrated callers and will be removed once none remain.
The ``strategy_type: "perps_baseline"`` registry/config value is unchanged.
"""

from __future__ import annotations

from gpt_trader.features.live_trade.strategies.baseline import (
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
