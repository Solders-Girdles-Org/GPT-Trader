"""Regime-overlay counterfactual diagnostics (#1243).

Split from test_regime_proposer.py (400-line hygiene cap): the diagnostics
counts that make each overlay decision channel's activity visible in replay
evidence, and their attachment to ``ReplayReport``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tests.unit.gpt_trader.features.trade_ideas.test_regime_proposer import (
    AS_OF,
    CONFIG,
    GOLDEN_CROSS,
    make_series,
    regime_state,
    scripted_factory,
    snapshot_of,
)

from gpt_trader.core import Candle
from gpt_trader.features.intelligence.regime import RegimeState, RegimeType
from gpt_trader.features.trade_ideas import (
    BaselineProposer,
    BaselineProposerConfig,
    RegimeAwareProposer,
    RegimeAwareProposerConfig,
    ReplayRunnerConfig,
    TradeIdeaReplayRunner,
)


def test_replay_diagnostics_count_suppression_and_exit_adjustments() -> None:
    # The M5 diagnosis found suppression fired 0/84 with nothing reporting
    # it; the diagnostics make each overlay channel's activity countable.
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    factory, _detectors = scripted_factory(regime_state(RegimeType.CRISIS))
    proposer = RegimeAwareProposer(CONFIG, detector_factory=factory)

    assert proposer.propose(snapshot) == []
    diagnostics = proposer.replay_diagnostics()
    assert diagnostics["candidate_ideas"] == 1
    assert diagnostics["emitted_ideas"] == 0
    assert diagnostics["suppressed_by_regime"] == {"CRISIS": 1}
    # CRISIS is volatile, so the exit channel also fired before suppression.
    assert diagnostics["exit_plans_adjusted"] == 1

    # Counts are cumulative across propose calls.
    proposer.propose(snapshot)
    assert proposer.replay_diagnostics()["suppressed_by_regime"] == {"CRISIS": 2}


def test_replay_diagnostics_count_unknown_and_emitted_regimes() -> None:
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))

    unknown_factory, _ = scripted_factory(RegimeState.unknown())
    unready = RegimeAwareProposer(CONFIG, detector_factory=unknown_factory)
    assert unready.propose(snapshot) == []
    assert unready.replay_diagnostics()["unknown_skipped"] == 1

    quiet_factory, _ = scripted_factory(regime_state(RegimeType.BULL_QUIET))
    emitting = RegimeAwareProposer(CONFIG, detector_factory=quiet_factory)
    assert len(emitting.propose(snapshot)) == 1
    diagnostics = emitting.replay_diagnostics()
    assert diagnostics["emitted_ideas"] == 1
    assert diagnostics["emitted_by_regime"] == {"BULL_QUIET": 1}
    assert diagnostics["exit_plans_adjusted"] == 0
    assert diagnostics["suppressed_by_regime"] == {}


def test_changing_the_per_regime_entry_policy_changes_counts_and_idea_set() -> None:
    # The entry policy is the suppressed_regimes tuple: widening it to a
    # regime that actually occurs must move ideas from emitted to suppressed.
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    factory, _detectors = scripted_factory(regime_state(RegimeType.BULL_QUIET))

    default_policy = RegimeAwareProposer(CONFIG, detector_factory=factory)
    assert len(default_policy.propose(snapshot)) == 1
    assert default_policy.replay_diagnostics()["suppressed_by_regime"] == {}

    deny_bull_quiet = RegimeAwareProposer(
        RegimeAwareProposerConfig(
            baseline_config=CONFIG.baseline_config,
            suppressed_regimes=(RegimeType.BULL_QUIET,),
        ),
        detector_factory=factory,
    )
    assert deny_bull_quiet.propose(snapshot) == []
    assert deny_bull_quiet.replay_diagnostics()["suppressed_by_regime"] == {"BULL_QUIET": 1}


def test_replay_report_attaches_regime_counterfactual_diagnostics() -> None:
    def candle(offset_hours: int, close: str = "100") -> Candle:
        price = Decimal(close)
        return Candle(
            ts=AS_OF + timedelta(hours=offset_hours),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal("1000"),
        )

    factory, _detectors = scripted_factory(regime_state(RegimeType.CRISIS))
    proposer = RegimeAwareProposer(
        RegimeAwareProposerConfig(
            baseline_config=BaselineProposerConfig(
                short_window=2,
                long_window=4,
                crossover_lookback=1,
                expiry_hours=3,
            )
        ),
        detector_factory=factory,
    )

    report = TradeIdeaReplayRunner(
        proposer,
        config=ReplayRunnerConfig(source="fixture:candles", min_history=5),
    ).run_series(
        symbol="BTC-USD",
        granularity="ONE_HOUR",
        candles=(
            candle(-5),
            candle(-4),
            candle(-3),
            candle(-2),
            candle(-1, close="110"),
            candle(0, close="111"),
        ),
    )

    assert report.ideas_proposed == 0
    diagnostics = report.proposer_diagnostics
    assert diagnostics is not None
    assert diagnostics["suppressed_by_regime"] == {"CRISIS": 1}
    assert report.to_dict()["proposer_diagnostics"]["suppressed_by_regime"] == {"CRISIS": 1}
    # The plain baseline exposes no diagnostics surface: its report says so.
    baseline_report = TradeIdeaReplayRunner(
        BaselineProposer(
            BaselineProposerConfig(short_window=2, long_window=4, crossover_lookback=1)
        ),
        config=ReplayRunnerConfig(source="fixture:candles", min_history=5),
    ).run_series(
        symbol="BTC-USD",
        granularity="ONE_HOUR",
        candles=(candle(-5), candle(-4), candle(-3), candle(-2), candle(-1), candle(0)),
    )
    assert baseline_report.proposer_diagnostics is None
    assert baseline_report.to_dict()["proposer_diagnostics"] is None
