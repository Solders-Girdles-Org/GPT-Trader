"""Runtime risk kernel: the one gate every execution path consults per decision.

Implements the kernel extraction from
docs/decisions/adopt-event-driven-execution-topology.md (#1189): the rails —
budget envelope, audited autonomy state (with the automatic ratchet),
eligibility invariants, audit append — are exposed as a single in-process,
synchronous entry point that admits or denies an (idea, action) pair and
records the audited event for the check. The ticket queue and approval sweep
are one client of this kernel (the human-review client); the paper-execution
lane is another. The kernel gates decisions, it never proposes
(no-second-proposer-brain rule).

Kernel checks are library calls with identity stamping: every check carries
the actor type it was evaluated for, and every recorded outcome lands on the
append-only audit log. Cross-process safety comes from the locked append
primitives underneath (budget/autonomy logs); the kernel adds no locking of
its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from gpt_trader.features.trade_ideas.audit import ActorType, AuditAction
from gpt_trader.features.trade_ideas.autonomy import (
    AUTONOMY_SOURCE_FAIL_CLOSED,
    AutonomyResolution,
)
from gpt_trader.features.trade_ideas.budget import RiskBudget
from gpt_trader.features.trade_ideas.models import AutonomyMode, TradeIdea
from gpt_trader.features.trade_ideas.policy import (
    ApprovalBudgetContext,
    ApprovalPolicy,
    PolicyViolationError,
)
from gpt_trader.features.trade_ideas.workflow import TradeIdeaState


def autonomy_resolution_violations(resolution: AutonomyResolution) -> list[str]:
    """Return the violation raised when autonomy resolution failed closed."""
    if resolution.source != AUTONOMY_SOURCE_FAIL_CLOSED:
        return []
    return [
        "autonomy state resolution failed closed to "
        f"'{resolution.mode.value}': {resolution.error}"
    ]


class KernelRuntime(Protocol):
    """Audited state a kernel check consults, implemented by ``TradeIdeaService``.

    The kernel owns no storage: budget, autonomy, exposure, and audit append
    all resolve through the same logs the workflow clients already share, so
    a kernel admit/deny is always evaluated against — and recorded on — the
    single source of truth.
    """

    def current_budget(self) -> RiskBudget: ...

    def approval_budget_context(
        self,
        *,
        exclude_decision_id: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalBudgetContext: ...

    def open_approved_count(self) -> int: ...

    def decision_autonomy(
        self,
        *,
        now: datetime,
        budget: RiskBudget | None = None,
        budget_context: ApprovalBudgetContext | None = None,
    ) -> AutonomyResolution: ...

    def review_started_at(self, decision_id: str) -> datetime | None: ...

    def append_audit(
        self,
        idea: TradeIdea,
        *,
        action: AuditAction,
        after_state: TradeIdeaState,
        actor_type: ActorType,
        actor_id: str,
        reason: str,
        evidence: tuple[str, ...] = (),
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class KernelCheck:
    """Admit/deny outcome of one kernel check, with the inputs it was judged on.

    ``budget`` and ``budget_context`` are populated for approval checks;
    execution checks resolve autonomy only and leave them ``None`` so a
    render- or execution-path check never seeds the budget log as a side
    effect.
    """

    action: AuditAction
    decision_id: str
    actor_type: ActorType
    evaluated_at: datetime
    autonomy: AutonomyResolution
    violations: tuple[str, ...]
    budget: RiskBudget | None = None
    budget_context: ApprovalBudgetContext | None = None
    candidate_max_loss_pct: Decimal | None = None

    @property
    def admitted(self) -> bool:
        return not self.violations

    def _autonomy_evidence(self) -> str:
        return (
            f"autonomy_state version {self.autonomy.version} "
            f"mode={self.autonomy.mode.value} "
            f"(source={self.autonomy.source})"
        )

    def _require_approval_inputs(self) -> tuple[RiskBudget, ApprovalBudgetContext]:
        if self.budget is None or self.budget_context is None:
            raise ValueError(
                "Evidence for an approval outcome requires an approval check; "
                f"this check gated action '{self.action.value}' without budget inputs"
            )
        return self.budget, self.budget_context

    @property
    def budget_version(self) -> int:
        """Version of the budget this approval check was judged against."""
        budget, _ = self._require_approval_inputs()
        return budget.version

    def admission_evidence(self) -> tuple[str, ...]:
        """Evidence recorded with a system approval admitted by this check."""
        budget, budget_context = self._require_approval_inputs()
        return (
            self._autonomy_evidence(),
            f"risk budget version {budget.version}: approval_violations=0",
            "budget envelope: "
            f"same_day_realized_loss_pct={budget_context.same_day_realized_loss_pct} "
            f"open_approved_at_risk_pct={budget_context.open_approved_at_risk_pct} "
            f"candidate_max_loss_pct={self.candidate_max_loss_pct} "
            f"max_daily_loss_pct={budget.max_daily_loss_pct}",
        )

    def denial_evidence(self) -> tuple[str, ...]:
        """Evidence recorded when a denied check is audited rather than raised."""
        budget, _ = self._require_approval_inputs()
        return (
            self._autonomy_evidence(),
            f"risk budget version {budget.version}: " f"approval_violations={len(self.violations)}",
            *(f"violation: {violation}" for violation in self.violations),
        )

    def execution_denial_evidence(self) -> tuple[str, ...]:
        """Evidence recorded when a denied execution check is audited.

        Execution checks carry no budget inputs (exposure was gated at
        approval), so the evidence is the autonomy resolution the denial was
        judged on plus every violation.
        """
        return (
            self._autonomy_evidence(),
            *(f"violation: {violation}" for violation in self.violations),
        )


class RiskKernel:
    """In-process gate that admits or denies an (idea, action) pair per decision.

    Checks are synchronous library calls: resolve the envelope and the
    decision-time autonomy mode (applying the automatic ratchet), evaluate the
    approval policy, and return every violation. Recording an outcome appends
    the audited event through the same append the workflow clients use.
    """

    def __init__(
        self,
        runtime: KernelRuntime,
        *,
        now_factory: Callable[[], datetime],
    ) -> None:
        self._runtime = runtime
        self._now = now_factory

    def check_approval(
        self,
        idea: TradeIdea,
        *,
        actor_type: ActorType,
        now: datetime | None = None,
    ) -> KernelCheck:
        """Gate one approval decision against budget + autonomy + eligibility."""
        evaluated_at = now or self._now()
        budget = self._runtime.current_budget()
        budget_context = self._runtime.approval_budget_context(
            exclude_decision_id=idea.decision_id,
            now=evaluated_at,
        )
        resolution = self._runtime.decision_autonomy(
            now=evaluated_at,
            budget=budget,
            budget_context=budget_context,
        )
        policy = ApprovalPolicy(resolution.mode)
        violations = (
            *autonomy_resolution_violations(resolution),
            *policy.approval_violations(
                idea,
                actor_type=actor_type,
                budget=budget,
                open_approved_count=self._runtime.open_approved_count(),
                now=evaluated_at,
                review_started_at=self._runtime.review_started_at(idea.decision_id),
                budget_context=budget_context,
            ),
        )
        return KernelCheck(
            action=AuditAction.APPROVED,
            decision_id=idea.decision_id,
            actor_type=actor_type,
            evaluated_at=evaluated_at,
            autonomy=resolution,
            violations=violations,
            budget=budget,
            budget_context=budget_context,
            candidate_max_loss_pct=idea.max_loss.percent_of_account,
        )

    def check_execution(
        self,
        decision_id: str,
        *,
        actor_type: ActorType,
        now: datetime | None = None,
    ) -> KernelCheck:
        """Gate one system execution decision by re-resolving autonomy at execution time.

        Generalizes the Stage 2 execution gate (#1177): the audited autonomy
        mode is re-resolved — ratchet applied — at the moment of execution, so
        an approval granted under ``bounded_autonomy`` cannot be executed
        after the level ratcheted down. The check itself carries no budget
        envelope inputs (budget exposure was gated at approval); the ratchet
        may still consult the active budget, exactly as at any decision
        boundary.
        """
        evaluated_at = now or self._now()
        resolution = self._runtime.decision_autonomy(now=evaluated_at)
        violations = list(autonomy_resolution_violations(resolution))
        if resolution.mode is not AutonomyMode.BOUNDED_AUTONOMY:
            violations.append(
                "system-approved execution requires audited autonomy mode "
                f"'{AutonomyMode.BOUNDED_AUTONOMY.value}'; resolved "
                f"'{resolution.mode.value}' (source={resolution.source})"
            )
        return KernelCheck(
            action=AuditAction.SUBMITTED,
            decision_id=decision_id,
            actor_type=actor_type,
            evaluated_at=evaluated_at,
            autonomy=resolution,
            violations=tuple(violations),
        )

    def record_approval(
        self,
        idea: TradeIdea,
        check: KernelCheck,
        *,
        actor_id: str,
        reason: str,
        evidence: tuple[str, ...] = (),
    ) -> None:
        """Append the audited APPROVED event for an admitted approval check."""
        if not check.admitted:
            raise PolicyViolationError(
                f"Cannot record approval of '{check.decision_id}': the kernel "
                "denied it: " + "; ".join(check.violations),
                list(check.violations),
            )
        self._runtime.append_audit(
            idea,
            action=AuditAction.APPROVED,
            after_state=TradeIdeaState.APPROVED,
            actor_type=check.actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence=evidence,
        )

    def record_denied_approval(
        self,
        idea: TradeIdea,
        check: KernelCheck,
        *,
        actor_id: str,
        reason: str,
    ) -> None:
        """Append the audited AUTO_APPROVAL_SKIPPED event for a denied check.

        Used by clients that record denials instead of raising (the Stage 2
        sweep); the idea stays ``proposed`` for human review with every
        violation on the audit trail — never silently dropped.
        """
        if check.admitted:
            raise PolicyViolationError(
                f"Cannot record a denial for '{check.decision_id}': the kernel " "admitted it",
                [],
            )
        self._runtime.append_audit(
            idea,
            action=AuditAction.AUTO_APPROVAL_SKIPPED,
            after_state=TradeIdeaState.PROPOSED,
            actor_type=check.actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence=check.denial_evidence(),
        )

    def record_denied_execution(
        self,
        idea: TradeIdea,
        check: KernelCheck,
        *,
        actor_id: str,
        reason: str,
    ) -> None:
        """Append the audited AUTO_EXECUTION_SKIPPED event for a denied check.

        Used by execution clients that record denials instead of raising (the
        event-driven lane, #1191): the idea stays ``approved`` — the denial is
        an audited no-op, so a mid-stream ratchet-down or kill-switch leaves
        evidence on the idea-level trail instead of vanishing into a log line.
        """
        if check.admitted:
            raise PolicyViolationError(
                f"Cannot record an execution denial for '{check.decision_id}': "
                "the kernel admitted it",
                [],
            )
        self._runtime.append_audit(
            idea,
            action=AuditAction.AUTO_EXECUTION_SKIPPED,
            after_state=TradeIdeaState.APPROVED,
            actor_type=check.actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence=check.execution_denial_evidence(),
        )


__all__ = [
    "KernelCheck",
    "KernelRuntime",
    "RiskKernel",
    "autonomy_resolution_violations",
]
