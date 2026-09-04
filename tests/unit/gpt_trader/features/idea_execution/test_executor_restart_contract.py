"""Persisted submission and approval state remains authoritative after restart."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import (
    AUTO_EXECUTION_ENV_VAR,
    IdeaNotExecutableError,
    PaperIdeaExecutor,
)
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ENV_VAR,
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    AutonomyMode,
    TimeHorizon,
    TradeIdeaService,
    TradeIdeaState,
)

_NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
_ID = "trade-20260904-restart-contract"


@pytest.fixture
def approved_service(tmp_path, monkeypatch):
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, "1")
    service = TradeIdeaService(tmp_path, now_factory=lambda: _NOW)
    service.update_budget(
        replace(DEFAULT_RISK_BUDGET, version=2, account_equity=Decimal("25000")),
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
    )
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="isolated restart test",
    )
    service.propose(
        build_trade_idea(
            decision_id=_ID,
            time_horizon=TimeHorizon(
                expected_hold="3-10 days", expires_at=_NOW + timedelta(days=7)
            ),
        ),
        actor_id="test-proposer",
    )
    service.auto_approve_sweep()
    assert service.get(_ID).state is TradeIdeaState.APPROVED
    return service


@pytest.mark.parametrize("changed_gate", ["expiry", "autonomy", "execution_flag"])
def test_execute_rechecks_earlier_admission_after_restart(
    approved_service, tmp_path, monkeypatch, changed_gate
):
    PaperIdeaExecutor(
        approved_service, DeterministicBroker(), now_factory=lambda: _NOW
    ).resolve_approved_idea(_ID)
    now = _NOW + timedelta(days=7) if changed_gate == "expiry" else _NOW
    # Fresh objects reload disk state instead of retaining the admitted view.
    restarted = TradeIdeaService(tmp_path, now_factory=lambda: now)
    if changed_gate == "autonomy":
        restarted.set_autonomy_mode(
            AutonomyMode.HUMAN_APPROVED_EXECUTION,
            actor_type=ActorType.HUMAN,
            actor_id="test-operator",
            reason="authority withdrawn after admission",
        )
    if changed_gate == "execution_flag":
        monkeypatch.delenv(AUTO_EXECUTION_ENV_VAR)
    broker = DeterministicBroker()
    place = Mock(wraps=broker.place_order)
    monkeypatch.setattr(broker, "place_order", place)
    executor = PaperIdeaExecutor(restarted, broker, now_factory=lambda: now)

    with pytest.raises(IdeaNotExecutableError):
        executor.execute(_ID)

    place.assert_not_called()
    view = restarted.get(_ID)
    assert view.state is TradeIdeaState.APPROVED
    assert [event.action for event in view.events] == [AuditAction.PROPOSED, AuditAction.APPROVED]


def test_interruption_after_submission_cannot_reexecute_after_restart(
    approved_service, tmp_path, monkeypatch
):
    broker = DeterministicBroker()

    def interrupt(*args, **kwargs):
        # The durable boundary must precede even an unsuccessful broker call.
        reopened = TradeIdeaService(tmp_path, now_factory=lambda: _NOW)
        assert reopened.get(_ID).state is TradeIdeaState.SUBMITTED
        raise OSError("simulated interruption")

    monkeypatch.setattr(broker, "place_order", interrupt)
    with pytest.raises(OSError, match="simulated interruption"):
        PaperIdeaExecutor(approved_service, broker, now_factory=lambda: _NOW).execute(_ID)

    restarted = TradeIdeaService(tmp_path, now_factory=lambda: _NOW)
    fresh_broker = DeterministicBroker()
    place = Mock(wraps=fresh_broker.place_order)
    monkeypatch.setattr(fresh_broker, "place_order", place)
    with pytest.raises(IdeaNotExecutableError, match="state is submitted"):
        PaperIdeaExecutor(restarted, fresh_broker, now_factory=lambda: _NOW).execute(_ID)

    place.assert_not_called()
    assert [event.action for event in restarted.get(_ID).events] == [
        AuditAction.PROPOSED,
        AuditAction.APPROVED,
        AuditAction.SUBMITTED,
    ]
