"""Review-instrumentation math over synthetic audit trails."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gpt_trader.features.trade_ideas.audit import ActorType, AuditAction, AuditEvent
from gpt_trader.features.trade_ideas.review_metrics import (
    compute_review_instrumentation,
    derive_review_cycles,
)
from gpt_trader.features.trade_ideas.workflow import TradeIdeaState

_BASE = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)


def _event(
    decision_id: str,
    action: AuditAction,
    after_state: TradeIdeaState,
    *,
    minutes: int,
    actor_id: str,
    actor_type: ActorType = ActorType.HUMAN,
) -> AuditEvent:
    return AuditEvent(
        event_id=f"{decision_id}-{action.value}-{minutes}",
        timestamp=_BASE + timedelta(minutes=minutes),
        decision_id=decision_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        before_state=None,
        after_state=after_state,
        reason="test",
        record_hash="hash",
    )


def _proposed(decision_id: str, *, minutes: int, proposer: str = "proposer-a") -> AuditEvent:
    return _event(
        decision_id,
        AuditAction.PROPOSED,
        TradeIdeaState.PROPOSED,
        minutes=minutes,
        actor_id=proposer,
        actor_type=ActorType.AI,
    )


def test_human_approval_cycle_measures_latency() -> None:
    events = [
        _proposed("idea-1", minutes=0),
        _event("idea-1", AuditAction.APPROVED, TradeIdeaState.APPROVED, minutes=30, actor_id="rj"),
    ]

    result = compute_review_instrumentation(events)

    assert result.overall.proposed_count == 1
    assert result.overall.approved_count == 1
    assert result.overall.agreement_rate == 1.0
    assert result.overall.latency_median_seconds == 30 * 60
    assert result.per_proposer[0].proposer_id == "proposer-a"


def test_auto_approval_counts_separately_and_skips_latency() -> None:
    events = [
        _proposed("idea-1", minutes=0),
        _event(
            "idea-1",
            AuditAction.APPROVED,
            TradeIdeaState.APPROVED,
            minutes=1,
            actor_id="auto-approval-sweep",
            actor_type=ActorType.SYSTEM,
        ),
    ]

    result = compute_review_instrumentation(events)

    assert result.overall.auto_approved_count == 1
    assert result.overall.approved_count == 0
    assert result.overall.agreement_rate is None
    assert result.overall.latency_count == 0


def test_resubmission_creates_a_second_attributed_cycle() -> None:
    events = [
        _proposed("idea-1", minutes=0, proposer="proposer-a"),
        _event(
            "idea-1",
            AuditAction.CHANGED,
            TradeIdeaState.NEEDS_CHANGES,
            minutes=10,
            actor_id="rj",
        ),
        _proposed("idea-1", minutes=20, proposer="proposer-b"),
        _event("idea-1", AuditAction.REJECTED, TradeIdeaState.REJECTED, minutes=50, actor_id="rj"),
    ]

    cycles = derive_review_cycles(events)
    assert [cycle.proposer_id for cycle in cycles] == ["proposer-a", "proposer-b"]
    assert [cycle.outcome for cycle in cycles] == [AuditAction.CHANGED, AuditAction.REJECTED]

    result = compute_review_instrumentation(events)
    assert result.overall.changes_requested_count == 1
    assert result.overall.rejected_count == 1
    assert result.overall.agreement_rate == 0.0
    by_proposer = {stats.proposer_id: stats.stats for stats in result.per_proposer}
    assert by_proposer["proposer-a"].changes_requested_count == 1
    assert by_proposer["proposer-b"].rejected_count == 1


def test_undecided_cycle_is_pending_and_post_decision_events_are_ignored() -> None:
    events = [
        _proposed("idea-1", minutes=0),
        _event("idea-1", AuditAction.APPROVED, TradeIdeaState.APPROVED, minutes=5, actor_id="rj"),
        _event(
            "idea-1",
            AuditAction.EXPIRED,
            TradeIdeaState.EXPIRED,
            minutes=500,
            actor_id="expiry-sweep",
            actor_type=ActorType.SYSTEM,
        ),
        _proposed("idea-2", minutes=0),
    ]

    result = compute_review_instrumentation(events)

    # The post-approval expiry ends the idea, not a review cycle.
    assert result.overall.expired_count == 0
    assert result.overall.approved_count == 1
    assert result.overall.pending_count == 1
    assert result.overall.proposed_count == 2


def test_empty_audit_log_produces_empty_instrumentation() -> None:
    result = compute_review_instrumentation([])

    assert result.overall.proposed_count == 0
    assert result.overall.agreement_rate is None
    assert result.overall.latency_median_seconds is None
    assert result.per_proposer == ()
