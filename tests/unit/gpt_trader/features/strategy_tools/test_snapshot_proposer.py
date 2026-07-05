from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from gpt_trader.app.config import MeanReversionConfig
from gpt_trader.core import Candle
from gpt_trader.errors import ValidationError
from gpt_trader.features.live_trade.strategies.baseline import (
    Action,
    BaselinePerpsStrategy,
    Decision,
    SpotStrategy,
)
from gpt_trader.features.live_trade.strategies.mean_reversion import MeanReversionStrategy
from gpt_trader.features.live_trade.strategies.regime_switcher import RegimeSwitchingStrategy
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
# Flat closes then a sharp final-bar dip: the Z-Score over the 20-candle
# window drops far below the -2.0 entry threshold (mean reversion long).
MEAN_REVERSION_DIP = ["100"] * 29 + ["96"]


def damped_sideways_closes(count: int) -> list[str]:
    """Ultra-quiet damped oscillation around 100.

    Strictly shrinking moves keep the regime detector's classification stable
    (SIDEWAYS_QUIET) once its 50-candle long EMA warms, so the first regime
    confirms at candle 54 (long-EMA 50 + min-regime-ticks 5 - 1). Appending a
    sharp dip as the final bar hands the switcher's mean-reversion delegate a
    deep negative Z-Score without flipping the confirmed regime.
    """
    return [f"{100 + (1 if i % 2 == 0 else -1) * 0.05 * (0.995 ** i):.4f}" for i in range(count)]


REGIME_SWITCHER_DIP = damped_sideways_closes(59) + ["96"]


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


def _mean_reversion_strategy() -> MeanReversionStrategy:
    return MeanReversionStrategy(MeanReversionConfig())


def mean_reversion_proposer() -> SnapshotStrategyProposer:
    return SnapshotStrategyProposer(_mean_reversion_strategy, strategy_name="mean-reversion")


def regime_switcher_proposer() -> SnapshotStrategyProposer:
    return SnapshotStrategyProposer(
        lambda: RegimeSwitchingStrategy(
            trend_strategy_factory=SpotStrategy,
            mean_reversion_strategy_factory=_mean_reversion_strategy,
            enable_shorts=False,
        ),
        strategy_name="regime-switcher",
        drive="per-candle",
    )


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


def test_strategy_name_is_normalized() -> None:
    padded = SnapshotStrategyProposer(SpotStrategy, strategy_name=" baseline-spot ")

    assert padded.proposer_id == "snapshot-strategy-baseline-spot"

    ideas = padded.propose(snapshot_of(make_series(GOLDEN_CROSS)))
    assert ideas == spot_proposer().propose(snapshot_of(make_series(GOLDEN_CROSS)))


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


def test_non_spot_product_type_fails_closed() -> None:
    perps = SnapshotStrategyProposer(
        BaselinePerpsStrategy,
        strategy_name="baseline-perps",
        product_type=ProductType.FUTURES,
    )

    with pytest.raises(ValidationError, match="supports spot ideas only"):
        perps.propose(snapshot_of(make_series(GOLDEN_CROSS)))


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


def test_unknown_drive_fails_closed() -> None:
    with pytest.raises(ValidationError, match="drive"):
        SnapshotStrategyProposer(
            SpotStrategy,
            strategy_name="baseline-spot",
            drive="warp",  # type: ignore[arg-type]
        )


def test_per_candle_drive_feeds_growing_prefixes_from_the_snapshot() -> None:
    spy = SpyStrategy()
    series = make_series(GOLDEN_CROSS)

    SnapshotStrategyProposer(lambda: spy, strategy_name="spy", drive="per-candle").propose(
        snapshot_of(series)
    )

    closes = [candle.close for candle in series.candles]
    assert len(spy.calls) == len(closes)
    for index, call in enumerate(spy.calls):
        assert call["recent_marks"] == closes[: index + 1]
        assert call["current_mark"] == closes[index]
        assert call["candles"] == series.candles[: index + 1]
        assert call["position_state"] is None
        assert call["equity"] == Decimal("0")


def test_per_candle_warmup_decisions_never_become_ideas() -> None:
    class EarlyBuyStrategy:
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
            if len(recent_marks) == 5:
                return Decision(Action.BUY, "warm-up buy", confidence=0.9, indicators={})
            return Decision(Action.HOLD, "hold", confidence=0.0, indicators={})

    proposer = SnapshotStrategyProposer(
        EarlyBuyStrategy, strategy_name="early-buy", drive="per-candle"
    )

    assert proposer.propose(snapshot_of(make_series(GOLDEN_CROSS))) == []


def test_drives_agree_for_pure_strategies() -> None:
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))

    final_bar = spot_proposer().propose(snapshot)
    per_candle = SnapshotStrategyProposer(
        SpotStrategy, strategy_name="baseline-spot", drive="per-candle"
    ).propose(snapshot)

    assert final_bar == per_candle


def test_mean_reversion_dip_maps_to_eligible_executable_long_idea() -> None:
    snapshot = snapshot_of(make_series(MEAN_REVERSION_DIP))

    first = mean_reversion_proposer().propose(snapshot)
    second = mean_reversion_proposer().propose(snapshot)

    assert len(first) == 1
    assert first == second
    idea = first[0]
    assert idea.direction is TradeDirection.LONG
    assert idea.product_type is ProductType.SPOT
    assert evaluate_eligibility(idea) == []
    assert idea.sizing_recommendation.quantity is not None
    assert idea.sizing_recommendation.quantity > 0
    assert idea.max_loss.amount is not None


def test_mean_reversion_holds_below_its_lookback_window() -> None:
    below_floor = make_series(["100"] * 15 + ["96"])

    assert mean_reversion_proposer().propose(snapshot_of(below_floor)) == []


def test_regime_switcher_emits_deterministic_eligible_idea() -> None:
    snapshot = snapshot_of(make_series(REGIME_SWITCHER_DIP))

    first = regime_switcher_proposer().propose(snapshot)
    second = regime_switcher_proposer().propose(snapshot)

    assert len(first) == 1
    assert first == second
    assert [idea.record_hash() for idea in first] == [idea.record_hash() for idea in second]
    idea = first[0]
    assert idea.direction is TradeDirection.LONG
    assert evaluate_eligibility(idea) == []
    assert idea.sizing_recommendation.quantity is not None
    assert idea.sizing_recommendation.quantity > 0


def test_regime_switcher_holds_until_detector_confirms_a_regime() -> None:
    # 53 candles: the detector's 50-candle long EMA has warmed, but the first
    # regime cannot confirm before candle 54 (min-regime-ticks 5), so the
    # switcher still holds on the dip the mean-reversion delegate would buy.
    below_floor = make_series(damped_sideways_closes(52) + ["96"])

    assert regime_switcher_proposer().propose(snapshot_of(below_floor)) == []
