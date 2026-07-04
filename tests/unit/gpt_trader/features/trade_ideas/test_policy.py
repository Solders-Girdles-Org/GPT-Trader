from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    ApprovalPolicy,
    AutonomyMode,
    MaxLoss,
    ProductType,
    RiskBudget,
    TradeDirection,
    TradeIdea,
)

NOW = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)


def violations(
    idea: TradeIdea,
    *,
    policy: ApprovalPolicy | None = None,
    actor_type: ActorType = ActorType.HUMAN,
    budget: RiskBudget = DEFAULT_RISK_BUDGET,
    open_approved_count: int = 0,
) -> list[str]:
    active = policy or ApprovalPolicy()
    return active.approval_violations(
        idea,
        actor_type=actor_type,
        budget=budget,
        open_approved_count=open_approved_count,
        now=NOW,
    )


def test_eligible_idea_with_human_approver_passes(trade_idea: TradeIdea) -> None:
    assert violations(trade_idea) == []


def test_ai_actor_cannot_approve_in_human_approved_mode(trade_idea: TradeIdea) -> None:
    found = violations(trade_idea, actor_type=ActorType.AI)

    assert any("requires a human approver" in violation for violation in found)


def test_research_only_mode_blocks_all_approvals(trade_idea: TradeIdea) -> None:
    policy = ApprovalPolicy(AutonomyMode.RESEARCH_ONLY)

    found = violations(trade_idea, policy=policy)

    assert any("research_only" in violation for violation in found)


def test_bounded_autonomy_refuses_ai_and_venue_approvals(
    trade_idea: TradeIdea,
) -> None:
    policy = ApprovalPolicy(AutonomyMode.BOUNDED_AUTONOMY)

    for actor_type in (ActorType.AI, ActorType.VENUE):
        found = violations(trade_idea, policy=policy, actor_type=actor_type)

        assert found == [
            "Autonomy mode 'bounded_autonomy' permits only human or system "
            "approvals inside the budget envelope "
            "(docs/decisions/stage2-auto-approval-workflow.md); "
            f"got actor_type '{actor_type.value}'"
        ]


def test_bounded_autonomy_human_approval_still_uses_existing_gates(
    trade_idea: TradeIdea,
) -> None:
    policy = ApprovalPolicy(AutonomyMode.BOUNDED_AUTONOMY)

    assert violations(trade_idea, policy=policy, actor_type=ActorType.HUMAN) == []


def test_bounded_autonomy_system_approval_passes_the_same_gates(
    trade_idea: TradeIdea,
) -> None:
    """The Stage 2 exception: system approvals clear, subject to every check."""
    policy = ApprovalPolicy(AutonomyMode.BOUNDED_AUTONOMY)

    assert violations(trade_idea, policy=policy, actor_type=ActorType.SYSTEM) == []

    over_budget = build_trade_idea(
        max_loss=MaxLoss(amount=Decimal("900"), percent_of_account=Decimal("9"))
    )
    found = violations(over_budget, policy=policy, actor_type=ActorType.SYSTEM)
    assert any("exceeds budget cap" in violation for violation in found)


def test_system_approval_outside_bounded_autonomy_is_still_refused(
    trade_idea: TradeIdea,
) -> None:
    found = violations(trade_idea, actor_type=ActorType.SYSTEM)

    assert any("requires a human approver" in violation for violation in found)


def test_ineligible_idea_cannot_be_approved() -> None:
    idea = build_trade_idea(invalidation="")

    assert any("invalidation" in violation for violation in violations(idea))


def test_max_loss_above_budget_cap_is_refused() -> None:
    idea = build_trade_idea(
        max_loss=MaxLoss(amount=Decimal("900"), percent_of_account=Decimal("9"))
    )

    found = violations(idea)

    assert any("exceeds budget cap" in violation for violation in found)


def test_futures_leverage_requires_budget_permission() -> None:
    idea = build_trade_idea(product_type=ProductType.FUTURES)

    found = violations(idea)

    assert found == ["product_type futures requires risk budget allow_futures_leverage=true"]


def test_futures_leverage_passes_when_budget_allows_it() -> None:
    idea = build_trade_idea(product_type=ProductType.FUTURES)
    allowed_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "allow_futures_leverage": True,
        }
    )

    assert violations(idea, budget=allowed_budget) == []


def test_short_direction_requires_budget_permission_by_default() -> None:
    idea = build_trade_idea(direction=TradeDirection.SHORT)

    found = violations(idea)

    assert found == ["direction short requires risk budget allow_naked_shorts=true"]


def test_short_direction_requires_budget_permission_when_explicitly_disabled() -> None:
    idea = build_trade_idea(direction=TradeDirection.SHORT)
    disabled_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "allow_naked_shorts": False,
        }
    )

    found = violations(idea, budget=disabled_budget)

    assert found == ["direction short requires risk budget allow_naked_shorts=true"]


def test_short_direction_passes_when_budget_allows_naked_shorts() -> None:
    idea = build_trade_idea(direction=TradeDirection.SHORT)
    allowed_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "allow_naked_shorts": True,
        }
    )

    assert violations(idea, budget=allowed_budget) == []


def test_missing_percent_cannot_be_verified() -> None:
    idea = build_trade_idea(max_loss=MaxLoss(amount=Decimal("250")))

    found = violations(idea)

    assert any("percent_of_account is required" in violation for violation in found)


def test_concurrent_approved_cap_is_enforced(trade_idea: TradeIdea) -> None:
    found = violations(
        trade_idea,
        open_approved_count=DEFAULT_RISK_BUDGET.max_concurrent_approved_tickets,
    )

    assert any("concurrent approved tickets" in violation for violation in found)


def test_stale_idea_cannot_be_approved(trade_idea: TradeIdea) -> None:
    policy = ApprovalPolicy()

    found = policy.approval_violations(
        trade_idea,
        actor_type=ActorType.HUMAN,
        budget=DEFAULT_RISK_BUDGET,
        open_approved_count=0,
        now=datetime(2026, 7, 1, tzinfo=UTC),
    )

    assert any("expired" in violation for violation in found)


def test_review_latency_budget_blocks_stale_proposed_idea(trade_idea: TradeIdea) -> None:
    policy = ApprovalPolicy()
    strict_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "max_review_latency_hours": 1,
        }
    )

    found = policy.approval_violations(
        trade_idea,
        actor_type=ActorType.HUMAN,
        budget=strict_budget,
        open_approved_count=0,
        now=NOW,
        review_started_at=NOW - timedelta(hours=2),
    )

    assert any("review deadline expired" in violation for violation in found)


def test_budget_changes_require_human_below_bounded_autonomy() -> None:
    policy = ApprovalPolicy()

    assert policy.budget_change_violations(ActorType.HUMAN) == []
    assert policy.budget_change_violations(ActorType.AI) != []


def test_bounded_autonomy_non_human_budget_changes_fail_closed_until_meta_envelope() -> None:
    policy = ApprovalPolicy(AutonomyMode.BOUNDED_AUTONOMY)

    assert policy.budget_change_violations(ActorType.HUMAN) == []

    for actor_type in (ActorType.AI, ActorType.SYSTEM, ActorType.VENUE):
        found = policy.budget_change_violations(actor_type)

        assert found == [
            "Autonomy mode 'bounded_autonomy' does not permit non-human budget changes "
            "until a budget meta-envelope is modeled or a later decision packet scopes "
            f"a narrower exception; got actor_type '{actor_type.value}'"
        ]
