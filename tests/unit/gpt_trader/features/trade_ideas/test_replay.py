from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.core import Candle
from gpt_trader.features.trade_ideas import (
    EntryZone,
    ReplayOutcome,
    TimeHorizon,
    TradeDirection,
    TradeIdea,
    TradeIdeaService,
    score_filled_trade_idea,
    score_trade_idea,
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


def test_score_trade_idea_records_target_hit() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(
            candle(0, high="103", low="100", close="102"),
            candle(1, high="114", low="101", close="113"),
        ),
    )

    assert result.outcome is ReplayOutcome.TARGET_HIT
    assert result.entry_price == Decimal("101")
    assert result.exit_price == Decimal("113")
    assert result.return_r == Decimal("2")
    assert result.bars_evaluated == 2


def test_score_trade_idea_requires_midpoint_entry_fill() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(
            candle(0, high="100.10", low="99.50", close="100"),
            candle(1, high="106", low="101", close="104"),
            candle(2, high="114", low="102", close="113"),
        ),
    )

    assert result.outcome is ReplayOutcome.TARGET_HIT
    assert result.entry_time == AS_OF + timedelta(hours=1)
    assert result.entry_price == Decimal("101")
    assert result.bars_evaluated == 3


def test_score_trade_idea_reports_not_filled_when_only_entry_zone_edge_trades() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(
            candle(0, high="100.10", low="99.50", close="100"),
            candle(1, high="100.50", low="99.75", close="100.25"),
        ),
    )

    assert result.outcome is ReplayOutcome.NOT_FILLED
    assert result.entry_time is None
    assert result.entry_price is None


def test_score_trade_idea_uses_conservative_stop_first_when_bar_hits_both() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(candle(0, high="114", low="94", close="101"),),
    )

    assert result.outcome is ReplayOutcome.STOP_HIT
    assert result.exit_price == Decimal("95")
    assert result.return_r == Decimal("-1")


def test_score_trade_idea_defers_target_hit_on_entry_candle() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(
            candle(0, open_="114", high="114", low="101", close="102"),
            candle(1, high="106", low="101", close="104"),
        ),
    )

    assert result.outcome is ReplayOutcome.TIMED_OUT
    assert result.entry_time == AS_OF
    assert result.exit_time == AS_OF + timedelta(hours=1)
    assert result.exit_price == Decimal("104")


def test_score_trade_idea_defers_prefill_long_stop_on_entry_candle() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(
            candle(0, open_="94", high="102", low="94", close="101"),
            candle(1, high="106", low="101", close="104"),
        ),
    )

    assert result.outcome is ReplayOutcome.TIMED_OUT
    assert result.entry_time == AS_OF
    assert result.exit_time == AS_OF + timedelta(hours=1)
    assert result.exit_price == Decimal("104")


def test_score_trade_idea_defers_prefill_short_stop_on_entry_candle() -> None:
    result = score_trade_idea(
        scoreable_idea(
            direction=TradeDirection.SHORT,
            invalidation="Close above 106",
            target_exit="Take profit at 95 or exit at expiry",
        ),
        as_of=AS_OF,
        future_candles=(
            candle(0, open_="107", high="107", low="100", close="101"),
            candle(1, high="101", low="97", close="98"),
        ),
    )

    assert result.outcome is ReplayOutcome.TIMED_OUT
    assert result.entry_time == AS_OF
    assert result.exit_time == AS_OF + timedelta(hours=1)
    assert result.exit_price == Decimal("98")


def test_score_trade_idea_keeps_long_entry_candle_stop_when_not_pinned_to_open() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(candle(0, open_="94", high="102", low="93", close="101"),),
    )

    assert result.outcome is ReplayOutcome.STOP_HIT
    assert result.exit_price == Decimal("95")


def test_score_trade_idea_keeps_short_entry_candle_stop_when_not_pinned_to_open() -> None:
    result = score_trade_idea(
        scoreable_idea(
            direction=TradeDirection.SHORT,
            invalidation="Close above 106",
            target_exit="Take profit at 95 or exit at expiry",
        ),
        as_of=AS_OF,
        future_candles=(candle(0, open_="107", high="108", low="100", close="101"),),
    )

    assert result.outcome is ReplayOutcome.STOP_HIT
    assert result.exit_price == Decimal("106")


def test_score_trade_idea_records_short_target_hit() -> None:
    result = score_trade_idea(
        scoreable_idea(
            direction=TradeDirection.SHORT,
            invalidation="Close above 106",
            target_exit="Take profit at 95 or exit at expiry",
        ),
        as_of=AS_OF,
        future_candles=(
            candle(0, high="102", low="100", close="101"),
            candle(1, high="101", low="94", close="95"),
        ),
    )

    assert result.outcome is ReplayOutcome.TARGET_HIT
    assert result.entry_price == Decimal("101")
    assert result.exit_price == Decimal("95")
    assert result.return_r == Decimal("1.2")


def test_score_trade_idea_uses_conservative_short_stop_first_when_bar_hits_both() -> None:
    result = score_trade_idea(
        scoreable_idea(
            direction=TradeDirection.SHORT,
            invalidation="Close above 106",
            target_exit="Take profit at 95 or exit at expiry",
        ),
        as_of=AS_OF,
        future_candles=(candle(0, high="107", low="94", close="101"),),
    )

    assert result.outcome is ReplayOutcome.STOP_HIT
    assert result.exit_price == Decimal("106")
    assert result.return_r == Decimal("-1")


def test_score_trade_idea_times_out_at_last_close_before_expiry() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(
            candle(0, high="103", low="100", close="102"),
            candle(3, high="106", low="99", close="104"),
            candle(4, high="120", low="104", close="119"),
        ),
    )

    assert result.outcome is ReplayOutcome.TIMED_OUT
    assert result.exit_time == AS_OF + timedelta(hours=3)
    assert result.exit_price == Decimal("104")
    assert result.return_r == Decimal("0.5")


def test_score_trade_idea_counts_waiting_bars_before_delayed_entry() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(
            candle(0, high="99", low="96", close="98"),
            candle(1, high="99.5", low="97", close="99"),
            candle(2, high="103", low="100", close="101"),
            candle(3, high="114", low="101", close="113"),
        ),
    )

    assert result.outcome is ReplayOutcome.TARGET_HIT
    assert result.entry_time == AS_OF + timedelta(hours=2)
    assert result.exit_time == AS_OF + timedelta(hours=3)
    assert result.bars_evaluated == 4


def test_score_trade_idea_reports_not_filled_when_entry_zone_never_trades() -> None:
    result = score_trade_idea(
        scoreable_idea(),
        as_of=AS_OF,
        future_candles=(
            candle(0, high="99", low="94", close="98"),
            candle(1, high="99.5", low="96", close="99"),
        ),
    )

    assert result.outcome is ReplayOutcome.NOT_FILLED
    assert result.entry_time is None
    assert result.return_r is None
    assert result.bars_evaluated == 2


def test_score_trade_idea_reports_no_future_data() -> None:
    result = score_trade_idea(scoreable_idea(), as_of=AS_OF, future_candles=())

    assert result.outcome is ReplayOutcome.NO_FUTURE_DATA
    assert result.bars_evaluated == 0


def test_score_trade_idea_excludes_candles_that_extend_past_expiry() -> None:
    result = score_trade_idea(
        scoreable_idea(
            time_horizon=TimeHorizon(
                expected_hold="30 minutes",
                expires_at=AS_OF + timedelta(minutes=30),
            ),
        ),
        as_of=AS_OF,
        future_candles=(candle(0, high="114", low="101", close="113"),),
        candle_duration=timedelta(hours=1),
    )

    assert result.outcome is ReplayOutcome.NO_FUTURE_DATA
    assert result.bars_evaluated == 0


def test_replay_scoring_does_not_mutate_live_closeout_records(tmp_path: Path) -> None:
    idea = scoreable_idea()
    service = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: AS_OF,
    )
    service.propose(idea, actor_id="idea-generator-v1")

    result = score_trade_idea(
        idea,
        as_of=AS_OF,
        future_candles=(
            candle(0, high="103", low="100", close="102"),
            candle(1, high="114", low="101", close="113"),
        ),
    )

    assert result.outcome is ReplayOutcome.TARGET_HIT
    assert service.get_closeout_attribution(idea.decision_id) is None
    assert not service.closeout_log.path.exists()


def test_replay_granularity_table_agrees_with_recorder_builder() -> None:
    """Replay's private granularity table must match the recorder builder's.

    The frozen trade_ideas dependency set (core, errors, itself) forbids
    replay from importing the recorder's shared helper, so the two tables
    are kept in exact agreement here instead — a snapshot the builder can
    produce must never be rejected or mis-scored by replay, and vice versa.
    """
    from gpt_trader.features.recorder.snapshot_builder import (
        _CANONICAL_GRANULARITY_BY_ALIAS,
        granularity_duration,
    )
    from gpt_trader.features.trade_ideas.replay import _GRANULARITY_DURATION_BY_ALIAS

    recorder_table = {
        alias: granularity_duration(alias) for alias in _CANONICAL_GRANULARITY_BY_ALIAS
    }

    assert _GRANULARITY_DURATION_BY_ALIAS == recorder_table


def test_score_filled_trade_idea_scores_stop_before_target_in_one_bar() -> None:
    """Fill-anchored scoring keeps the conservative same-bar ordering."""
    result = score_filled_trade_idea(
        scoreable_idea(),
        filled_at=AS_OF,
        fill_price=Decimal("104"),
        future_candles=(candle(1, high="114", low="94", close="100"),),  # touches both
    )

    assert result.outcome is ReplayOutcome.STOP_HIT
    assert result.exit_price == Decimal("95")


def test_score_filled_trade_idea_with_no_in_horizon_candles_is_no_future_data() -> None:
    """A fill at/after expiry has no scorable window; it must not fabricate."""
    result = score_filled_trade_idea(
        scoreable_idea(),
        filled_at=AS_OF + timedelta(hours=4),  # == expires_at
        fill_price=Decimal("104"),
        future_candles=(candle(5, high="114", low="94", close="100"),),  # post-expiry
    )

    assert result.outcome is ReplayOutcome.NO_FUTURE_DATA
