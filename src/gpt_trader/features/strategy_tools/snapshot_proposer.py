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

Two drive modes cover the strategy library (#1164 stage 3). ``"final-bar"``
calls ``decide`` once over the full mark window — correct for strategies whose
decision is a pure function of that window (the baseline family; mean
reversion once its cooldown is caller-owned and inert on a flat book).
``"per-candle"`` replays ``decide`` over every candle prefix and keeps only
the final decision, so strategies that accumulate per-call state (the regime
switcher's ``MarketRegimeDetector``) warm that state from recorded closes
alone; the fresh instance per series makes the replay deterministic, and
discarded warm-up decisions can never become ideas.

The lane is long-only and spot-only today: non-buy decisions (including a
perps strategy's short entries) are dropped by the adapter, and a non-spot
``product_type`` fails closed through the adapter's spot-only validation
rather than recording a futures signal as a spot idea — the same fail-closed
stance the live proposal gate takes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, get_args, runtime_checkable

from gpt_trader.errors import ValidationError
from gpt_trader.features.strategy_tools.trade_idea_adapter import (
    StrategyDecisionSignal,
    StrategySignalContext,
    StrategySignalToTradeIdeaAdapter,
    StrategySignalToTradeIdeaAdapterConfig,
)
from gpt_trader.features.trade_ideas import (
    MarketSnapshot,
    ProductType,
    SymbolSeries,
    TradeIdea,
    TradeIdeaPositionSizingBridge,
)

SNAPSHOT_STRATEGY_PROPOSER_PREFIX = "snapshot-strategy"

# How the wrapper feeds a strategy's decide() from a symbol series: one call
# over the full mark window, or one call per candle prefix (only the final
# decision can become an idea). Per-candle exists for strategies whose decide
# accumulates state per call, so that state is fed from recorded closes alone.
SnapshotDecideDrive = Literal["final-bar", "per-candle"]


@runtime_checkable
class SnapshotDecider(Protocol):
    """Structural slice of the live ``TradingStrategy`` surface the wrapper drives.

    Mirrors ``features.live_trade.interfaces.TradingStrategy.decide`` without
    importing the live-trade slice. Strategies whose ``decide`` is a pure
    function of these arguments are replay-safe under the ``"final-bar"``
    drive; strategies that accumulate internal state per call need the
    ``"per-candle"`` drive, which rebuilds that state from the snapshot's own
    closes on a fresh instance.
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
        product_type: ProductType = ProductType.SPOT,
        adapter: StrategySignalToTradeIdeaAdapter | None = None,
        drive: SnapshotDecideDrive = "final-bar",
    ) -> None:
        if not strategy_name.strip():
            raise ValidationError("strategy_name must be non-empty", field="strategy_name")
        if drive not in get_args(SnapshotDecideDrive):
            raise ValidationError(
                f"drive must be one of {get_args(SnapshotDecideDrive)}", field="drive"
            )
        self._strategy_factory = strategy_factory
        self._strategy_name = strategy_name.strip()
        self._product_type = product_type
        self._drive: SnapshotDecideDrive = drive
        if adapter is None:
            adapter = StrategySignalToTradeIdeaAdapter(
                StrategySignalToTradeIdeaAdapterConfig(
                    enabled=True,
                    proposer_id_prefix=SNAPSHOT_STRATEGY_PROPOSER_PREFIX,
                ),
                sizing_bridge=TradeIdeaPositionSizingBridge(),
            )
        self._adapter = adapter

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
        if self._drive == "per-candle":
            # Warm per-call strategy state from the recorded closes; every
            # decision before the final bar is warm-up only and is discarded.
            for end in range(1, len(series.candles) + 1):
                decision = strategy.decide(
                    symbol=series.symbol,
                    current_mark=closes[end - 1],
                    position_state=None,
                    recent_marks=closes[:end],
                    equity=Decimal("0"),
                    product=None,
                    candles=series.candles[:end],
                )
        else:
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
            product_type=self._product_type,
        )
        return self._adapter.map_decision(decision, context)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


__all__ = [
    "SNAPSHOT_STRATEGY_PROPOSER_PREFIX",
    "SnapshotDecideDrive",
    "SnapshotDecider",
    "SnapshotStrategyProposer",
    "StrategyFactory",
]
