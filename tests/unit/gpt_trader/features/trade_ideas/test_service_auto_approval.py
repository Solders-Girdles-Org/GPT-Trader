"""Stage 2 auto-approval sweep: docs/decisions/stage2-auto-approval-workflow.md."""

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
    AUTO_APPROVAL_ACTOR_ID,
    AUTO_APPROVAL_ENV_VAR,
    AUTO_APPROVAL_REASON_PREFIX,
    ActorType,
    AuditAction,
    AutonomyMode,
    CloseoutResolution,
    MaxLoss,
    PolicyViolationError,
    TradeIdeaState,
    resolve_auto_approval_enabled,
)
from gpt_trader.features.trade_ideas.autonomy import RATCHET_ACTOR_ID
from gpt_trader.features.trade_ideas.service import TradeIdeaService


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


@pytest.fixture
def flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")


@pytest.fixture
def flag_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTO_APPROVAL_ENV_VAR, raising=False)


def _enter_bounded_autonomy(service: TradeIdeaService) -> None:
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Test: enter bounded autonomy through the audited path",
    )


def _record_same_day_realized_loss(
    service: TradeIdeaService,
    *,
    decision_id: str,
    loss_percent: str,
) -> None:
    idea = build_trade_idea(decision_id=decision_id)
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(decision_id, actor_id="operator", venue="manual")
    service.record_fill(decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_percent=Decimal(loss_percent),
    )


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_flag_parses_explicit_enablement(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, value)

    assert resolve_auto_approval_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "off", "enabled?"])
def test_flag_defaults_off_for_everything_else(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, value)

    assert resolve_auto_approval_enabled() is False


def test_sweep_refused_when_flag_off_and_human_path_unchanged(
    service: TradeIdeaService, flag_unset: None
) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    idea = build_trade_idea(decision_id="trade-20260612-flagoff")
    service.propose(idea, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.auto_approve_sweep()

    assert any(AUTO_APPROVAL_ENV_VAR in violation for violation in exc_info.value.violations)
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED

    approved = service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    assert approved.state is TradeIdeaState.APPROVED
    assert approved.events[-1].actor_type is ActorType.HUMAN


def test_sweep_refused_below_bounded_autonomy(
    service: TradeIdeaService, flag_enabled: None
) -> None:
    attest_account_equity(service)
    idea = build_trade_idea(decision_id="trade-20260612-mode")
    service.propose(idea, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.auto_approve_sweep()

    assert any(
        "requires audited autonomy mode 'bounded_autonomy'" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED


def test_in_budget_idea_auto_approves_with_audited_evidence(
    service: TradeIdeaService, flag_enabled: None
) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    idea = build_trade_idea(decision_id="trade-20260612-auto")
    service.propose(idea, actor_id="idea-generator-v1")

    result = service.auto_approve_sweep()

    assert result.approved_count == 1
    assert result.skipped_count == 0
    assert result.autonomy_mode == AutonomyMode.BOUNDED_AUTONOMY.value
    view = service.get(idea.decision_id)
    assert view.state is TradeIdeaState.APPROVED
    event = view.events[-1]
    assert event.actor_type is ActorType.SYSTEM
    assert event.actor_id == AUTO_APPROVAL_ACTOR_ID
    assert event.reason.startswith(AUTO_APPROVAL_REASON_PREFIX)
    assert event.evidence
    assert any("approval_violations=0" in item for item in event.evidence)
    assert any("mode=bounded_autonomy" in item for item in event.evidence)
    assert any("budget envelope" in item for item in event.evidence)


def test_violating_idea_stays_proposed_and_is_reported(
    service: TradeIdeaService, flag_enabled: None
) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    over_cap = build_trade_idea(
        decision_id="trade-20260612-overcap",
        max_loss=MaxLoss(amount=Decimal("1800"), percent_of_account=Decimal("9")),
    )
    service.propose(over_cap, actor_id="idea-generator-v1")

    result = service.auto_approve_sweep()

    assert result.approved_count == 0
    assert result.skipped_count == 1
    skip = result.skipped[0]
    assert skip.decision_id == over_cap.decision_id
    assert any("exceeds budget cap" in violation for violation in skip.violations)
    view = service.get(over_cap.decision_id)
    assert view.state is TradeIdeaState.PROPOSED
    event = view.events[-1]
    assert event.action is AuditAction.AUTO_APPROVAL_SKIPPED
    assert event.actor_type is ActorType.SYSTEM
    assert event.actor_id == AUTO_APPROVAL_ACTOR_ID
    assert event.reason.startswith(AUTO_APPROVAL_REASON_PREFIX)
    assert any("approval_violations=1" in item for item in event.evidence)
    assert any("violation: max_loss 9%" in item for item in event.evidence)


def test_sweep_respects_the_envelope_across_the_queue(
    service: TradeIdeaService, flag_enabled: None
) -> None:
    """FIFO sweep: approvals consume the daily envelope; the overflow idea stays."""
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    for suffix in ("a", "b", "c"):
        idea = build_trade_idea(
            decision_id=f"trade-20260612-queue-{suffix}",
            max_loss=MaxLoss(amount=Decimal("900"), percent_of_account=Decimal("4.5")),
        )
        service.propose(idea, actor_id="idea-generator-v1")

    result = service.auto_approve_sweep()

    approved_ids = [view.idea.decision_id for view in result.approved]
    assert approved_ids == ["trade-20260612-queue-a", "trade-20260612-queue-b"]
    assert result.skipped_count == 1
    skip = result.skipped[0]
    assert skip.decision_id == "trade-20260612-queue-c"
    assert any("max_daily_loss_pct budget breached" in violation for violation in skip.violations)
    skipped = service.get("trade-20260612-queue-c")
    assert skipped.state is TradeIdeaState.PROPOSED
    assert skipped.events[-1].action is AuditAction.AUTO_APPROVAL_SKIPPED


def test_daily_loss_breach_ratchets_down_and_refuses_the_sweep(
    service: TradeIdeaService, flag_enabled: None
) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    _record_same_day_realized_loss(service, decision_id="trade-20260612-loss", loss_percent="-12")
    candidate = build_trade_idea(decision_id="trade-20260612-candidate")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.auto_approve_sweep()

    assert any(
        "requires audited autonomy mode" in violation for violation in exc_info.value.violations
    )
    resolution = service.current_autonomy()
    assert resolution.mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert service.autonomy_history()[-1].actor_id == RATCHET_ACTOR_ID
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_broken_autonomy_log_fails_the_sweep_closed(
    service: TradeIdeaService, flag_enabled: None, tmp_path: Path
) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)
    log_path = tmp_path / "trade_ideas" / "autonomy_state.jsonl"
    log_path.write_text("garbage\n", encoding="utf-8")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.auto_approve_sweep()

    violations = exc_info.value.violations
    assert any("failed closed" in violation for violation in violations)
    assert any("research_only" in violation for violation in violations)


def test_sweep_over_empty_queue_is_a_reported_noop(
    service: TradeIdeaService, flag_enabled: None
) -> None:
    attest_account_equity(service)
    _enter_bounded_autonomy(service)

    result = service.auto_approve_sweep()

    assert result.approved == ()
    assert result.skipped == ()
    assert result.autonomy_mode == AutonomyMode.BOUNDED_AUTONOMY.value
