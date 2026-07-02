from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.core import Candle
from gpt_trader.features.trade_ideas import (
    BaselineProposer,
    BaselineProposerConfig,
    EntryZone,
    MarketSnapshot,
    ReplayOutcome,
    ReplayRunnerConfig,
    ReplayScoringError,
    TimeHorizon,
    TradeIdea,
    TradeIdeaReplayRunner,
    TradeIdeaReplayTournamentRunner,
)

AS_OF = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


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


def scoreable_idea(**overrides: object) -> TradeIdea:
    fields = {
        "entry_zone": EntryZone(lower=Decimal("100"), upper=Decimal("102")),
        "invalidation": "Close below 95",
        "target_exit": "Take profit at 113 or exit at expiry",
        "time_horizon": TimeHorizon(
            expected_hold="1-4 hours",
            expires_at=AS_OF + timedelta(hours=4),
        ),
    }
    fields.update(overrides)
    return build_trade_idea(**fields)


def test_score_trade_idea_normalizes_common_granularity_aliases_for_expiry() -> None:
    class ScriptedProposer:
        proposer_id = "scripted"

        def propose(self, snapshot: MarketSnapshot) -> list[TradeIdea]:
            return [
                scoreable_idea(
                    time_horizon=TimeHorizon(
                        expected_hold="30 minutes",
                        expires_at=snapshot.as_of + timedelta(minutes=30),
                    ),
                )
            ]

    report = TradeIdeaReplayRunner(
        ScriptedProposer(),
        config=ReplayRunnerConfig(min_history=1),
    ).run_series(
        symbol="BTC-USD",
        granularity="1H",
        candles=(
            candle(-1, high="102", low="100", close="101"),
            candle(0, high="114", low="101", close="113"),
        ),
    )

    assert report.ideas[0].outcome is ReplayOutcome.NO_FUTURE_DATA


def test_replay_runner_normalizes_daily_granularity_before_expiry_filter() -> None:
    class DailyProposer:
        proposer_id = "daily"

        def propose(self, snapshot: MarketSnapshot) -> list[TradeIdea]:
            return [
                scoreable_idea(
                    time_horizon=TimeHorizon(
                        expected_hold="12 hours",
                        expires_at=snapshot.as_of + timedelta(hours=12),
                    ),
                )
            ]

    report = TradeIdeaReplayRunner(
        DailyProposer(),
        config=ReplayRunnerConfig(min_history=1),
    ).run_series(
        symbol="BTC-USD",
        granularity="1d",
        candles=(
            candle(-24, close="100", high="101", low="99"),
            candle(0, high="114", low="101", close="113"),
        ),
    )

    assert report.ideas_proposed == 1
    assert report.no_future_data == 1


def test_replay_runner_feeds_point_in_time_snapshots_and_reports_aggregates() -> None:
    class ScriptedProposer:
        proposer_id = "scripted"

        def propose(self, snapshot: MarketSnapshot) -> list[TradeIdea]:
            series = snapshot.series_for("BTC-USD")
            assert series is not None
            assert all(item.ts < snapshot.as_of for item in series.candles)
            if len(series.candles) != 2:
                return []
            return [
                scoreable_idea(
                    decision_id=f"trade-{snapshot.as_of:%Y%m%d%H}",
                    time_horizon=TimeHorizon(
                        expected_hold="1-4 hours",
                        expires_at=snapshot.as_of + timedelta(hours=4),
                    ),
                )
            ]

    candles = (
        candle(-2, close="99", high="100", low="98"),
        candle(-1, close="101", high="102", low="100"),
        candle(0, close="102", high="103", low="100"),
        candle(1, close="113", high="114", low="101"),
    )

    report = TradeIdeaReplayRunner(
        ScriptedProposer(),
        config=ReplayRunnerConfig(min_history=2),
    ).run_series(symbol="BTC-USD", granularity="ONE_HOUR", candles=candles)

    assert report.snapshots_evaluated == 2
    assert report.ideas_proposed == 1
    assert report.target_hits == 1
    assert report.stop_hits == 0
    assert report.target_hit_rate == Decimal("1")
    assert report.average_return_r == Decimal("2")
    assert report.to_dict()["ideas"][0]["outcome"] == "target_hit"


def test_replay_runner_scores_baseline_proposer_on_historical_candles() -> None:
    candles = (
        candle(-5, close="100", high="100", low="100"),
        candle(-4, close="100", high="100", low="100"),
        candle(-3, close="100", high="100", low="100"),
        candle(-2, close="100", high="100", low="100"),
        candle(-1, close="110", high="110", low="110"),
        candle(0, open_="110", close="111", high="112", low="109"),
        candle(1, open_="111", close="126", high="126", low="111"),
    )

    report = TradeIdeaReplayRunner(
        BaselineProposer(
            BaselineProposerConfig(
                short_window=2,
                long_window=4,
                crossover_lookback=1,
                expiry_hours=3,
            )
        ),
        config=ReplayRunnerConfig(source="fixture:candles", min_history=5),
    ).run_series(symbol="BTC-USD", granularity="ONE_HOUR", candles=candles)

    assert report.ideas_proposed == 1
    assert report.target_hits == 1
    assert report.ideas[0].outcome is ReplayOutcome.TARGET_HIT
    assert report.ideas[0].levels.stop == Decimal("102.50")
    assert report.ideas[0].levels.target == Decimal("125.00")


def test_replay_tournament_ranks_proposers_on_shared_window() -> None:
    candles = (
        candle(-8, open_="100", close="100", high="100", low="100"),
        candle(-7, open_="100", close="100", high="100", low="100"),
        candle(-6, open_="100", close="100", high="100", low="100"),
        candle(-5, open_="100", close="100", high="100", low="100"),
        candle(-4, open_="110", close="110", high="110", low="110"),
        candle(-3, open_="90", close="90", high="90", low="90"),
        candle(-2, open_="112", close="112", high="112", low="112"),
        candle(-1, open_="112", close="112", high="113", low="112"),
        candle(0, open_="132", close="132", high="132", low="132"),
    )

    report = TradeIdeaReplayTournamentRunner(
        (
            BaselineProposer(
                BaselineProposerConfig(
                    short_window=2,
                    long_window=4,
                    crossover_lookback=1,
                    expiry_hours=3,
                )
            ),
            BaselineProposer(
                BaselineProposerConfig(
                    short_window=3,
                    long_window=5,
                    crossover_lookback=1,
                    expiry_hours=3,
                )
            ),
        ),
        config=ReplayRunnerConfig(source="fixture:candles", min_history=6),
    ).run_series(symbol="BTC-USD", granularity="ONE_HOUR", candles=candles)

    assert {item.snapshots_evaluated for item in report.reports} == {3}
    assert [ranking.proposer_id for ranking in report.rankings] == [
        "baseline-ma-3-5",
        "baseline-ma-2-4",
    ]
    assert report.rankings[0].average_return_r == Decimal("2")
    assert report.rankings[0].target_hit_rate == Decimal("1")
    assert report.to_dict()["rankings"][0]["eligibility_pass_rate"] == "1"


def test_replay_tournament_computes_eligibility_pass_rate() -> None:
    class MixedEligibilityProposer:
        proposer_id = "mixed-eligibility"

        def propose(self, snapshot: MarketSnapshot) -> list[TradeIdea]:
            horizon = TimeHorizon(
                expected_hold="1-4 hours",
                expires_at=snapshot.as_of + timedelta(hours=4),
            )
            # Asymmetric 3:1 split so an inverted eligible/ineligible
            # calculation (0.25) cannot masquerade as the correct 0.75.
            return [
                scoreable_idea(
                    decision_id="trade-20260612-eligible-a",
                    time_horizon=horizon,
                ),
                scoreable_idea(
                    decision_id="trade-20260612-eligible-b",
                    time_horizon=horizon,
                ),
                scoreable_idea(
                    decision_id="trade-20260612-eligible-c",
                    time_horizon=horizon,
                ),
                scoreable_idea(
                    decision_id="trade-20260612-missing-data-used",
                    data_used=(),
                    time_horizon=horizon,
                ),
            ]

    report = TradeIdeaReplayTournamentRunner(
        (MixedEligibilityProposer(),),
        config=ReplayRunnerConfig(source="fixture:candles", min_history=1),
    ).run_series(
        symbol="BTC-USD",
        granularity="ONE_HOUR",
        candles=(
            candle(-1, high="102", low="100", close="101"),
            candle(0, high="114", low="101", close="113"),
        ),
    )

    assert report.rankings[0].eligibility_pass_rate == Decimal("0.75")
    assert report.to_dict()["rankings"][0]["eligibility_pass_rate"] == "0.75"
    report_payload = report.reports[0].to_dict()
    assert report_payload["eligibility_checked"] == 4
    assert report_payload["eligibility_passed"] == 3
    assert report_payload["eligibility_pass_rate"] == "0.75"


def test_replay_runner_config_rejects_non_positive_min_history() -> None:
    try:
        ReplayRunnerConfig(min_history=0)
    except ReplayScoringError as exc:
        assert exc.context["field"] == "min_history"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("ReplayRunnerConfig should reject min_history=0")
