"""Eligibility split: invariant vs mode-dependent constraints (#1190).

Invariant checks apply identically at every autonomy level; review-latency
survivability applies only when a human review loop is in the decision path
(every mode except ``bounded_autonomy``). Rejection reasons carry their
constraint-class prefix so the audit trail distinguishes "unsound idea" from
"too fast for a human".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    INVARIANT_ELIGIBILITY_PREFIX,
    MODE_DEPENDENT_ELIGIBILITY_PREFIX,
    ActorType,
    ApprovalPolicy,
    AutonomyMode,
    TradeIdeaService,
    TradeIdeaState,
)

START = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
FAR_EXPIRY = datetime(2026, 6, 19, 16, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(START)


@pytest.fixture
def service(tmp_path: Path, clock: MutableClock) -> TradeIdeaService:
    return TradeIdeaService(tmp_path / "ideas", now_factory=clock)


def enable_bounded_autonomy(service: TradeIdeaService) -> None:
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Stage 2 exercised in tests",
    )


def tighten_review_latency(service: TradeIdeaService, hours: int = 1) -> None:
    current = service.current_budget()
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            max_review_latency_hours=hours,
            reason="Short review window for latency tests",
        ),
        ActorType.HUMAN,
        "rj",
    )


class TestInvariantClass:
    def test_invariant_checks_apply_in_every_mode(self) -> None:
        """The same invariant rejections surface no matter the autonomy mode."""
        ineligible = build_trade_idea(thesis=" ", invalidation=" ")
        expected = {
            f"{INVARIANT_ELIGIBILITY_PREFIX}Missing thesis: "
            "no plain-language reason the trade exists",
            f"{INVARIANT_ELIGIBILITY_PREFIX}Missing invalidation: "
            "no level or condition that makes the thesis false",
        }
        for mode, actor_type in (
            (AutonomyMode.HUMAN_APPROVED_EXECUTION, ActorType.HUMAN),
            (AutonomyMode.BOUNDED_AUTONOMY, ActorType.SYSTEM),
            (AutonomyMode.RESEARCH_ONLY, ActorType.HUMAN),
        ):
            violations = ApprovalPolicy(mode).approval_violations(
                ineligible,
                actor_type=actor_type,
                budget=DEFAULT_RISK_BUDGET,
                open_approved_count=0,
                now=START,
            )
            assert expected <= set(violations), f"mode={mode.value}"

    def test_invariant_reasons_carry_class_prefix(self, service: TradeIdeaService) -> None:
        attest_account_equity(service)
        ineligible = build_trade_idea(failure_mode=" ")
        service.propose(ineligible, actor_id="idea-generator-v1")

        check = service.kernel.check_approval(ineligible, actor_type=ActorType.HUMAN)

        assert any(
            violation.startswith(INVARIANT_ELIGIBILITY_PREFIX) for violation in check.violations
        )


class TestModeDependentClass:
    def test_latency_violation_in_human_mode_carries_class_prefix(self) -> None:
        policy = ApprovalPolicy(AutonomyMode.HUMAN_APPROVED_EXECUTION)
        assert policy.review_latency_applies
        violation = policy.review_latency_violation(
            review_started_at=START - timedelta(hours=2),
            budget=replace(DEFAULT_RISK_BUDGET, max_review_latency_hours=1),
            now=START,
        )
        assert violation is not None
        assert violation.startswith(MODE_DEPENDENT_ELIGIBILITY_PREFIX)
        assert "review deadline expired" in violation

    def test_latency_never_violates_under_bounded_autonomy(self) -> None:
        policy = ApprovalPolicy(AutonomyMode.BOUNDED_AUTONOMY)
        assert not policy.review_latency_applies
        violation = policy.review_latency_violation(
            review_started_at=START - timedelta(hours=200),
            budget=replace(DEFAULT_RISK_BUDGET, max_review_latency_hours=1),
            now=START,
        )
        assert violation is None

    def test_latency_applies_in_fail_closed_research_only(self) -> None:
        """The conservative direction: an integrity-broken log keeps the constraint."""
        assert ApprovalPolicy(AutonomyMode.RESEARCH_ONLY).review_latency_applies


class TestExpirySweepAcrossModes:
    def prepare_stale_review(self, service: TradeIdeaService, clock: MutableClock) -> str:
        """Propose an idea, tighten the review window, and let it elapse."""
        attest_account_equity(service)
        tighten_review_latency(service, hours=1)
        idea = build_trade_idea()
        service.propose(idea, actor_id="idea-generator-v1")
        clock.advance(hours=2)
        return idea.decision_id

    def test_sweep_expires_stale_review_in_human_mode(
        self, service: TradeIdeaService, clock: MutableClock
    ) -> None:
        decision_id = self.prepare_stale_review(service, clock)

        expired = service.expire_due_ideas()

        assert [view.idea.decision_id for view in expired] == [decision_id]

    def test_sweep_keeps_stale_review_under_bounded_autonomy(
        self, service: TradeIdeaService, clock: MutableClock
    ) -> None:
        enable_bounded_autonomy(service)
        decision_id = self.prepare_stale_review(service, clock)

        assert service.expire_due_ideas() == []
        assert service.get(decision_id).state is TradeIdeaState.PROPOSED
        # The idea's own expiry stays invariant: once it passes, the sweep acts.
        clock.now = FAR_EXPIRY + timedelta(minutes=1)
        assert [view.idea.decision_id for view in service.expire_due_ideas()] == [decision_id]

    def test_mode_drop_reinstates_latency_expiry(
        self, service: TradeIdeaService, clock: MutableClock
    ) -> None:
        """Ratchet-down mid-lifecycle: proposed under bounded, mode drops later."""
        enable_bounded_autonomy(service)
        decision_id = self.prepare_stale_review(service, clock)
        assert service.expire_due_ideas() == []

        # Lowering is open to any actor — this is the ratchet's path.
        service.set_autonomy_mode(
            AutonomyMode.HUMAN_APPROVED_EXECUTION,
            actor_type=ActorType.SYSTEM,
            actor_id="autonomy-ratchet",
            reason="Simulated ratchet-down",
        )

        expired = service.expire_due_ideas()
        assert [view.idea.decision_id for view in expired] == [decision_id]


class TestApprovalAcrossModeTransitions:
    def test_ratchet_down_before_execution_reinstates_latency_check(
        self, service: TradeIdeaService, clock: MutableClock
    ) -> None:
        """An idea admitted under bounded autonomy is re-judged after the drop."""
        enable_bounded_autonomy(service)
        attest_account_equity(service)
        tighten_review_latency(service, hours=1)
        idea = build_trade_idea()
        service.propose(idea, actor_id="idea-generator-v1")
        clock.advance(hours=2)

        under_bounded = service.kernel.check_approval(idea, actor_type=ActorType.SYSTEM)
        assert under_bounded.admitted

        service.set_autonomy_mode(
            AutonomyMode.HUMAN_APPROVED_EXECUTION,
            actor_type=ActorType.SYSTEM,
            actor_id="autonomy-ratchet",
            reason="Simulated ratchet-down",
        )

        after_drop = service.kernel.check_approval(idea, actor_type=ActorType.HUMAN)
        assert not after_drop.admitted
        assert any(
            violation.startswith(MODE_DEPENDENT_ELIGIBILITY_PREFIX)
            for violation in after_drop.violations
        )


class TestQueueStatusAcrossModes:
    def test_review_latency_warning_only_when_mode_enforces_it(
        self, service: TradeIdeaService, clock: MutableClock
    ) -> None:
        attest_account_equity(service)
        tighten_review_latency(service, hours=1)
        idea = build_trade_idea()
        service.propose(idea, actor_id="idea-generator-v1")

        in_human_mode = service.queue_status(warning_window_hours=24)
        assert [expiration.deadline_type for expiration in in_human_mode.upcoming_expirations] == [
            "review_latency"
        ]

        enable_bounded_autonomy(service)
        under_bounded = service.queue_status(warning_window_hours=24)
        assert all(
            expiration.deadline_type != "review_latency"
            for expiration in under_bounded.upcoming_expirations
        )
