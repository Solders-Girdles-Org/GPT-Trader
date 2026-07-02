from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    ApprovalPolicy,
    AutonomyMode,
    PolicyViolationError,
    RiskBudget,
)
from gpt_trader.features.trade_ideas.service import TradeIdeaService


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def test_budget_seeds_defaults_on_first_use(service: TradeIdeaService) -> None:
    assert service.current_budget() == DEFAULT_RISK_BUDGET


def test_human_can_renegotiate_budget(service: TradeIdeaService) -> None:
    widened = RiskBudget.from_dict(
        {**DEFAULT_RISK_BUDGET.to_dict(), "version": 2, "max_loss_per_idea_pct": "8"}
    )

    service.update_budget(widened, actor_type=ActorType.HUMAN, actor_id="rj")

    assert service.current_budget().max_loss_per_idea_pct == Decimal("8")


def test_agent_budget_change_refused_in_current_mode(service: TradeIdeaService) -> None:
    widened = RiskBudget.from_dict(
        {**DEFAULT_RISK_BUDGET.to_dict(), "version": 2, "max_loss_per_idea_pct": "8"}
    )

    with pytest.raises(PolicyViolationError):
        service.update_budget(widened, actor_type=ActorType.AI, actor_id="idea-generator-v1")

    assert service.current_budget() == DEFAULT_RISK_BUDGET


def test_agent_budget_change_refused_in_bounded_autonomy_until_meta_envelope(
    tmp_path: Path,
) -> None:
    service = TradeIdeaService(
        tmp_path / "trade_ideas",
        policy=ApprovalPolicy(AutonomyMode.BOUNDED_AUTONOMY),
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    widened = RiskBudget.from_dict(
        {**DEFAULT_RISK_BUDGET.to_dict(), "version": 2, "max_loss_per_idea_pct": "8"}
    )

    with pytest.raises(PolicyViolationError) as exc_info:
        service.update_budget(widened, actor_type=ActorType.AI, actor_id="idea-generator-v1")

    assert any("budget meta-envelope" in violation for violation in exc_info.value.violations)
    assert service.current_budget() == DEFAULT_RISK_BUDGET
