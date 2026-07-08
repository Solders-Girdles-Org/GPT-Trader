from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gpt_trader.core import Candle
from gpt_trader.features.intelligence.regime import RegimeConfig, RegimeState, RegimeType
from gpt_trader.features.trade_ideas import (
    REGIME_DETECTOR_VERSION,
    BaselineProposer,
    BaselineProposerConfig,
    ConfidenceLabel,
    MarketSnapshot,
    Proposer,
    RegimeAwareProposer,
    RegimeAwareProposerConfig,
    ReplayOutcome,
    ReplayRunnerConfig,
    SymbolSeries,
    TradeIdeaReplayRunner,
    evaluate_eligibility,
)

AS_OF = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
CONFIG = RegimeAwareProposerConfig(
    baseline_config=BaselineProposerConfig(
        short_window=5,
        long_window=20,
        crossover_lookback=3,
    )
)
GOLDEN_CROSS = ["100"] * 28 + ["102", "104"]


class ScriptedRegimeDetector:
    config: RegimeConfig

    def __init__(self, config: RegimeConfig, state: RegimeState) -> None:
        self.config = config
        self._state = state
        self.updates: list[tuple[str, Decimal]] = []

    def update(self, symbol: str, price: Decimal) -> RegimeState:
        self.updates.append((symbol, price))
        return self._state


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


def regime_state(regime: RegimeType) -> RegimeState:
    return RegimeState(
        regime=regime,
        confidence=0.82,
        trend_score=0.65,
        volatility_percentile=0.22,
        momentum_score=0.71,
        regime_age_ticks=8,
        transition_probability=0.14,
    )


def scripted_factory(
    state: RegimeState,
) -> tuple[Callable[[RegimeConfig], ScriptedRegimeDetector], list[ScriptedRegimeDetector]]:
    detectors: list[ScriptedRegimeDetector] = []

    def factory(config: RegimeConfig) -> ScriptedRegimeDetector:
        detector = ScriptedRegimeDetector(config, state)
        detectors.append(detector)
        return detector

    return factory, detectors


def test_satisfies_proposer_protocol() -> None:
    assert isinstance(RegimeAwareProposer(CONFIG), Proposer)


def test_regime_aware_proposer_enriches_baseline_signal() -> None:
    factory, detectors = scripted_factory(regime_state(RegimeType.BULL_QUIET))
    proposer = RegimeAwareProposer(CONFIG, detector_factory=factory)

    ideas = proposer.propose(snapshot_of(make_series(GOLDEN_CROSS)))

    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.decision_id.startswith("trade-20260612-btcusd-")
    assert idea.confidence.label is ConfidenceLabel.MEDIUM
    assert "Regime overlay classified BTC-USD as BULL_QUIET" in idea.thesis
    assert "CRISIS or BEAR_VOLATILE or BULL_VOLATILE" in idea.invalidation
    assert any(
        f"detector={REGIME_DETECTOR_VERSION}" in item
        and "config_sha256=" in item
        and "state=BULL_QUIET" in item
        for item in idea.data_used
    )
    assert (
        "Regime overlay is CRISIS or BEAR_VOLATILE or BULL_VOLATILE before review"
        in idea.do_not_trade_if
    )
    assert evaluate_eligibility(idea) == []
    assert len(detectors) == 1
    assert len(detectors[0].updates) == len(GOLDEN_CROSS)


def test_regime_aware_proposer_uses_configured_suppressed_regime_text() -> None:
    factory, _detectors = scripted_factory(regime_state(RegimeType.BULL_QUIET))
    proposer = RegimeAwareProposer(
        RegimeAwareProposerConfig(
            baseline_config=CONFIG.baseline_config,
            suppressed_regimes=(RegimeType.SIDEWAYS_VOLATILE,),
        ),
        detector_factory=factory,
    )

    ideas = proposer.propose(snapshot_of(make_series(GOLDEN_CROSS)))

    assert len(ideas) == 1
    assert "regime overlay shifts to SIDEWAYS_VOLATILE" in ideas[0].invalidation
    assert "CRISIS or BEAR_VOLATILE" not in ideas[0].invalidation  # default policy text gone
    assert "Regime overlay is SIDEWAYS_VOLATILE before review" in ideas[0].do_not_trade_if


def test_regime_aware_proposer_suppresses_crisis_signal() -> None:
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    baseline = BaselineProposer(CONFIG.baseline_config)
    factory, _detectors = scripted_factory(regime_state(RegimeType.CRISIS))
    proposer = RegimeAwareProposer(CONFIG, detector_factory=factory)

    assert len(baseline.propose(snapshot)) == 1
    assert proposer.propose(snapshot) == []


def test_regime_downgraded_confidence_feeds_sizing() -> None:
    # A bearish overlay downgrades visible confidence to LOW; sizing must use
    # that post-overlay decision confidence, not the baseline's MEDIUM.
    snapshot = snapshot_of(make_series(GOLDEN_CROSS, last_volume="5000"))
    factory, _detectors = scripted_factory(regime_state(RegimeType.BEAR_QUIET))
    proposer = RegimeAwareProposer(CONFIG, detector_factory=factory)

    ideas = proposer.propose(snapshot)

    assert len(ideas) == 1
    idea = ideas[0]
    assert idea.confidence.label is ConfidenceLabel.LOW
    sizing_inputs = next(item for item in idea.data_used if item.startswith("sizing:"))
    assert "decision_confidence=0.3500" in sizing_inputs


def test_decision_id_covers_full_proposer_configuration() -> None:
    # Two runs differing only in an output-affecting knob (suppression policy,
    # baseline band) must not collide on decision_id in the same ideas root.
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    factory, _detectors = scripted_factory(regime_state(RegimeType.BULL_QUIET))

    default_ids = [
        idea.decision_id
        for idea in RegimeAwareProposer(CONFIG, detector_factory=factory).propose(snapshot)
    ]
    other_suppression = RegimeAwareProposerConfig(
        baseline_config=CONFIG.baseline_config,
        suppressed_regimes=(RegimeType.SIDEWAYS_VOLATILE,),
    )
    suppression_ids = [
        idea.decision_id
        for idea in RegimeAwareProposer(other_suppression, detector_factory=factory).propose(
            snapshot
        )
    ]
    wider_band = RegimeAwareProposerConfig(
        baseline_config=replace(CONFIG.baseline_config, entry_band_pct=Decimal("2")),
    )
    band_ids = [
        idea.decision_id
        for idea in RegimeAwareProposer(wider_band, detector_factory=factory).propose(snapshot)
    ]

    assert default_ids and suppression_ids and band_ids
    assert set(default_ids).isdisjoint(suppression_ids)
    assert set(default_ids).isdisjoint(band_ids)


def test_regime_aware_proposer_treats_unknown_regime_as_unready() -> None:
    # A detector that has not warmed up returns UNKNOWN; the proposer must not
    # persist a "regime-aware" idea carrying a 0.0-confidence overlay.
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    baseline = BaselineProposer(CONFIG.baseline_config)
    factory, _detectors = scripted_factory(RegimeState.unknown())
    proposer = RegimeAwareProposer(CONFIG, detector_factory=factory)

    assert len(baseline.propose(snapshot)) == 1
    assert proposer.propose(snapshot) == []


def test_replay_runner_scores_regime_aware_proposer_on_historical_candles() -> None:
    def candle(
        offset_hours: int,
        *,
        open_: str = "101",
        high: str = "102",
        low: str = "100",
        close: str = "101",
    ) -> Candle:
        return Candle(
            ts=AS_OF + timedelta(hours=offset_hours),
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("1000"),
        )

    factory, _detectors = scripted_factory(regime_state(RegimeType.BULL_QUIET))
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
            candle(-5, close="100", high="100", low="100"),
            candle(-4, close="100", high="100", low="100"),
            candle(-3, close="100", high="100", low="100"),
            candle(-2, close="100", high="100", low="100"),
            candle(-1, close="110", high="110", low="110"),
            candle(0, open_="110", close="111", high="112", low="109"),
            candle(1, open_="111", close="126", high="126", low="111"),
        ),
    )

    assert report.proposer_id == "regime-aware-ma-2-4"
    assert report.ideas_proposed == 1
    assert report.target_hits == 1
    assert report.ideas[0].outcome is ReplayOutcome.TARGET_HIT


def test_volatile_regime_widens_stop_and_target() -> None:
    # The replay-visible decision channel (#1242): a volatile classification
    # widens the stop distance about the close and moves the target with it.
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    baseline_idea = BaselineProposer(CONFIG.baseline_config).propose(snapshot)[0]
    factory, _detectors = scripted_factory(regime_state(RegimeType.SIDEWAYS_VOLATILE))
    idea = RegimeAwareProposer(CONFIG, detector_factory=factory).propose(snapshot)[0]

    assert baseline_idea.exit_plan is not None
    assert idea.exit_plan is not None
    # close 104, 20-bar average 100.30 -> stop 104 - 1.5 * 3.70 = 98.45,
    # 2R target 104 + 2 * 5.55 = 115.10.
    assert baseline_idea.exit_plan.stop == Decimal("100.30")
    assert idea.exit_plan.stop == Decimal("98.45")
    assert idea.exit_plan.target == Decimal("115.10")
    assert idea.entry_zone == baseline_idea.entry_zone
    assert "volatility-adjusted stop" in idea.invalidation
    assert "98.45" in idea.invalidation
    assert "115.10" in idea.target_exit
    assert any("stop distance is 6.27% from there" in item for item in idea.max_loss.assumptions)
    assert evaluate_eligibility(idea) == []


def test_quiet_regime_reproduces_baseline_levels() -> None:
    # With the default multiplier the diff must be attributable to volatility:
    # quiet regimes keep baseline levels exactly.
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    baseline_idea = BaselineProposer(CONFIG.baseline_config).propose(snapshot)[0]
    factory, _detectors = scripted_factory(regime_state(RegimeType.BULL_QUIET))
    idea = RegimeAwareProposer(CONFIG, detector_factory=factory).propose(snapshot)[0]

    assert idea.exit_plan == baseline_idea.exit_plan
    assert idea.entry_zone == baseline_idea.entry_zone
    assert idea.invalidation.startswith("Close below the 20-bar average (100.30)")


def test_volatile_reward_multiple_override_moves_target() -> None:
    config = RegimeAwareProposerConfig(
        baseline_config=CONFIG.baseline_config,
        volatile_reward_multiple=Decimal("3"),
    )
    factory, _detectors = scripted_factory(regime_state(RegimeType.SIDEWAYS_VOLATILE))
    idea = RegimeAwareProposer(config, detector_factory=factory).propose(
        snapshot_of(make_series(GOLDEN_CROSS))
    )[0]

    assert idea.exit_plan is not None
    # stop 98.45; 3R target = 104 + 3 * 5.55 = 120.65.
    assert idea.exit_plan.target == Decimal("120.65")
    assert "(3R)" in idea.target_exit


def test_exit_channel_config_enters_decision_id() -> None:
    snapshot = snapshot_of(make_series(GOLDEN_CROSS))
    factory, _detectors = scripted_factory(regime_state(RegimeType.SIDEWAYS_VOLATILE))

    default_ids = [
        idea.decision_id
        for idea in RegimeAwareProposer(CONFIG, detector_factory=factory).propose(snapshot)
    ]
    wider = RegimeAwareProposerConfig(
        baseline_config=CONFIG.baseline_config,
        volatile_stop_distance_multiplier=Decimal("2"),
    )
    wider_ids = [
        idea.decision_id
        for idea in RegimeAwareProposer(wider, detector_factory=factory).propose(snapshot)
    ]

    assert default_ids and wider_ids
    assert set(default_ids).isdisjoint(wider_ids)
