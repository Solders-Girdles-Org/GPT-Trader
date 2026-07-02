from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    MaxLoss,
    PolicyViolationError,
    RiskBudget,
    TradeIdeaState,
)
from gpt_trader.features.trade_ideas.service import TradeIdeaService


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def test_open_notional_budget_uses_existing_exposure_equity_before_candidate(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    first = build_trade_idea(
        decision_id="trade-20260612-open-small-equity",
        max_loss=MaxLoss(amount=Decimal("100"), percent_of_account=Decimal("1")),
    )
    service.propose(first, actor_id="idea-generator-v1")
    service.approve(first.decision_id, actor_id="rj", reason="Risk verified")
    strict_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 3,
            "max_open_notional_pct": "100",
        }
    )
    service.update_budget(strict_budget, actor_type=ActorType.HUMAN, actor_id="rj")
    candidate = build_trade_idea(
        decision_id="trade-20260612-candidate-large-equity",
        max_loss=MaxLoss(amount=Decimal("1000"), percent_of_account=Decimal("1")),
    )
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "projected open notional 121.5% exceeds limit 100%" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_approval_fails_closed_without_independent_equity_source(
    service: TradeIdeaService,
) -> None:
    # No attested budget equity and no independent records: the candidate's own
    # max_loss pair must never supply the notional-cap denominator.
    candidate = build_trade_idea(decision_id="trade-20260612-bootstrap-candidate")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "account_equity_snapshot is required" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_approval_budget_context_is_read_only(
    service: TradeIdeaService,
) -> None:
    # Building a budget context (used by render-only ticket exports) must not
    # seed risk_budget.jsonl on a root that has never negotiated a budget.
    context = service.approval_budget_context()

    assert context.account_equity_snapshot is None
    assert not (service.audit_log.path.parent / "risk_budget.jsonl").exists()


def test_attested_budget_equity_overrides_record_inferred_equity(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service, equity=Decimal("10000"))
    first = build_trade_idea(decision_id="trade-20260612-attested-open")
    service.propose(first, actor_id="idea-generator-v1")
    service.approve(first.decision_id, actor_id="rj", reason="Risk verified")
    candidate = build_trade_idea(decision_id="trade-20260612-attested-candidate")
    service.propose(candidate, actor_id="idea-generator-v1")

    # Attested equity 10000 with a 100% cap: 6075 open + 6075 candidate = 121.5%.
    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any("max_open_notional_pct" in violation for violation in exc_info.value.violations)
    assert any(
        "account_equity_snapshot=10000" in violation for violation in exc_info.value.violations
    )
