"""Deterministic rule channels an experiment may select, at default settings.

``baseline`` is today's moving-average crossover. ``mean-reversion`` is the
existing ``MeanReversionStrategy`` through the snapshot-strategy wrapper, with
a fresh strategy and cooldown per decision so replay stays a pure function of
the recorded bars. Neither is tuned here. Exits come from the structured plan
each rule emits (stop, target, expiry); mean reversion's own z-score exit is a
live-engine behaviour the snapshot contract does not carry.
"""

from __future__ import annotations

from typing import Any

from gpt_trader.app.config.bot_config import MeanReversionConfig
from gpt_trader.features.live_trade.strategies.mean_reversion.strategy import (
    CooldownState,
    MeanReversionStrategy,
)
from gpt_trader.features.strategy_tools.snapshot_proposer import SnapshotStrategyProposer
from gpt_trader.features.trade_ideas.baseline import BaselineProposer
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot

# Bars each decision sees, ending at the decision bar. Baseline needs 53
# (50-bar average plus its 3-bar crossover lookback); mean reversion's 20-bar
# z-score fits inside, and one shared window keeps the channels comparable.
HISTORY_BARS = 53


def _mean_reversion() -> MeanReversionStrategy:
    return MeanReversionStrategy(MeanReversionConfig(), CooldownState())


def propose(benchmark: str, snapshot: MarketSnapshot) -> list[Any]:
    if benchmark == "baseline":
        return list(BaselineProposer().propose(snapshot))
    if benchmark == "mean-reversion":
        proposer = SnapshotStrategyProposer(
            _mean_reversion, strategy_name="mean-reversion", drive="final-bar"
        )
        return proposer.propose(snapshot)
    raise ValueError(f"Unknown benchmark: {benchmark}")
