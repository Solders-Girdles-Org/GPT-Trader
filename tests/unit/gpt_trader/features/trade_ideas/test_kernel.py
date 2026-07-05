"""Kernel-level tests: the one gate every execution path consults (#1189).

The batch approve/sweep and paper-execution behavior the kernel now backs is
pinned by the existing service and executor suites; these tests pin the
kernel API itself — admit/deny outcomes, evidence, and the audited record of
the check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    ActorType,
    AuditAction,
    AutonomyMode,
    KernelCheck,
    PolicyViolationError,
    TradeIdeaService,
    TradeIdeaState,
)

FROZEN_NOW = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(tmp_path / "ideas", now_factory=lambda: FROZEN_NOW)


def proposed_idea(service: TradeIdeaService, decision_id: str = "trade-20260612-001"):
    attest_account_equity(service)
    idea = build_trade_idea(decision_id=decision_id)
    service.propose(idea, actor_id="idea-generator-v1")
    return idea


class TestCheckApproval:
    def test_admits_eligible_idea_for_human_actor(self, service: TradeIdeaService) -> None:
        idea = proposed_idea(service)

        check = service.kernel.check_approval(idea, actor_type=ActorType.HUMAN)

        assert check.admitted
        assert check.violations == ()
        assert check.decision_id == idea.decision_id
        assert check.actor_type is ActorType.HUMAN
        assert check.action is AuditAction.APPROVED
        assert check.budget is not None
        assert check.budget_context is not None
        assert check.candidate_max_loss_pct == idea.max_loss.percent_of_account

    def test_denies_system_actor_in_human_approved_mode(self, service: TradeIdeaService) -> None:
        idea = proposed_idea(service)

        check = service.kernel.check_approval(idea, actor_type=ActorType.SYSTEM)

        assert not check.admitted
        assert any("human_approved_execution" in violation for violation in check.violations)

    def test_matches_service_approval_violations(self, service: TradeIdeaService) -> None:
        """The kernel and the pre-existing violations surface judge identically."""
        attest_account_equity(service)
        ineligible = build_trade_idea(
            decision_id="trade-20260612-002",
            thesis=" ",
        )
        service.propose(ineligible, actor_id="idea-generator-v1")

        check = service.kernel.check_approval(ineligible, actor_type=ActorType.HUMAN)

        assert list(check.violations) == service.approval_violations(
            ineligible, actor_type=ActorType.HUMAN
        )
        assert not check.admitted

    def test_denial_evidence_lists_every_violation(self, service: TradeIdeaService) -> None:
        idea = proposed_idea(service)

        check = service.kernel.check_approval(idea, actor_type=ActorType.AI)

        evidence = check.denial_evidence()
        assert evidence[0].startswith("autonomy_state version ")
        assert f"approval_violations={len(check.violations)}" in evidence[1]
        assert all(f"violation: {violation}" in evidence for violation in check.violations)

    def test_admission_evidence_carries_budget_envelope(self, service: TradeIdeaService) -> None:
        idea = proposed_idea(service)

        check = service.kernel.check_approval(idea, actor_type=ActorType.HUMAN)

        evidence = check.admission_evidence()
        assert f"risk budget version {check.budget_version}: approval_violations=0" in evidence
        assert any(entry.startswith("budget envelope: ") for entry in evidence)
        assert any(
            f"candidate_max_loss_pct={idea.max_loss.percent_of_account}" in entry
            for entry in evidence
        )


class TestRecordOutcomes:
    def test_record_approval_appends_audited_event(self, service: TradeIdeaService) -> None:
        idea = proposed_idea(service)
        check = service.kernel.check_approval(idea, actor_type=ActorType.HUMAN)

        service.kernel.record_approval(idea, check, actor_id="rj", reason="Risk verified")

        view = service.get(idea.decision_id)
        assert view.state is TradeIdeaState.APPROVED
        approval = view.events[-1]
        assert approval.action is AuditAction.APPROVED
        assert approval.actor_type is ActorType.HUMAN
        assert approval.actor_id == "rj"

    def test_record_approval_refuses_denied_check(self, service: TradeIdeaService) -> None:
        idea = proposed_idea(service)
        check = service.kernel.check_approval(idea, actor_type=ActorType.AI)

        with pytest.raises(PolicyViolationError, match="the kernel denied it"):
            service.kernel.record_approval(idea, check, actor_id="bot", reason="nope")
        assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED

    def test_record_denied_approval_appends_skip_with_evidence(
        self, service: TradeIdeaService
    ) -> None:
        idea = proposed_idea(service)
        check = service.kernel.check_approval(idea, actor_type=ActorType.SYSTEM)

        service.kernel.record_denied_approval(
            idea, check, actor_id="auto-approval-sweep", reason="skipped"
        )

        view = service.get(idea.decision_id)
        assert view.state is TradeIdeaState.PROPOSED
        skip = view.events[-1]
        assert skip.action is AuditAction.AUTO_APPROVAL_SKIPPED
        assert skip.evidence == check.denial_evidence()

    def test_record_denied_approval_refuses_admitted_check(self, service: TradeIdeaService) -> None:
        idea = proposed_idea(service)
        check = service.kernel.check_approval(idea, actor_type=ActorType.HUMAN)

        with pytest.raises(PolicyViolationError, match="the kernel admitted it"):
            service.kernel.record_denied_approval(
                idea, check, actor_id="auto-approval-sweep", reason="skipped"
            )


class TestCheckExecution:
    def test_denies_outside_bounded_autonomy(self, service: TradeIdeaService) -> None:
        check = service.kernel.check_execution("trade-20260612-001", actor_type=ActorType.SYSTEM)

        assert not check.admitted
        assert any("bounded_autonomy" in violation for violation in check.violations)
        assert check.action is AuditAction.SUBMITTED
        assert check.budget is None
        assert check.budget_context is None

    def test_admits_under_bounded_autonomy(self, service: TradeIdeaService) -> None:
        attest_account_equity(service)
        service.set_autonomy_mode(
            AutonomyMode.BOUNDED_AUTONOMY,
            actor_type=ActorType.HUMAN,
            actor_id="rj",
            reason="Stage 2 exercised in tests",
        )

        check = service.kernel.check_execution("trade-20260612-001", actor_type=ActorType.SYSTEM)

        assert check.admitted
        assert check.autonomy.mode is AutonomyMode.BOUNDED_AUTONOMY

    def test_execution_check_has_no_approval_evidence(self, service: TradeIdeaService) -> None:
        check = service.kernel.check_execution("trade-20260612-001", actor_type=ActorType.SYSTEM)

        with pytest.raises(ValueError, match="requires an approval check"):
            check.admission_evidence()
        with pytest.raises(ValueError, match="requires an approval check"):
            check.denial_evidence()


def test_kernel_check_is_immutable(service: TradeIdeaService) -> None:
    idea = proposed_idea(service)
    check = service.kernel.check_approval(idea, actor_type=ActorType.HUMAN)

    assert isinstance(check, KernelCheck)
    with pytest.raises(AttributeError):
        check.violations = ()  # type: ignore[misc]
