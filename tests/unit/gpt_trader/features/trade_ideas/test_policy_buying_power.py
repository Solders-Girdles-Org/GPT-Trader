"""Cash-account buying-power dimension of the risk budget (#1231).

Equity ideas must not be approvable beyond what a cash account could fund
given settlement; crypto-spot approval outcomes are pinned unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

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

# The lever is unconfigured in the seeded default; CONFIGURED versions in
# cash-account fidelity (100% of attested equity is spendable buying power).
CONFIGURED = replace(
    DEFAULT_RISK_BUDGET,
    max_equity_buying_power_pct=Decimal("100"),
    reason="Configure cash-account buying power for equities",
)
UNCONFIGURED = DEFAULT_RISK_BUDGET


def equity_idea(**overrides: Any) -> TradeIdea:
    fields: dict[str, Any] = {
        "instrument": "AAPL",
        "thesis": "Earnings momentum with sector breadth confirmation",
        "data_used": ("provider:candles:AAPL:1d:2026-06-11",),
    }
    fields.update(overrides)
    return build_trade_idea(**fields)


def violations(
    idea: TradeIdea,
    budget: RiskBudget = CONFIGURED,
    **context_overrides: Any,
) -> list[str]:
    context_fields: dict[str, Any] = {"account_equity_snapshot": Decimal("10000")}
    context_fields.update(context_overrides)
    return ApprovalPolicy().approval_violations(
        idea,
        actor_type=ActorType.HUMAN,
        budget=budget,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(**context_fields),
    )


def test_open_equity_exposure_plus_candidate_breaches_buying_power() -> None:
    found = violations(equity_idea(), open_equity_notional=Decimal("4000"))

    assert any(
        "max_equity_buying_power_pct budget breached: projected equity "
        "buying-power usage 100.75% exceeds limit 100%" in violation
        for violation in found
    )


def test_unsettled_equity_proceeds_consume_buying_power() -> None:
    # 6075 candidate fits attested equity alone, but 4000 of same-day sale
    # proceeds are still unsettled: a cash account cannot fund the buy.
    found = violations(equity_idea(), unsettled_equity_proceeds=Decimal("4000"))

    assert any(
        "unsettled_equity_proceeds=4000" in violation and "max_equity_buying_power_pct" in violation
        for violation in found
    )


def test_equity_candidate_within_settled_cash_is_not_flagged() -> None:
    found = violations(equity_idea())

    assert not any("max_equity_buying_power_pct" in violation for violation in found)


def test_unconfigured_lever_adds_no_buying_power_violations() -> None:
    # None = not configured: the pre-existing checks are the entire story.
    found = violations(
        equity_idea(),
        budget=UNCONFIGURED,
        open_equity_notional=Decimal("9000"),
        unsettled_equity_proceeds=Decimal("9000"),
    )

    assert not any("max_equity_buying_power_pct" in violation for violation in found)


def test_missing_equity_snapshot_reports_cannot_verify_not_silence() -> None:
    found = ApprovalPolicy().approval_violations(
        equity_idea(),
        actor_type=ActorType.HUMAN,
        budget=CONFIGURED,
        open_approved_count=0,
        now=NOW,
        budget_context=ApprovalBudgetContext(account_equity_snapshot=None),
    )

    assert any(
        "account_equity_snapshot is required to verify "
        "max_equity_buying_power_pct budget exposure" in violation
        for violation in found
    )


def test_non_positive_equity_snapshot_reports_cannot_verify() -> None:
    found = violations(equity_idea(), account_equity_snapshot=Decimal("0"))

    assert any(
        "account_equity_snapshot must be positive to verify "
        "max_equity_buying_power_pct budget exposure" in violation
        for violation in found
    )


def test_unverifiable_open_equity_exposure_is_refused_loudly() -> None:
    found = violations(equity_idea(), open_equity_notional_unavailable_count=2)

    assert any(
        "2 idea(s) whose equity notional cannot be verified" in violation for violation in found
    )


def test_unverifiable_unsettled_proceeds_are_refused_loudly() -> None:
    found = violations(equity_idea(), unsettled_equity_proceeds_unavailable_count=1)

    assert any(
        "1 closeout(s) whose sale proceeds cannot be verified" in violation for violation in found
    )


def test_equity_candidate_without_notional_cannot_verify_buying_power() -> None:
    idea = equity_idea(
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("10"),
            notional=None,
            rationale="Missing notional cannot prove buying-power compliance",
        )
    )

    found = violations(idea)

    assert any(
        "sizing_recommendation.notional is required to verify "
        "max_equity_buying_power_pct budget exposure" in violation
        for violation in found
    )


def test_unclassifiable_instrument_is_refused_loudly_when_lever_is_set() -> None:
    found = violations(build_trade_idea(instrument="BRK.B"))

    assert any(
        "instrument cannot be classified to verify max_equity_buying_power_pct" in violation
        for violation in found
    )


def test_crypto_spot_approval_outcomes_are_pinned_unchanged() -> None:
    # Representative crypto-spot candidates must evaluate identically whether
    # the buying-power lever is configured or absent — even against hostile
    # equity aggregates, which crypto never consumes.
    candidates = (
        build_trade_idea(),
        build_trade_idea(
            instrument="ETH-USD",
            sizing_recommendation=SizingRecommendation(
                quantity=Decimal("2"),
                notional=Decimal("9500"),
                rationale="Near the notional cap but inside it",
            ),
        ),
        build_trade_idea(
            instrument="SOL-USDC",
            sizing_recommendation=SizingRecommendation(
                quantity=Decimal("100"),
                notional=Decimal("20000"),
                rationale="Breaches max_open_notional_pct either way",
            ),
        ),
        build_trade_idea(
            instrument="BTC-USD",
            sizing_recommendation=SizingRecommendation(
                quantity=Decimal("0.1"),
                notional=None,
                rationale="Missing notional fails the notional check either way",
            ),
        ),
    )

    for candidate in candidates:
        with_lever = violations(
            candidate,
            budget=CONFIGURED,
            open_equity_notional=Decimal("9999"),
            unsettled_equity_proceeds=Decimal("9999"),
            open_equity_notional_unavailable_count=3,
            unsettled_equity_proceeds_unavailable_count=3,
        )
        without_lever = violations(
            candidate,
            budget=UNCONFIGURED,
            open_equity_notional=Decimal("9999"),
            unsettled_equity_proceeds=Decimal("9999"),
            open_equity_notional_unavailable_count=3,
            unsettled_equity_proceeds_unavailable_count=3,
        )

        assert with_lever == without_lever
        assert not any("max_equity_buying_power_pct" in violation for violation in with_lever)
