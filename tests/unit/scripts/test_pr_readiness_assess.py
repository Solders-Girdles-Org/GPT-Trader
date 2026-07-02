from __future__ import annotations

from scripts.agents.pr_readiness import (
    BranchProtection,
    CheckStatus,
    PullRequestState,
    ReviewSignal,
    ReviewThread,
    assess_readiness,
)


def _protection(
    *,
    conversation_resolution: bool = False,
    required_checks: tuple[str, ...] = ("Unit Tests (Core)",),
    required_review_count: int = 0,
    strict: bool = True,
) -> BranchProtection:
    return BranchProtection(
        strict=strict,
        conversation_resolution=conversation_resolution,
        required_review_count=required_review_count,
        required_checks=required_checks,
    )


# --------------------------------------------------------------------------- #
# assess_readiness - the scenario that bit us
# --------------------------------------------------------------------------- #
def test_green_checks_but_unresolved_threads_is_not_ready() -> None:
    """All required checks SUCCESS, but enforced conversation resolution + open
    threads must still report NOT READY (the PR #1056 situation)."""
    state = PullRequestState(
        number=1056,
        head_oid="c6f5c2c4",
        merge_state_status="BLOCKED",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(
            ReviewThread("chatgpt-codex-connector", False, "medium", "src/x.py", 152),
            ReviewThread("coderabbitai", False, "high", "src/x.py", 236),
        ),
        protection=_protection(conversation_resolution=True),
    )
    report = assess_readiness(state)

    assert report.ready is False
    blockers = [finding for finding in report.findings if finding.severity == "blocker"]
    assert len(blockers) == 1
    assert "unresolved review thread" in blockers[0].message
    assert "block the merge" in blockers[0].message


def test_unresolved_threads_without_enforcement_is_warning_not_blocker() -> None:
    state = PullRequestState(
        number=1,
        head_oid="abc",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(ReviewThread("coderabbitai", False, "low", "docs/x.md", 1),),
        protection=_protection(conversation_resolution=False),
    )
    report = assess_readiness(state)

    assert report.ready is True
    assert any(finding.severity == "warning" for finding in report.findings)


def test_failing_required_check_is_blocker() -> None:
    state = PullRequestState(
        number=2,
        head_oid="abc",
        merge_state_status="BLOCKED",
        review_decision="",
        checks=(
            CheckStatus("Unit Tests (Core)", "FAILURE", required=True),
            CheckStatus("CodeRabbit", "SUCCESS", required=False),
        ),
        threads=(),
        protection=_protection(),
    )
    report = assess_readiness(state)

    assert report.ready is False
    assert any("Unit Tests (Core)" in finding.message for finding in report.findings)


def test_missing_required_check_is_blocker() -> None:
    state = PullRequestState(
        number=20,
        head_oid="abc",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(),
        threads=(),
        protection=_protection(required_checks=("Unit Tests (Core)",)),
    )
    report = assess_readiness(state)

    assert report.ready is False
    assert any("missing" in finding.message for finding in report.findings)


def test_skipped_and_neutral_required_checks_are_passing() -> None:
    state = PullRequestState(
        number=21,
        head_oid="abc",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(
            CheckStatus("Docs Link Audit", "SKIPPED", required=True),
            CheckStatus("Analyze Python", "NEUTRAL", required=True),
        ),
        threads=(),
        protection=_protection(required_checks=("Docs Link Audit", "Analyze Python")),
    )
    report = assess_readiness(state)

    assert report.ready is True


def test_blocked_state_without_specific_finding_is_not_ready() -> None:
    state = PullRequestState(
        number=22,
        head_oid="abc",
        merge_state_status="BLOCKED",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(ReviewThread("coderabbitai", False, "low", "docs/x.md", 1),),
        protection=_protection(conversation_resolution=False),
    )
    report = assess_readiness(state)

    assert report.ready is False
    assert any("Branch protection" in finding.message for finding in report.findings)


def test_draft_state_is_not_ready() -> None:
    state = PullRequestState(
        number=23,
        head_oid="abc",
        merge_state_status="DRAFT",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
    )
    report = assess_readiness(state)

    assert report.ready is False
    assert any("draft" in finding.message.lower() for finding in report.findings)


def test_unknown_merge_state_is_not_ready() -> None:
    state = PullRequestState(
        number=24,
        head_oid="abc",
        merge_state_status="UNKNOWN",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
    )
    report = assess_readiness(state)

    assert report.ready is False
    assert any("mergeability" in finding.message for finding in report.findings)


def test_clean_pr_with_no_open_threads_is_ready() -> None:
    state = PullRequestState(
        number=3,
        head_oid="abc",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(ReviewThread("coderabbitai", True, "high", "src/x.py", 1),),
        protection=_protection(conversation_resolution=True),
    )
    report = assess_readiness(state)

    assert report.ready is True
    assert any(finding.severity == "warning" for finding in report.findings)
    assert any("current-head review" in finding.message for finding in report.findings)


def test_behind_base_is_blocker() -> None:
    state = PullRequestState(
        number=4,
        head_oid="abc",
        merge_state_status="BEHIND",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
    )
    report = assess_readiness(state)

    assert report.ready is False
    assert any("behind" in finding.message.lower() for finding in report.findings)


def test_stacked_pr_base_branch_is_warning() -> None:
    state = PullRequestState(
        number=6,
        head_oid="abc",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
        base_ref_name="feature/parent-branch",
    )
    report = assess_readiness(state)

    assert report.ready is True  # warning, not blocker
    stacked = [finding for finding in report.findings if "Stacked PR" in finding.message]
    assert len(stacked) == 1
    assert stacked[0].severity == "warning"
    assert "feature/parent-branch" in stacked[0].message
    assert "restore the deleted branch" in stacked[0].message


def test_main_based_pr_has_no_stacked_warning() -> None:
    state = PullRequestState(
        number=7,
        head_oid="abc",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
    )
    report = assess_readiness(state)

    assert not any("Stacked PR" in finding.message for finding in report.findings)


def test_required_review_missing_is_blocker() -> None:
    state = PullRequestState(
        number=5,
        head_oid="abc",
        merge_state_status="BLOCKED",
        review_decision="REVIEW_REQUIRED",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(required_review_count=1),
    )
    report = assess_readiness(state)

    assert report.ready is False
    assert any("approving review" in finding.message for finding in report.findings)


def test_missing_current_head_review_signal_can_block() -> None:
    state = PullRequestState(
        number=6,
        head_oid="abcdef1234",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
    )
    report = assess_readiness(state, require_current_head_review_signal=True)

    assert report.ready is False
    assert any("current-head review" in finding.message for finding in report.findings)


def test_missing_current_head_review_signal_warns_by_default() -> None:
    state = PullRequestState(
        number=6,
        head_oid="abcdef1234",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
    )
    report = assess_readiness(state)

    assert report.ready is True
    assert any(finding.severity == "warning" for finding in report.findings)
    assert any("not a universal PR gate" in finding.message for finding in report.findings)


def test_current_head_review_signal_satisfies_required_signal() -> None:
    state = PullRequestState(
        number=7,
        head_oid="abcdef1234",
        merge_state_status="CLEAN",
        review_decision="",
        checks=(CheckStatus("Unit Tests (Core)", "SUCCESS", required=True),),
        threads=(),
        protection=_protection(),
        review_signals=(
            ReviewSignal(
                kind="pr_reaction",
                author="chatgpt-codex-connector[bot]",
                state="+1",
                current_head=True,
            ),
        ),
    )
    report = assess_readiness(state, require_current_head_review_signal=True)

    assert report.ready is True
