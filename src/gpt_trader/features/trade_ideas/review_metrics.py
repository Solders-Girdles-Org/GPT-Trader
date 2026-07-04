"""Read-only review instrumentation derived from the trade-idea audit log.

Approval latency and per-proposer agreement rate are the Stage-2 graduation
evidence named in docs/decisions/adopt-operator-web-console.md: the promotion
case cites how long ideas wait for a decision and how often the reviewer
agrees with each proposer. Everything here is a pure computation over audit
events; nothing mutates storage.

A *review cycle* runs from a PROPOSED event (initial proposal or resubmission)
to the first event that ends review: APPROVED, REJECTED, CHANGED, EXPIRED, or
CANCELLED. Each cycle is attributed to the actor who proposed it, so a
resubmitted idea contributes one cycle per round of review.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean, median

from gpt_trader.features.trade_ideas.audit import ActorType, AuditAction, AuditEvent

_CYCLE_START_ACTION = AuditAction.PROPOSED
_CYCLE_END_ACTIONS = frozenset(
    {
        AuditAction.APPROVED,
        AuditAction.REJECTED,
        AuditAction.CHANGED,
        AuditAction.EXPIRED,
        AuditAction.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class ReviewCycle:
    """One PROPOSED -> decision round, attributed to its proposer."""

    decision_id: str
    proposer_id: str
    outcome: AuditAction | None
    decided_by: ActorType | None
    latency_seconds: float | None

    @property
    def is_pending(self) -> bool:
        return self.outcome is None


@dataclass(frozen=True, slots=True)
class ReviewOutcomeStats:
    """Aggregated review-cycle outcomes (overall or for one proposer)."""

    proposed_count: int
    approved_count: int
    auto_approved_count: int
    rejected_count: int
    changes_requested_count: int
    expired_count: int
    cancelled_count: int
    pending_count: int
    latency_count: int
    latency_mean_seconds: float | None
    latency_median_seconds: float | None

    @property
    def human_decided_count(self) -> int:
        return self.approved_count + self.rejected_count + self.changes_requested_count

    @property
    def agreement_rate(self) -> float | None:
        """Share of human decisions that approved the idea (None until decided)."""
        decided = self.human_decided_count
        if decided == 0:
            return None
        return self.approved_count / decided


@dataclass(frozen=True, slots=True)
class ProposerReviewStats:
    """Review-cycle outcomes attributed to one proposing actor."""

    proposer_id: str
    stats: ReviewOutcomeStats


@dataclass(frozen=True, slots=True)
class ReviewInstrumentation:
    """Queue self-instrumentation: overall and per-proposer review evidence."""

    overall: ReviewOutcomeStats
    per_proposer: tuple[ProposerReviewStats, ...]


def derive_review_cycles(events: Iterable[AuditEvent]) -> tuple[ReviewCycle, ...]:
    """Split each decision's audit trail into attributed review cycles."""
    events_by_decision: dict[str, list[AuditEvent]] = {}
    for event in events:
        events_by_decision.setdefault(event.decision_id, []).append(event)

    cycles: list[ReviewCycle] = []
    for decision_id in sorted(events_by_decision):
        ordered = sorted(events_by_decision[decision_id], key=lambda event: event.timestamp)
        open_cycle: AuditEvent | None = None
        for event in ordered:
            if event.action is _CYCLE_START_ACTION:
                open_cycle = event
                continue
            if open_cycle is None or event.action not in _CYCLE_END_ACTIONS:
                continue
            latency = (event.timestamp - open_cycle.timestamp).total_seconds()
            cycles.append(
                ReviewCycle(
                    decision_id=decision_id,
                    proposer_id=open_cycle.actor_id,
                    outcome=event.action,
                    decided_by=event.actor_type,
                    latency_seconds=latency,
                )
            )
            open_cycle = None
        if open_cycle is not None:
            cycles.append(
                ReviewCycle(
                    decision_id=decision_id,
                    proposer_id=open_cycle.actor_id,
                    outcome=None,
                    decided_by=None,
                    latency_seconds=None,
                )
            )
    return tuple(cycles)


def _aggregate(cycles: Iterable[ReviewCycle]) -> ReviewOutcomeStats:
    approved = auto_approved = rejected = changes = expired = cancelled = pending = 0
    proposed = 0
    # Latency measures the human-review bottleneck; auto-approval sweeps decide
    # in the same process tick and would only dilute the evidence.
    human_latencies: list[float] = []
    for cycle in cycles:
        proposed += 1
        if cycle.outcome is None:
            pending += 1
            continue
        if cycle.outcome is AuditAction.APPROVED:
            if cycle.decided_by is ActorType.HUMAN:
                approved += 1
            else:
                auto_approved += 1
        elif cycle.outcome is AuditAction.REJECTED:
            rejected += 1
        elif cycle.outcome is AuditAction.CHANGED:
            changes += 1
        elif cycle.outcome is AuditAction.EXPIRED:
            expired += 1
        elif cycle.outcome is AuditAction.CANCELLED:
            cancelled += 1
        if cycle.decided_by is ActorType.HUMAN and cycle.latency_seconds is not None:
            human_latencies.append(cycle.latency_seconds)
    return ReviewOutcomeStats(
        proposed_count=proposed,
        approved_count=approved,
        auto_approved_count=auto_approved,
        rejected_count=rejected,
        changes_requested_count=changes,
        expired_count=expired,
        cancelled_count=cancelled,
        pending_count=pending,
        latency_count=len(human_latencies),
        latency_mean_seconds=mean(human_latencies) if human_latencies else None,
        latency_median_seconds=median(human_latencies) if human_latencies else None,
    )


def compute_review_instrumentation(events: Iterable[AuditEvent]) -> ReviewInstrumentation:
    """Compute overall and per-proposer review evidence from audit events."""
    cycles = derive_review_cycles(events)
    by_proposer: dict[str, list[ReviewCycle]] = {}
    for cycle in cycles:
        by_proposer.setdefault(cycle.proposer_id, []).append(cycle)
    return ReviewInstrumentation(
        overall=_aggregate(cycles),
        per_proposer=tuple(
            ProposerReviewStats(proposer_id=proposer_id, stats=_aggregate(by_proposer[proposer_id]))
            for proposer_id in sorted(by_proposer)
        ),
    )
