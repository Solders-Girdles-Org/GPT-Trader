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
from gpt_trader.errors import ValidationError
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


# Both channels quantize plan levels to the proposers' default 0.01 precision
# (a modelling assumption). Baseline drops a signal whose levels collapse; the
# mean-reversion adapter raises instead, which is recorded as a refused signal
# rather than aborting the run.
PRECISION_REFUSAL = "signal refused: plan levels not representable at the modelled price precision"


def propose(benchmark: str, snapshot: MarketSnapshot) -> tuple[list[Any], str | None]:
    """Return the channel's ideas, or none plus the reason a signal was refused."""
    if benchmark == "baseline":
        return list(BaselineProposer().propose(snapshot)), None
    if benchmark == "mean-reversion":
        proposer = SnapshotStrategyProposer(
            _mean_reversion, strategy_name="mean-reversion", drive="final-bar"
        )
        try:
            return proposer.propose(snapshot), None
        except ValidationError as error:
            if error.context.get("field") != "price_precision":
                raise
            return [], PRECISION_REFUSAL
    raise ValueError(f"Unknown benchmark: {benchmark}")
