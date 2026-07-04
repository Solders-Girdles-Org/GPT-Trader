"""Stage 2 system-approval gate tests for the paper idea executor."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import (
    AUTO_EXECUTION_ENV_VAR,
    IdeaNotExecutableError,
    PaperIdeaExecutor,
    resolve_auto_execution_enabled,
)
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ENV_VAR,
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    AuditEvent,
    AutonomyMode,
    TimeHorizon,
    TradeIdea,
    TradeIdeaService,
    TradeIdeaState,
)

_NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    trade_idea_service = TradeIdeaService(tmp_path, now_factory=lambda: _NOW)
    trade_idea_service.update_budget(
        replace(
            DEFAULT_RISK_BUDGET,
            version=2,
            account_equity=Decimal("25000"),
            reason="test: attest scratch equity",
        ),
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
    )
    return trade_idea_service


def _build_idea(decision_id: str) -> TradeIdea:
    return build_trade_idea(
        decision_id=decision_id,
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=_NOW + timedelta(days=7),
        ),
    )


def _executor(
    service: TradeIdeaService,
    broker: DeterministicBroker | None = None,
) -> PaperIdeaExecutor:
    return PaperIdeaExecutor(
        service,
        broker or DeterministicBroker(),
        now_factory=lambda: _NOW,
    )


def _system_auto_approved_idea(service: TradeIdeaService, decision_id: str) -> None:
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="Test: enter bounded autonomy for auto approval",
    )
    service.propose(_build_idea(decision_id), actor_id="test-proposer")
    service.auto_approve_sweep()
    assert service.get(decision_id).events[-1].actor_type is ActorType.SYSTEM


def _approval_event(
    service: TradeIdeaService,
    decision_id: str,
    *,
    actor_type: ActorType,
    actor_id: str,
) -> None:
    idea = _build_idea(decision_id)
    service.propose(idea, actor_id="test-proposer")
    service.audit_log.append(
        AuditEvent(
            event_id=f"evt-test-{decision_id}",
            timestamp=_NOW,
            decision_id=decision_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=AuditAction.APPROVED,
            before_state=TradeIdeaState.PROPOSED,
            after_state=TradeIdeaState.APPROVED,
            reason="test non-human approval",
            record_hash=idea.record_hash(),
        )
    )


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_auto_execution_flag_parses_explicit_enablement(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, value)

    assert resolve_auto_execution_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "off", "enabled?"])
def test_auto_execution_flag_defaults_off_for_everything_else(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, value)

    assert resolve_auto_execution_enabled() is False


def test_refuses_system_auto_approved_idea_when_execution_flag_is_off(
    service: TradeIdeaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.delenv(AUTO_EXECUTION_ENV_VAR, raising=False)
    decision_id = "trade-20260702-exec-auto-approved"
    _system_auto_approved_idea(service, decision_id)

    with pytest.raises(IdeaNotExecutableError, match="requires human approval"):
        _executor(service).execute(decision_id)

    view = service.get(decision_id)
    assert view.state is TradeIdeaState.APPROVED
    assert not any(event.action is AuditAction.SUBMITTED for event in view.events)


def test_refuses_system_auto_approved_idea_below_bounded_autonomy(
    service: TradeIdeaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, "1")
    decision_id = "trade-20260702-exec-auto-approved-mode-off"
    _system_auto_approved_idea(service, decision_id)
    service.set_autonomy_mode(
        AutonomyMode.HUMAN_APPROVED_EXECUTION,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="Test: lower autonomy before execution",
    )

    with pytest.raises(IdeaNotExecutableError, match="requires human approval"):
        _executor(service).execute(decision_id)

    view = service.get(decision_id)
    assert view.state is TradeIdeaState.APPROVED
    assert not any(event.action is AuditAction.SUBMITTED for event in view.events)


@pytest.mark.parametrize(
    ("actor_type", "actor_id"),
    (
        (ActorType.AI, "idea-generator-v1"),
        (ActorType.VENUE, "paper-venue"),
        (ActorType.SYSTEM, "not-auto-approval-sweep"),
    ),
)
def test_refuses_non_sweep_approval_actors_even_when_execution_gate_is_on(
    service: TradeIdeaService,
    monkeypatch: pytest.MonkeyPatch,
    actor_type: ActorType,
    actor_id: str,
) -> None:
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, "1")
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="Test: enter bounded autonomy for executor admission",
    )
    decision_id = f"trade-20260702-exec-{actor_type.value}-approved"
    _approval_event(service, decision_id, actor_type=actor_type, actor_id=actor_id)

    with pytest.raises(IdeaNotExecutableError, match="requires human approval"):
        _executor(service).execute(decision_id)

    assert service.get(decision_id).state is TradeIdeaState.APPROVED


def test_human_approved_execution_ignores_auto_execution_gate(
    service: TradeIdeaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, "1")
    decision_id = "trade-20260702-exec-human-flag-on"
    service.propose(_build_idea(decision_id), actor_id="test-proposer")
    service.approve(decision_id, actor_id="test-operator", reason="test approval")

    result = _executor(service).execute(decision_id)

    assert result.final_state == TradeIdeaState.FILLED.value
    submitted = [
        event for event in service.get(decision_id).events if event.action is AuditAction.SUBMITTED
    ]
    assert len(submitted) == 1
    assert submitted[0].evidence == ()


def test_execute_system_auto_approved_idea_when_gate_passes(
    service: TradeIdeaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, "1")
    decision_id = "trade-20260702-exec-auto-approved-fill"
    _system_auto_approved_idea(service, decision_id)
    broker = DeterministicBroker()
    broker.set_mark("BTC-USD", Decimal("60750"))

    result = _executor(service, broker).execute(decision_id)

    assert result.final_state == TradeIdeaState.FILLED.value
    view = service.get(decision_id)
    submitted = [event for event in view.events if event.action is AuditAction.SUBMITTED]
    assert len(submitted) == 1
    assert any(AUTO_EXECUTION_ENV_VAR in item for item in submitted[0].evidence)
    assert any("mode=bounded_autonomy" in item for item in submitted[0].evidence)
    assert any("actor_id=auto-approval-sweep" in item for item in submitted[0].evidence)
