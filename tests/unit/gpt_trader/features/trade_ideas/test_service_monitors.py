"""Service-level portfolio monitors: one trail-derived read for every consumer (#1192)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    ActorType,
    AutonomyMode,
    CloseoutResolution,
)
from gpt_trader.features.trade_ideas.autonomy import RATCHET_ACTOR_ID
from gpt_trader.features.trade_ideas.service import TradeIdeaService


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def _close_with_loss(service: TradeIdeaService, decision_id: str, amount: str) -> None:
    idea = build_trade_idea(decision_id=decision_id)
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(decision_id, actor_id="operator", venue="manual")
    service.record_fill(decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_amount=Decimal(amount),
    )


def test_portfolio_monitors_snapshot_reads_the_trail(service: TradeIdeaService) -> None:
    attest_account_equity(service)  # 20000
    current = service.current_budget()
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            max_drawdown_from_peak_pct=Decimal("2"),
            reason="Configure the drawdown-from-peak appetite",
        ),
        ActorType.HUMAN,
        "rj",
    )
    _close_with_loss(service, "trade-20260612-dd", "-600")

    snapshot = service.portfolio_monitors()

    assert snapshot.high_water_mark == Decimal("20000")
    assert snapshot.current_equity == Decimal("19400")
    assert snapshot.drawdown_amount == Decimal("600")
    assert snapshot.drawdown_from_peak_pct == Decimal("3")
    assert snapshot.max_drawdown_from_peak_pct == Decimal("2")
    assert snapshot.drawdown_breached is True
    assert snapshot.account_equity_snapshot == Decimal("20000")
    assert snapshot.budget_version == service.peek_budget().version


def test_portfolio_monitors_is_a_non_seeding_read(
    service: TradeIdeaService, tmp_path: Path
) -> None:
    snapshot = service.portfolio_monitors()

    assert snapshot.current_equity is None
    assert snapshot.drawdown_breached is None
    root = tmp_path / "trade_ideas"
    assert not (root / "risk_budget.jsonl").exists()
    assert not (root / "autonomy_state.jsonl").exists()


def test_equity_ledger_points_match_the_accounting_summary(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    _close_with_loss(service, "trade-20260612-dd", "-600")

    points = service.equity_ledger_points()
    summary = service.paper_accounting()

    assert points
    assert points[-1].equity == summary.current_equity
    assert points[-1].high_water_mark == summary.high_water_mark
    assert points[-1].drawdown_percent == summary.drawdown_percent


def _configure_drawdown_appetite(service: TradeIdeaService, limit: str) -> None:
    current = service.current_budget()
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            max_drawdown_from_peak_pct=Decimal(limit),
            reason="Configure the drawdown-from-peak appetite",
        ),
        ActorType.HUMAN,
        "rj",
    )


def _enter_bounded_autonomy(service: TradeIdeaService) -> None:
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Test: enter bounded autonomy through the audited path",
    )


def test_drawdown_breach_ratchets_bounded_autonomy_down(service: TradeIdeaService) -> None:
    attest_account_equity(service)  # 20000 attested basis
    _configure_drawdown_appetite(service, "2")
    _enter_bounded_autonomy(service)
    # -600 on a 20000 peak: drawdown-from-peak 3% (> 2% appetite) while the
    # same-day realized loss (3%) stays inside max_daily_loss_pct (10%), so
    # only the drawdown monitor can be the trigger.
    _close_with_loss(service, "trade-20260612-dd", "-600")

    resolution = service.resolve_execution_autonomy()

    assert resolution.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    latest = service.autonomy_history()[-1]
    assert latest.actor_type is ActorType.SYSTEM
    assert latest.actor_id == RATCHET_ACTOR_ID
    assert "drawdown-from-peak" in latest.reason
    assert latest.evidence
    assert "drawdown_from_peak_pct=3" in latest.evidence[0]
    assert "max_drawdown_from_peak_pct=2" in latest.evidence[0]
    assert "high_water_mark=20000" in latest.evidence[0]


def test_no_drawdown_ratchet_within_appetite(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    _configure_drawdown_appetite(service, "5")
    _enter_bounded_autonomy(service)
    _close_with_loss(service, "trade-20260612-dd", "-600")

    resolution = service.resolve_execution_autonomy()

    assert resolution.mode is AutonomyMode.BOUNDED_AUTONOMY
    assert service.autonomy_history()[-1].actor_id != RATCHET_ACTOR_ID


def test_no_drawdown_ratchet_without_configured_limit(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    _close_with_loss(service, "trade-20260612-dd", "-600")

    resolution = service.resolve_execution_autonomy()

    assert resolution.mode is AutonomyMode.BOUNDED_AUTONOMY
