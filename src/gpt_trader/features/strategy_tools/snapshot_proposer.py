"""Run existing live-trade strategies as proposers over recorded snapshots.

First strategy→proposer convergence step of the accepted five-role composition
decision (#1164): wraps a strategy's pure ``decide(...)`` surface into the
``Proposer`` snapshot contract, so the same intelligence that drives the live
engine can be replayed and scored over recorder-produced ``MarketSnapshot``
artifacts instead of growing a second proposer brain.

The wrapper derives every ``decide`` input from the snapshot alone: the mark
window is the series' candle closes, the current mark is the last close, the
book is flat (``position_state=None``), and equity is zero because account
state is not part of the snapshot contract — executable sizing comes from the
offline ``TradeIdeaPositionSizingBridge`` instead. The strategy is injected as
a factory and constructed fresh per symbol series, so no strategy state can
bleed across symbols or snapshots and identical snapshots yield identical
ideas. Strategies are injected structurally (:class:`SnapshotDecider`), which
keeps this slice free of ``features.live_trade`` imports; composition roots
own strategy construction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from gpt_trader.errors import ValidationError
from gpt_trader.features.strategy_tools.trade_idea_adapter import (
    StrategyDecisionSignal,
    StrategySignalContext,
    StrategySignalToTradeIdeaAdapter,
    StrategySignalToTradeIdeaAdapterConfig,
)
from gpt_trader.features.trade_ideas import (
    MarketSnapshot,
    SymbolSeries,
    TradeIdea,
    TradeIdeaPositionSizingBridge,
)

SNAPSHOT_STRATEGY_PROPOSER_PREFIX = "snapshot-strategy"


@runtime_checkable
class SnapshotDecider(Protocol):
    """Structural slice of the live ``TradingStrategy`` surface the wrapper drives.

    Mirrors ``features.live_trade.interfaces.TradingStrategy.decide`` without
    importing the live-trade slice. Only strategies whose ``decide`` is a pure
    function of these arguments are replay-safe here; strategies that mutate
    internal state per call need fresh-state-per-snapshot handling first.
    """

    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Any,
        market_data: Any = None,
        candles: Sequence[Any] | None = None,
    ) -> StrategyDecisionSignal: ...


StrategyFactory = Callable[[], SnapshotDecider]


class SnapshotStrategyProposer:
    """Adapt an injected strategy factory onto the ``Proposer`` contract."""

    def __init__(
        self,
        strategy_factory: StrategyFactory,
        *,
        strategy_name: str,
        adapter: StrategySignalToTradeIdeaAdapter | None = None,
    ) -> None:
        if not strategy_name.strip():
            raise ValidationError("strategy_name must be non-empty", field="strategy_name")
        self._strategy_factory = strategy_factory
        self._strategy_name = strategy_name
        self._adapter = adapter or StrategySignalToTradeIdeaAdapter(
            StrategySignalToTradeIdeaAdapterConfig(
                enabled=True,
                proposer_id_prefix=SNAPSHOT_STRATEGY_PROPOSER_PREFIX,
            ),
            sizing_bridge=TradeIdeaPositionSizingBridge(),
        )

    @property
    def proposer_id(self) -> str:
        return self._adapter.actor_id(self._strategy_name)

    def propose(self, snapshot: MarketSnapshot) -> list[TradeIdea]:
        ideas: list[TradeIdea] = []
        for series in snapshot.series:
            idea = self._propose_for_series(snapshot, series)
            if idea is not None:
                ideas.append(idea)
        return ideas

    def _propose_for_series(
        self, snapshot: MarketSnapshot, series: SymbolSeries
    ) -> TradeIdea | None:
        if not series.candles:
            return None
        closes = [candle.close for candle in series.candles]
        strategy = self._strategy_factory()
        decision = strategy.decide(
            symbol=series.symbol,
            current_mark=closes[-1],
            position_state=None,
            recent_marks=closes,
            equity=Decimal("0"),
            product=None,
            candles=series.candles,
        )
        context = StrategySignalContext(
            symbol=series.symbol,
            current_mark=closes[-1],
            as_of=_utc_aware(snapshot.as_of),
            strategy_name=self._strategy_name,
            data_source=f"{snapshot.source}:{series.granularity}",
        )
        return self._adapter.map_decision(decision, context)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


__all__ = [
    "SNAPSHOT_STRATEGY_PROPOSER_PREFIX",
    "SnapshotDecider",
    "SnapshotStrategyProposer",
    "StrategyFactory",
]
