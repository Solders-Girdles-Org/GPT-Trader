"""Stage-1 cycle boundary for Stage-2 auto-approved ideas."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    build_cycle_idea,
    crossover_series,
    make_cycle_runner,
    snapshot,
    snapshot_provider,
)

from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ENV_VAR,
    ActorType,
    AutonomyMode,
    TradeIdeaService,
    TradeIdeaState,
)


def test_system_auto_approval_does_not_execute_in_stage1_cycle(
    cycle_service: TradeIdeaService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    cycle_service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="Test: enter bounded autonomy for auto-approval",
    )
    decision_id = "trade-20260703-cycle-auto-approved"
    cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
    sweep = cycle_service.auto_approve_sweep()
    assert sweep.approved_count == 1
    assert cycle_service.get(decision_id).events[-1].actor_type is ActorType.SYSTEM

    result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
        snapshot_provider(snapshot(crossover_series("BTC-USD")))
    )

    assert result.execution.executed == ()
    (skip,) = result.execution.skipped
    assert skip["decision_id"] == decision_id
    assert "approval actor_type 'system'" in skip["reason"]
    assert cycle_service.get(decision_id).state is TradeIdeaState.APPROVED
