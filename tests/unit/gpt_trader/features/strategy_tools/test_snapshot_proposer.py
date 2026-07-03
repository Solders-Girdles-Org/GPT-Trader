from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from gpt_trader.core import Candle
from gpt_trader.features.live_trade.strategies.baseline import (
    Action,
    BaselinePerpsStrategy,
    Decision,
    SpotStrategy,
)
from gpt_trader.features.strategy_tools import (
    SnapshotDecider,
    SnapshotStrategyProposer,
)
from gpt_trader.features.trade_ideas import (
    MarketSnapshot,
    ProductType,
    Proposer,
    SymbolSeries,
    TradeDirection,
    evaluate_eligibility,
)

AS_OF = datetime(2026, 7, 3, 0, 0, tzinfo=UTC)

# Flat closes then a two-bar rise: the 5-bar average crosses above the 20-bar
# average within the crossover lookback and the trend turns bullish, clearing
# the baseline entry gate (crossover 0.4 + trend 0.3 >= min_confidence 0.5).
GOLDEN_CROSS = ["100"] * 28 + ["102", "104"]
# Mirror image: bearish crossover + bearish trend emits SELL on a flat book.
BEARISH_BREAK = ["100"] * 28 + ["98", "96"]


def make_series(
    closes: list[str],
    symbol: str = "BTC-USD",
    as_of: datetime = AS_OF,
) -> SymbolSeries:
    candles = tuple(
        Candle(
            ts=as_of - timedelta(days=len(closes) - index),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("1000"),
        )
        for index, close in enumerate(closes)
    )
    return SymbolSeries(symbol=symbol, granularity="1d", candles=candles)


def snapshot_of(*series: SymbolSeries, as_of: datetime = AS_OF) -> MarketSnapshot:
    return MarketSnapshot(as_of=as_of, source="coinbase:candles", series=series)


def spot_proposer() -> SnapshotStrategyProposer:
    return SnapshotStrategyProposer(SpotStrategy, strategy_name="baseline-spot")


class SpyStrategy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Any,
        equity: Decimal,
        product: Any,
        market_data: Any = None,
        candles: Any = None,
    ) -> Decision:
        self.calls.append(
            {
                "symbol": symbol,
                "current_mark": current_mark,
                "position_state": position_state,
                "recent_marks": recent_marks,
                "equity": equity,
                "product": product,
                "market_data": market_data,
                "candles": candles,
            }
        )
        return Decision(Action.HOLD, "spy", confidence=0.0, indicators={})


def test_satisfies_proposer_protocol() -> None:
    assert isinstance(spot_proposer(), Proposer)


def test_shipped_baseline_strategies_satisfy_snapshot_decider() -> None:
    assert isinstance(SpotStrategy(), SnapshotDecider)
    assert isinstance(BaselinePerpsStrategy(), SnapshotDecider)


def test_proposer_id_is_a_stable_property() -> None:
    assert spot_proposer().proposer_id == "snapshot-strategy-baseline-spot"


def test_buy_signal_maps_to_eligible_executable_long_idea() -> None:
    ideas = spot_proposer().propose(snapshot_of(make_series(GOLDEN_CROSS)))

    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.instrument == "BTC-USD"
    assert idea.direction is TradeDirection.LONG
    assert idea.product_type is ProductType.SPOT
    assert idea.decision_id.startswith("trade-20260703-baseline-spot-btc-usd-")
    assert idea.data_used[0].startswith("coinbase:candles:1d:BTC-USD:")
    assert idea.time_horizon.expires_at == AS_OF + timedelta(hours=48)
    assert evaluate_eligibility(idea) == []
    # Executor admission requires real sizing, not the advisory default.
    assert idea.sizing_recommendation.quantity is not None
    assert idea.sizing_recommendation.quantity > 0
    assert idea.sizing_recommendation.notional is not None
    assert idea.max_loss.amount is not None
    assert idea.max_loss.amount > 0
    assert any("engine=position-sizer-bridge-v1" in item for item in idea.data_used)


def test_identical_snapshots_yield_identical_ideas() -> None:
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))

    first = spot_proposer().propose(snapshot)
    second = spot_proposer().propose(snapshot)

    assert first == second
    assert [idea.record_hash() for idea in first] == [idea.record_hash() for idea in second]


def test_non_buy_decisions_produce_no_ideas() -> None:
    bearish = snapshot_of(make_series(BEARISH_BREAK))

    perps = SnapshotStrategyProposer(BaselinePerpsStrategy, strategy_name="baseline-perps")
    assert perps.propose(bearish) == []
    assert spot_proposer().propose(bearish) == []


def test_insufficient_history_produces_no_ideas() -> None:
    assert spot_proposer().propose(snapshot_of(make_series(["100"] * 10))) == []


def test_empty_series_is_skipped() -> None:
    empty = SymbolSeries(symbol="ETH-USD", granularity="1d", candles=())

    ideas = spot_proposer().propose(snapshot_of(make_series(GOLDEN_CROSS), empty))

    assert [idea.instrument for idea in ideas] == ["BTC-USD"]


def test_fresh_strategy_instance_per_symbol_series() -> None:
    built: list[SpotStrategy] = []

    def factory() -> SpotStrategy:
        strategy = SpotStrategy()
        built.append(strategy)
        return strategy

    proposer = SnapshotStrategyProposer(factory, strategy_name="baseline-spot")
    snapshot = snapshot_of(make_series(GOLDEN_CROSS), make_series(GOLDEN_CROSS, symbol="ETH-USD"))

    ideas = proposer.propose(snapshot)

    assert [idea.instrument for idea in ideas] == ["BTC-USD", "ETH-USD"]
    assert len(built) == 2
    proposer.propose(snapshot)
    assert len(built) == 4


def test_decide_inputs_come_from_snapshot_only() -> None:
    spy = SpyStrategy()
    series = make_series(GOLDEN_CROSS)

    SnapshotStrategyProposer(lambda: spy, strategy_name="spy").propose(snapshot_of(series))

    assert len(spy.calls) == 1
    call = spy.calls[0]
    closes = [candle.close for candle in series.candles]
    assert call["symbol"] == "BTC-USD"
    assert call["recent_marks"] == closes
    assert call["current_mark"] == closes[-1]
    assert call["candles"] == series.candles
    # The snapshot contract carries no account or position state.
    assert call["position_state"] is None
    assert call["equity"] == Decimal("0")
    assert call["product"] is None
    assert call["market_data"] is None


def test_naive_snapshot_as_of_is_treated_as_utc() -> None:
    naive = AS_OF.replace(tzinfo=None)

    ideas = spot_proposer().propose(
        snapshot_of(make_series(GOLDEN_CROSS, as_of=naive), as_of=naive)
    )

    assert len(ideas) == 1
    assert ideas[0].time_horizon.expires_at == AS_OF + timedelta(hours=48)
