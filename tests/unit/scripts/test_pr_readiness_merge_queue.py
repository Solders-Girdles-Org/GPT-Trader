from __future__ import annotations

from scripts.agents.pr_readiness import (
    BranchProtection,
    CheckStatus,
    PullRequestState,
    ReviewSignal,
    assess_readiness,
)


def _protection() -> BranchProtection:
    return BranchProtection(
        strict=False,
        conversation_resolution=False,
        required_review_count=0,
        required_checks=("Unit Tests (Core)",),
    )


# --------------------------------------------------------------------------- #
# assess_readiness: merge-queue semantics (#1127)
# --------------------------------------------------------------------------- #
def _green_blocked_state() -> PullRequestState:
    return PullRequestState(
        number=1,
        head_oid="a367f3c6deadbeef",
        merge_state_status="BLOCKED",
        review_decision="",
        checks=(CheckStatus(name="Unit Tests (Core)", state="SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
        review_signals=(
            ReviewSignal(kind="review", author="bot", state="COMMENTED", current_head=True),
        ),
    )


def test_blocked_with_merge_queue_is_ready_to_enqueue() -> None:
    report = assess_readiness(_green_blocked_state(), merge_queue_active=True)

    assert report.ready is True
    assert any(
        finding.severity == "info" and "Merge queue is active" in finding.message
        for finding in report.findings
    )


def test_blocked_without_merge_queue_stays_a_blocker() -> None:
    report = assess_readiness(_green_blocked_state(), merge_queue_active=False)

    assert report.ready is False
    assert any(
        finding.severity == "blocker" and "Branch protection is blocking" in finding.message
        for finding in report.findings
    )
