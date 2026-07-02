from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    ApprovalBudgetContext,
    ApprovalPolicy,
    RiskBudget,
    SizingRecommendation,
    TradeIdea,
)

NOW = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)


def test_daily_loss_budget_context_is_enforced(trade_idea: TradeIdea) -> None:
    policy = ApprovalPolicy()
    strict_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "max_daily_loss_pct": "2",
        }
    )

    found = policy.approval_violations(
        trade_idea,
        actor_type=ActorType.HUMAN,
        budget=strict_budget,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(
            same_day_realized_loss_pct=Decimal("0.75"),
            open_approved_at_risk_pct=Decimal("0.25"),
        ),
    )

    assert any("max_daily_loss_pct" in violation for violation in found)
    assert any(
        "projected daily loss exposure 2.5% exceeds limit 2%" in violation for violation in found
    )


def test_daily_loss_budget_requires_measurable_same_day_closeouts(
    trade_idea: TradeIdea,
) -> None:
    policy = ApprovalPolicy()

    found = policy.approval_violations(
        trade_idea,
        actor_type=ActorType.HUMAN,
        budget=DEFAULT_RISK_BUDGET,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(
            same_day_realized_loss_unavailable_count=1,
        ),
    )

    assert any(
        "same-day closeout budget exposure includes 1 closeout(s) without realized profit/loss"
        in violation
        for violation in found
    )


def test_daily_loss_budget_requires_measurable_open_max_loss(
    trade_idea: TradeIdea,
) -> None:
    policy = ApprovalPolicy()

    found = policy.approval_violations(
        trade_idea,
        actor_type=ActorType.HUMAN,
        budget=DEFAULT_RISK_BUDGET,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(open_at_risk_unavailable_count=1),
    )

    assert any(
        "open budget exposure includes 1 idea(s) without max_loss.percent_of_account" in violation
        for violation in found
    )


def test_open_notional_budget_context_is_enforced(trade_idea: TradeIdea) -> None:
    policy = ApprovalPolicy()
    strict_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "max_open_notional_pct": "50",
        }
    )

    found = policy.approval_violations(
        trade_idea,
        actor_type=ActorType.HUMAN,
        budget=strict_budget,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(
            open_notional=Decimal("4000"),
            account_equity_snapshot=Decimal("10000"),
        ),
    )

    assert any("max_open_notional_pct" in violation for violation in found)
    assert any(
        "projected open notional 100.75% exceeds limit 50%" in violation for violation in found
    )


def test_open_notional_budget_uses_absolute_notional_exposure() -> None:
    idea = build_trade_idea(
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("-0.1"),
            notional=Decimal("-6075"),
            rationale="Signed fixture notional still consumes exposure",
        )
    )
    policy = ApprovalPolicy()
    strict_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "max_open_notional_pct": "50",
        }
    )

    found = policy.approval_violations(
        idea,
        actor_type=ActorType.HUMAN,
        budget=strict_budget,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(
            open_notional=Decimal("-4000"),
            account_equity_snapshot=Decimal("10000"),
        ),
    )

    assert any(
        "projected open notional 100.75% exceeds limit 50%" in violation for violation in found
    )


def test_open_notional_budget_requires_candidate_notional() -> None:
    idea = build_trade_idea(
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("0.1"),
            notional=None,
            rationale="Missing notional cannot prove exposure budget compliance",
        )
    )
    policy = ApprovalPolicy()

    found = policy.approval_violations(
        idea,
        actor_type=ActorType.HUMAN,
        budget=DEFAULT_RISK_BUDGET,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(
            open_notional=Decimal("0"),
            account_equity_snapshot=Decimal("10000"),
        ),
    )

    assert any("sizing_recommendation.notional is required" in violation for violation in found)


def test_open_notional_budget_requires_measurable_open_exposure(
    trade_idea: TradeIdea,
) -> None:
    policy = ApprovalPolicy()

    found = policy.approval_violations(
        trade_idea,
        actor_type=ActorType.HUMAN,
        budget=DEFAULT_RISK_BUDGET,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(
            open_notional=Decimal("0"),
            open_notional_unavailable_count=1,
            account_equity_snapshot=Decimal("10000"),
        ),
    )

    assert any(
        "open budget exposure includes 1 idea(s) without sizing_recommendation.notional"
        in violation
        for violation in found
    )


def test_open_notional_budget_requires_positive_equity_snapshot(trade_idea: TradeIdea) -> None:
    policy = ApprovalPolicy()

    found = policy.approval_violations(
        trade_idea,
        actor_type=ActorType.HUMAN,
        budget=DEFAULT_RISK_BUDGET,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(account_equity_snapshot=Decimal("0")),
    )

    assert any("account_equity_snapshot must be positive" in violation for violation in found)
