from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AutonomyMode,
    BudgetIntegrityError,
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


def test_current_budget_adopts_concurrent_seed(service: TradeIdeaService) -> None:
    from concurrent.futures import ThreadPoolExecutor

    service._repository.initialize()
    other = TradeIdeaService(service.root)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda client: client.current_budget(), [service, other]))
    assert results[0] == results[1]
    history = service.budget_log.history()
    assert len(history) == 1


def test_budget_resolution_fails_closed_on_corrupt_log(service: TradeIdeaService) -> None:
    # A duplicated version (the unlocked-race artifact) must stop budget
    # resolution rather than fall back to the seeded defaults.
    service.current_budget()
    path = service.budget_log.path
    with service._repository.transaction(write=True):
        line = service._repository.lines(path.name)[0]
        service._repository.append(path.name, line)

    with pytest.raises(BudgetIntegrityError):
        service.peek_budget()
    with pytest.raises(BudgetIntegrityError):
        service.current_budget()


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
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Test: enter bounded autonomy through the audited path",
    )
    widened = RiskBudget.from_dict(
        {**DEFAULT_RISK_BUDGET.to_dict(), "version": 2, "max_loss_per_idea_pct": "8"}
    )

    with pytest.raises(PolicyViolationError) as exc_info:
        service.update_budget(widened, actor_type=ActorType.AI, actor_id="idea-generator-v1")

    assert any("budget meta-envelope" in violation for violation in exc_info.value.violations)
    assert service.current_budget() == DEFAULT_RISK_BUDGET
