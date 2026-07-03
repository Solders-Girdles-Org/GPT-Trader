"""Stage-1 cycle boundary for Stage-2 auto-approved ideas."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    build_cycle_idea,
    crossover_series,
    make_cycle_runner,
    snapshot,
    snapshot_provider,
)

from gpt_trader.features.idea_execution import AUTO_EXECUTION_ENV_VAR
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ENV_VAR,
    ActorType,
    AuditAction,
    AutonomyMode,
    CloseoutResolution,
    TradeIdeaService,
    TradeIdeaState,
)
from gpt_trader.features.trade_ideas.autonomy import RATCHET_ACTOR_ID


def _enter_bounded_autonomy(service: TradeIdeaService) -> None:
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="Test: enter bounded autonomy for auto-approval",
    )


def _auto_approve(
    service: TradeIdeaService,
    decision_id: str,
    *,
    instrument: str = "BTC-USD",
) -> None:
    service.propose(
        build_cycle_idea(decision_id, instrument=instrument),
        actor_id="test-proposer",
    )
    sweep = service.auto_approve_sweep()
    assert sweep.approved_count == 1
    assert service.get(decision_id).events[-1].actor_type is ActorType.SYSTEM


def test_system_auto_approval_skips_when_execution_flag_is_off(
    cycle_service: TradeIdeaService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.delenv(AUTO_EXECUTION_ENV_VAR, raising=False)
    _enter_bounded_autonomy(cycle_service)
    decision_id = "trade-20260703-cycle-auto-approved"
    _auto_approve(cycle_service, decision_id)

    result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
        snapshot_provider(snapshot(crossover_series("BTC-USD")))
    )

    assert result.execution.executed == ()
    (skip,) = result.execution.skipped
    assert skip["decision_id"] == decision_id
    assert "approval actor_type 'system'" in skip["reason"]
    assert cycle_service.get(decision_id).state is TradeIdeaState.APPROVED


def test_system_auto_approval_executes_when_execution_gate_passes(
    cycle_service: TradeIdeaService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, "1")
    _enter_bounded_autonomy(cycle_service)
    decision_id = "trade-20260703-cycle-auto-execute"
    _auto_approve(cycle_service, decision_id)

    result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
        snapshot_provider(snapshot(crossover_series("BTC-USD")))
    )

    assert result.execution.skipped == ()
    (executed,) = result.execution.executed
    assert executed["decision_id"] == decision_id
    assert executed["fill_price"] == "130"
    view = cycle_service.get(decision_id)
    assert view.state is TradeIdeaState.FILLED
    submitted = [event for event in view.events if event.action is AuditAction.SUBMITTED]
    assert len(submitted) == 1
    assert any(AUTO_EXECUTION_ENV_VAR in item for item in submitted[0].evidence)
    assert any("mode=bounded_autonomy" in item for item in submitted[0].evidence)


def test_execution_leg_ratchets_down_system_approvals_but_not_human_approvals(
    cycle_service: TradeIdeaService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.setenv(AUTO_EXECUTION_ENV_VAR, "1")
    _enter_bounded_autonomy(cycle_service)
    cycle_service.update_budget(
        replace(
            cycle_service.current_budget(),
            version=3,
            max_concurrent_approved_tickets=4,
            reason="test: allow approved system, human, and loss fixtures",
        ),
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
    )
    system_id = "trade-20260703-a-system-auto"
    human_id = "trade-20260703-z-human-approved"
    loss_id = "trade-20260703-loss"
    _auto_approve(cycle_service, system_id, instrument="BTC-USD")
    cycle_service.propose(
        build_cycle_idea(human_id, instrument="ETH-USD"),
        actor_id="test-proposer",
    )
    cycle_service.approve(human_id, actor_id="test-operator", reason="test approval")
    cycle_service.propose(
        build_cycle_idea(loss_id, instrument="SOL-USD"),
        actor_id="test-proposer",
    )
    cycle_service.approve(loss_id, actor_id="test-operator", reason="test approval")
    cycle_service.record_submission(loss_id, actor_id="test-operator", venue="manual")
    cycle_service.record_fill(loss_id, actor_id="test-operator", venue="manual")
    cycle_service.record_closeout_attribution(
        loss_id,
        actor_id="test-operator",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_percent=Decimal("-12"),
        evidence=("test realized loss after system approval",),
    )

    result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
        snapshot_provider(snapshot(crossover_series("BTC-USD"), crossover_series("ETH-USD")))
    )

    assert [item["decision_id"] for item in result.execution.executed] == [human_id]
    (skip,) = result.execution.skipped
    assert skip["decision_id"] == system_id
    assert "approval actor_type 'system'" in skip["reason"]
    assert cycle_service.get(system_id).state is TradeIdeaState.APPROVED
    assert cycle_service.get(human_id).state is TradeIdeaState.FILLED
    assert cycle_service.current_autonomy().mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert cycle_service.autonomy_history()[-1].actor_id == RATCHET_ACTOR_ID
