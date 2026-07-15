"""Stage 1 -> 2 promotion scorecard over the idea-level trail.

Covers the observation-window rule, per-gate verdicts against the tuned
thresholds, loop-health reds, replay-derived evidence labeling, and the
decision constraint that the metric path never reads the batch cycle
manifest (docs/decisions/adopt-event-driven-execution-topology.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    CloseoutResolution,
    TimeHorizon,
    TradeIdeaService,
)
from gpt_trader.features.trade_ideas.scorecard import (
    WALL_CLOCK_EVIDENCE_LABEL,
    ScorecardThresholds,
    build_replay_evidence,
    build_stage_promotion_scorecard,
    format_stage_promotion_scorecard,
)

_START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class _Clock:
    """Mutable clock so one service can write events across a long window."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def _service(root: Path, clock: _Clock) -> TradeIdeaService:
    return TradeIdeaService(root, now_factory=clock)


def _idea(decision_id: str, *, expires_at: datetime, **overrides: Any) -> Any:
    return build_trade_idea(
        decision_id=decision_id,
        time_horizon=TimeHorizon(expected_hold="3-10 days", expires_at=expires_at),
        **overrides,
    )


def _close_with_pnl(
    service: TradeIdeaService,
    clock: _Clock,
    decision_id: str,
    *,
    proposer_actor_id: str,
    realized_amount: Decimal,
    closed_at: datetime,
) -> None:
    """Propose -> approve -> submit -> fill -> attribute one idea at ``closed_at``."""
    clock.now = closed_at - timedelta(hours=4)
    idea = _idea(decision_id, expires_at=closed_at + timedelta(days=30))
    service.propose(idea, actor_id=proposer_actor_id)
    service.approve(decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(decision_id, actor_id="operator", venue="manual")
    service.record_fill(decision_id, actor_id="operator", venue="manual")
    clock.now = closed_at
    service.record_closeout_attribution(
        decision_id,
        actor_id="rj",
        resolution=(
            CloseoutResolution.THESIS_TARGET
            if realized_amount >= 0
            else CloseoutResolution.INVALIDATION
        ),
        realized_profit_loss_amount=realized_amount,
        evidence=("broker-statement:manual",),
    )


def test_default_thresholds_carry_the_rubric_starting_values() -> None:
    thresholds = ScorecardThresholds()

    assert thresholds.min_closed_ideas == 200
    assert thresholds.min_window_days == 60
    assert thresholds.min_eligibility_pass_rate == Decimal("0.90")
    assert thresholds.min_attribution_coverage == Decimal("1")
    assert thresholds.min_risk_calibration == Decimal("0.95")


def test_scorecard_scores_gates_from_the_trail(tmp_path: Path) -> None:
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)
    attest_account_equity(service)

    # Baseline proposer: one winner at R = 25/250 = 0.1.
    _close_with_pnl(
        service,
        clock,
        "trade-baseline-001",
        proposer_actor_id="baseline-ma-10-50",
        realized_amount=Decimal("25"),
        closed_at=_START + timedelta(days=5),
    )
    # Candidate proposer: a winner (R 0.8) and a calibrated loser (R -0.4,
    # |loss| 100 <= recorded max loss 250).
    _close_with_pnl(
        service,
        clock,
        "trade-candidate-001",
        proposer_actor_id="strategy-x",
        realized_amount=Decimal("200"),
        closed_at=_START + timedelta(days=10),
    )
    _close_with_pnl(
        service,
        clock,
        "trade-candidate-002",
        proposer_actor_id="strategy-x",
        realized_amount=Decimal("-100"),
        closed_at=_START + timedelta(days=20),
    )
    # A fresh open proposal keeps the proposals-flowing red green.
    now = _START + timedelta(days=70)
    clock.now = now - timedelta(hours=1)
    service.propose(
        _idea("trade-open-001", expires_at=now + timedelta(days=30)),
        actor_id="strategy-x",
    )

    payload = build_stage_promotion_scorecard(
        service,
        now=now,
        thresholds=ScorecardThresholds(min_closed_ideas=3, min_window_days=60),
    )

    assert payload["evidence"] == WALL_CLOCK_EVIDENCE_LABEL
    assert payload["observation_window"]["closed_idea_count"] == 3
    gates = payload["gates"]
    assert gates["track_record_depth"]["status"] == "pass"
    assert gates["eligibility_pass_rate"]["status"] == "pass"
    assert gates["attribution_coverage"]["status"] == "pass"
    assert gates["risk_calibration"]["status"] == "pass"
    assert gates["risk_calibration"]["measured"]["loser_count"] == 1
    assert gates["expectancy"]["status"] == "pass"
    assert gates["expectancy"]["measured"]["average_r"] == "0.1667"
    assert gates["benchmark_edge"]["status"] == "pass"
    assert gates["benchmark_edge"]["measured"]["edge_r"] == "0.1000"
    assert gates["max_drawdown_from_peak"]["status"] == "not_yet_measurable"
    loop_health = payload["loop_health"]
    assert loop_health["proposals_flowing"]["status"] == "pass"
    assert loop_health["attribution_coverage"]["status"] == "pass"
    assert loop_health["audit_integrity"]["status"] == "pass"
    overall = payload["overall"]
    # The drawdown gate measures from this trail but no
    # max_drawdown_from_peak_pct is configured on the budget, so it stays
    # not-yet-measurable and the scorecard cannot claim promotability.
    assert overall["promotable"] is False
    assert overall["gates_passed"] == 6
    assert overall["gates_not_yet_measurable"] == 1
    assert overall["loop_health_reds"] == []


def test_observation_window_extends_past_sixty_days_to_reach_closed_depth(
    tmp_path: Path,
) -> None:
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)
    attest_account_equity(service)

    for index, closed_at in enumerate((_START, _START + timedelta(days=1))):
        _close_with_pnl(
            service,
            clock,
            f"trade-old-{index:03d}",
            proposer_actor_id="strategy-x",
            realized_amount=Decimal("50"),
            closed_at=closed_at,
        )

    now = _START + timedelta(days=100)
    payload = build_stage_promotion_scorecard(
        service,
        now=now,
        thresholds=ScorecardThresholds(min_closed_ideas=2, min_window_days=60),
    )

    # Both closeouts predate the rolling 60-day start, so the window must
    # stretch back to include the most recent two closed ideas. An idea counts
    # as closed at its terminal audit event (the fill, 4h before attribution).
    assert payload["observation_window"]["start"] == (_START - timedelta(hours=4)).isoformat()
    assert payload["observation_window"]["closed_idea_count"] == 2
    assert payload["gates"]["track_record_depth"]["status"] == "pass"
    # No proposal for 100 days: the loop-health red must fire even though the
    # depth gate passes.
    assert payload["loop_health"]["proposals_flowing"]["status"] == "fail"


def test_missing_attribution_fails_the_gate_and_the_loop_health_red(
    tmp_path: Path,
) -> None:
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)

    idea = _idea("trade-expired-001", expires_at=_START + timedelta(days=1))
    service.propose(idea, actor_id="strategy-x")
    clock.now = _START + timedelta(days=2)
    service.expire(idea.decision_id)

    payload = build_stage_promotion_scorecard(
        service,
        now=_START + timedelta(days=3),
        thresholds=ScorecardThresholds(min_closed_ideas=1, min_window_days=1),
    )

    assert payload["gates"]["attribution_coverage"]["status"] == "fail"
    assert payload["loop_health"]["attribution_coverage"]["status"] == "fail"
    assert "attribution_coverage" in payload["overall"]["loop_health_reds"]
    assert payload["overall"]["promotable"] is False


def test_benchmark_edge_needs_both_baseline_and_candidate_closeouts(
    tmp_path: Path,
) -> None:
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)
    attest_account_equity(service)

    _close_with_pnl(
        service,
        clock,
        "trade-candidate-001",
        proposer_actor_id="strategy-x",
        realized_amount=Decimal("50"),
        closed_at=_START + timedelta(days=1),
    )

    payload = build_stage_promotion_scorecard(
        service,
        now=_START + timedelta(days=2),
        thresholds=ScorecardThresholds(min_closed_ideas=1, min_window_days=1),
    )

    gate = payload["gates"]["benchmark_edge"]
    assert gate["status"] == "not_yet_measurable"
    assert gate["measured"]["baseline_closeout_count"] == 0


def test_text_rendering_separates_wall_clock_gates_from_replay_evidence(
    tmp_path: Path,
) -> None:
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)
    payload = build_stage_promotion_scorecard(service, now=_START)
    payload["replay_evidence"] = [
        build_replay_evidence(
            {
                "proposer_id": "baseline-ma-10-50",
                "symbol": "BTC-USD",
                "granularity": "ONE_HOUR",
                "source": "fixture:candles",
                "snapshots_evaluated": 10,
                "resolved_ideas": 0,
                "target_hit_rate": "0",
                "stop_hit_rate": "0",
                "average_return_r": None,
                "eligibility_pass_rate": "0",
            }
        )
    ]

    text = format_stage_promotion_scorecard(payload)

    assert text.startswith("✓ ideas scorecard OK")
    assert "Stage 1 -> 2 gates (wall-clock)" in text
    assert "Replay evidence (replay-derived" in text
    assert "not blended into gates" in text


def test_metric_path_never_references_the_cycle_manifest() -> None:
    """The decision constraint: evidence comes from the idea-level trail.

    ``cycle/manifest.jsonl`` is launchd scaffolding; if any metric-path module
    starts reading (or even naming) the manifest, the scorecard would stop
    being valid once the event-driven lane replaces the batch cycle.
    """
    import gpt_trader.features.trade_ideas.replay as replay
    import gpt_trader.features.trade_ideas.report as report
    import gpt_trader.features.trade_ideas.review_metrics as review_metrics
    import gpt_trader.features.trade_ideas.scorecard as scorecard

    for module in (scorecard, report, replay, review_metrics):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "manifest" not in source.lower(), (
            f"{module.__name__} references the cycle manifest; scorecard "
            "metrics must compute from the idea-level closeout/audit trail"
        )
    assert "idea_execution" not in Path(scorecard.__file__).read_text(encoding="utf-8")


def test_open_fill_is_not_a_closed_idea_but_overdue_fill_is(tmp_path: Path) -> None:
    """The gates read closed trades; an open position is not one (#1212).

    FILLED is a terminal workflow state, so the window used to count a live
    unexpired position as a closed idea (inflating depth, diluting attribution
    coverage). An expired fill without a closeout must still count — as an
    attribution failure — so overdue evidence stays a visible red.
    """
    clock = _Clock(_START)
    service = _service(tmp_path / "ideas", clock)
    attest_account_equity(service)
    now = _START + timedelta(days=3)

    def _fill(decision_id: str, *, expires_at: datetime) -> None:
        clock.now = _START
        idea = _idea(decision_id, expires_at=expires_at)
        service.propose(idea, actor_id="proposer-a")
        service.approve(decision_id, actor_id="rj", reason="Risk verified")
        service.record_submission(decision_id, actor_id="operator", venue="manual")
        service.record_fill(decision_id, actor_id="operator", venue="manual")

    _fill("trade-scorecard-open", expires_at=now + timedelta(days=30))
    _fill("trade-scorecard-overdue", expires_at=now - timedelta(days=1))

    scorecard = build_stage_promotion_scorecard(service, now=now)

    # Only the overdue fill is a closed idea; the open position is excluded.
    assert scorecard["observation_window"]["closed_idea_count"] == 1
    coverage = scorecard["gates"]["attribution_coverage"]
    assert coverage["status"] == "fail"
    assert coverage["measured"]["attributed_count"] == 0
    assert coverage["measured"]["closed_idea_count"] == 1
