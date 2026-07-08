from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from gpt_trader.core import Candle
from gpt_trader.features.intelligence.regime import MarketRegimeDetector, RegimeState, RegimeType
from gpt_trader.features.intelligence.sizing import PositionSizer, PositionSizingConfig
from gpt_trader.features.trade_ideas import (
    BaselineProposer,
    BaselineProposerConfig,
    MarketSnapshot,
    Proposer,
    SymbolSeries,
    TradeDirection,
    TradeIdeaPositionSizingBridge,
    TradeIdeaSizingConfig,
    evaluate_eligibility,
)

AS_OF = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
CONFIG = BaselineProposerConfig(short_window=5, long_window=20, crossover_lookback=3)


def make_series(
    closes: list[str],
    symbol: str = "BTC-USD",
    last_volume: str = "1000",
    as_of: datetime = AS_OF,
) -> SymbolSeries:
    candles = []
    for index, close in enumerate(closes):
        price = Decimal(close)
        volume = Decimal(last_volume) if index == len(closes) - 1 else Decimal("1000")
        candles.append(
            Candle(
                ts=as_of - timedelta(days=len(closes) - index),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
            )
        )
    return SymbolSeries(symbol=symbol, granularity="1d", candles=tuple(candles))


def snapshot_of(*series: SymbolSeries, as_of: datetime = AS_OF) -> MarketSnapshot:
    return MarketSnapshot(as_of=as_of, source="coinbase:candles", series=series)


GOLDEN_CROSS = ["100"] * 28 + ["102", "104"]
FLAT = ["100"] * 30
DOWNTREND = [str(130 - i) for i in range(30)]


class StaticRegimeDetector:
    def __init__(self, state: RegimeState) -> None:
        self._state = state

    def get_regime(self, symbol: str) -> RegimeState:
        return self._state

    def get_indicator_values(self, symbol: str) -> dict[str, object]:
        return {"atr": None}


def test_satisfies_proposer_protocol() -> None:
    assert isinstance(BaselineProposer(CONFIG), Proposer)


def test_fresh_golden_cross_produces_long_idea() -> None:
    ideas = BaselineProposer(CONFIG).propose(snapshot_of(make_series(GOLDEN_CROSS)))

    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.direction is TradeDirection.LONG
    assert idea.instrument == "BTC-USD"
    assert evaluate_eligibility(idea) == []


def test_idea_records_are_complete_and_pinned() -> None:
    idea = BaselineProposer(CONFIG).propose(snapshot_of(make_series(GOLDEN_CROSS)))[0]

    assert idea.time_horizon.expires_at == AS_OF + timedelta(hours=CONFIG.expiry_hours)
    assert idea.data_used[0] == f"coinbase:candles:BTC-USD:1d:as_of={AS_OF.isoformat()}"
    assert any("engine=position-sizer-bridge-v1" in item for item in idea.data_used)
    # Sized at the worst permitted entry (entry_zone.upper), not the last close.
    assert idea.max_loss.amount == Decimal("2.26")
    assert idea.max_loss.percent_of_account == Decimal("0.0226")
    assert any("Risk budget cap remains 2% per idea" in item for item in idea.max_loss.assumptions)
    assert idea.entry_zone.lower is not None
    assert idea.entry_zone.upper is not None
    assert idea.entry_zone.lower < idea.entry_zone.upper
    assert idea.sizing_recommendation.quantity is not None
    assert idea.sizing_recommendation.notional is not None
    assert "kelly_factor=1.0000 (kelly_enabled=false)" in idea.sizing_recommendation.rationale
    # Machine-readable exit levels for filled-position resolution (#1218),
    # bracketing the entry and consistent with the free-text plan.
    assert idea.exit_plan is not None
    assert idea.exit_plan.stop is not None
    assert idea.exit_plan.target is not None
    assert idea.exit_plan.stop < idea.entry_zone.upper < idea.exit_plan.target
    assert str(idea.exit_plan.stop) in idea.invalidation
    assert str(idea.exit_plan.target) in idea.target_exit


def test_position_sizer_enriches_sizing_with_regime_kelly_and_budget_cap() -> None:
    state = RegimeState(
        regime=RegimeType.BULL_QUIET,
        confidence=0.8,
        trend_score=0.7,
        volatility_percentile=0.2,
        momentum_score=0.8,
    )
    position_config = PositionSizingConfig(
        base_position_fraction=2.0,
        max_position_fraction=2.0,
        min_position_fraction=0.0,
        regime_scale_factors={RegimeType.BULL_QUIET.name: 1.2},
        enable_volatility_scaling=False,
        enable_confidence_scaling=False,
        enable_kelly_sizing=True,
        max_portfolio_heat=2.0,
    )
    sizer = PositionSizer(
        regime_detector=cast(MarketRegimeDetector, StaticRegimeDetector(state)),
        config=position_config,
    )
    for index in range(10):
        sizer.record_trade_result("BTC-USD", is_win=index < 8)
    sizing_config = TradeIdeaSizingConfig(
        equity=Decimal("10000"),
        position_sizing_config=position_config,
    )
    proposer = BaselineProposer(
        CONFIG,
        sizing_bridge=TradeIdeaPositionSizingBridge(sizing_config, position_sizer=sizer),
    )

    idea = proposer.propose(snapshot_of(make_series(GOLDEN_CROSS)))[0]

    assert idea.sizing_recommendation.quantity is not None
    assert idea.sizing_recommendation.notional is not None
    assert "regime=BULL_QUIET regime_factor=1.0800" in idea.sizing_recommendation.rationale
    assert "kelly_factor=0.6500 (kelly_enabled=true)" in idea.sizing_recommendation.rationale
    assert "budget_cap=applied" in idea.sizing_recommendation.rationale
    sizing_inputs = next(item for item in idea.data_used if item.startswith("sizing:BTC-USD"))
    assert "regime=BULL_QUIET" in sizing_inputs
    assert "kelly_factor=0.6500" in sizing_inputs
    assert "budget_cap_applied=true" in sizing_inputs


def test_internal_naive_snapshot_as_of_produces_aware_expiry() -> None:
    naive_as_of = AS_OF.replace(tzinfo=None)

    idea = BaselineProposer(CONFIG).propose(
        snapshot_of(make_series(GOLDEN_CROSS, as_of=naive_as_of), as_of=naive_as_of)
    )[0]

    assert idea.time_horizon.expires_at == naive_as_of.replace(tzinfo=UTC) + timedelta(
        hours=CONFIG.expiry_hours
    )


def test_flat_market_produces_nothing() -> None:
    assert BaselineProposer(CONFIG).propose(snapshot_of(make_series(FLAT))) == []


def test_downtrend_produces_nothing() -> None:
    assert BaselineProposer(CONFIG).propose(snapshot_of(make_series(DOWNTREND))) == []


def test_stale_crossover_outside_lookback_produces_nothing() -> None:
    stale = ["100"] * 20 + [str(102 + 2 * i) for i in range(10)]

    assert BaselineProposer(CONFIG).propose(snapshot_of(make_series(stale))) == []


def test_degenerate_rounded_levels_produce_nothing() -> None:
    # A sub-dollar close within half a cent of the long average rounds the
    # stop and the entry midpoint to the same 0.01-precision level, leaving
    # no defined risk per unit (#1226): stop 0.82, entry zone 0.81-0.83.
    degenerate = ["0.82"] * 29 + ["0.821"]

    assert BaselineProposer(CONFIG).propose(snapshot_of(make_series(degenerate))) == []


def test_sub_dollar_crossover_with_clear_ma_gap_still_proposes() -> None:
    clear_gap = ["0.80"] * 28 + ["0.81", "0.83"]

    ideas = BaselineProposer(CONFIG).propose(snapshot_of(make_series(clear_gap)))

    assert len(ideas) == 1
    assert ideas[0].direction is TradeDirection.LONG


def test_insufficient_history_produces_nothing() -> None:
    short_history = ["100"] * 10 + ["102", "104"]

    assert BaselineProposer(CONFIG).propose(snapshot_of(make_series(short_history))) == []


def test_identical_snapshots_produce_identical_records() -> None:
    proposer = BaselineProposer(CONFIG)

    first = proposer.propose(snapshot_of(make_series(GOLDEN_CROSS)))[0]
    second = proposer.propose(snapshot_of(make_series(GOLDEN_CROSS)))[0]

    assert first.record_hash() == second.record_hash()
    assert first.decision_id == second.decision_id


def test_volume_confirmation_raises_confidence() -> None:
    confirmed = BaselineProposer(CONFIG).propose(
        snapshot_of(make_series(GOLDEN_CROSS, last_volume="5000"))
    )[0]
    unconfirmed = BaselineProposer(CONFIG).propose(
        snapshot_of(make_series(GOLDEN_CROSS, last_volume="1000"))
    )[0]

    assert confirmed.confidence.label.value == "medium"
    assert unconfirmed.confidence.label.value == "low"


def test_multi_symbol_snapshot_only_signals_where_warranted() -> None:
    snapshot = snapshot_of(
        make_series(GOLDEN_CROSS, symbol="BTC-USD"),
        make_series(FLAT, symbol="ETH-USD"),
    )

    ideas = BaselineProposer(CONFIG).propose(snapshot)

    assert [idea.instrument for idea in ideas] == ["BTC-USD"]
    assert "btcusd" in ideas[0].decision_id


@pytest.mark.parametrize("symbol", ["BTC-USD", "ETH-USD"])
def test_decision_ids_are_distinct_per_symbol(symbol: str) -> None:
    idea = BaselineProposer(CONFIG).propose(snapshot_of(make_series(GOLDEN_CROSS, symbol=symbol)))[
        0
    ]

    assert symbol.lower().replace("-", "") in idea.decision_id


def test_exit_overlay_adjusts_levels_prose_and_sizing() -> None:
    from gpt_trader.features.trade_ideas.baseline import ExitLevels

    def widen(symbol: str, close: Decimal, levels: ExitLevels) -> ExitLevels:
        return ExitLevels(
            stop_level=close - 2 * (close - levels.stop_level),
            reward_multiple=Decimal("3"),
            stop_basis="the widened stop",
        )

    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    baseline_idea = BaselineProposer(CONFIG).propose(snapshot)[0]
    idea = BaselineProposer(CONFIG).propose(snapshot, exit_overlay=widen)[0]

    # close 104, baseline stop 100.30 -> widened stop 96.60, 3R target 126.20.
    assert idea.exit_plan is not None
    assert idea.exit_plan.stop == Decimal("96.60")
    assert idea.exit_plan.target == Decimal("126.20")
    assert idea.invalidation == "Close below the widened stop (96.60)"
    assert "(3R)" in idea.target_exit
    assert str(idea.exit_plan.target) in idea.target_exit
    assert idea.entry_zone == baseline_idea.entry_zone
    # Sizing and its written risk snapshot must follow the adjusted stop
    # distance: (105.04 - 96.60) / 105.04 = 8.04%, not the baseline's 4.51%.
    assert any("stop distance is 8.04% from there" in item for item in idea.max_loss.assumptions)
    assert any(
        "stop distance is 4.51% from there" in item for item in baseline_idea.max_loss.assumptions
    )
    assert evaluate_eligibility(idea) == []


def test_exit_overlay_without_adjustment_reproduces_baseline_record() -> None:
    from gpt_trader.features.trade_ideas.baseline import ExitLevels

    def passthrough(symbol: str, close: Decimal, levels: ExitLevels) -> ExitLevels:
        return levels

    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    baseline_idea = BaselineProposer(CONFIG).propose(snapshot)[0]
    idea = BaselineProposer(CONFIG).propose(snapshot, exit_overlay=passthrough)[0]

    assert idea == baseline_idea


def test_exit_overlay_that_breaks_level_ordering_skips_idea() -> None:
    from gpt_trader.features.trade_ideas.baseline import ExitLevels

    def stop_above_close(symbol: str, close: Decimal, levels: ExitLevels) -> ExitLevels:
        return ExitLevels(
            stop_level=close + 1,
            reward_multiple=levels.reward_multiple,
            stop_basis=levels.stop_basis,
        )

    assert (
        BaselineProposer(CONFIG).propose(
            snapshot_of(make_series(GOLDEN_CROSS)), exit_overlay=stop_above_close
        )
        == []
    )


def test_exit_overlay_that_produces_non_positive_stop_skips_idea() -> None:
    from gpt_trader.features.trade_ideas.baseline import ExitLevels

    def negative_stop(symbol: str, close: Decimal, levels: ExitLevels) -> ExitLevels:
        return ExitLevels(
            stop_level=Decimal("-1"),
            reward_multiple=levels.reward_multiple,
            stop_basis=levels.stop_basis,
        )

    assert (
        BaselineProposer(CONFIG).propose(
            snapshot_of(make_series(GOLDEN_CROSS)), exit_overlay=negative_stop
        )
        == []
    )
