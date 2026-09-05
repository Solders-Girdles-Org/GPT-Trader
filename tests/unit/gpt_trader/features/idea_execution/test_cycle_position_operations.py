"""Real injected producer → cycle → existing target admission contracts."""

from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock

import pytest
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    crossover_series,
    snapshot,
    snapshot_provider,
)
from tests.unit.gpt_trader.features.idea_execution.reduction_test_helpers import (
    NOW,
    proposal,
    setup_position,
)

from gpt_trader.core.order_intent import PositionOperation
from gpt_trader.features.idea_execution import PaperCycleRunner
from gpt_trader.features.trade_ideas import ActorType, AutonomyMode, TradeIdeaState
from gpt_trader.features.trade_ideas.position_operations import position


def candidate(service, *, decision_id="close", quantity=None, action="close"):
    target = service.get("entry").idea
    return replace(
        target,
        decision_id=decision_id,
        broker_ticket=type(target.broker_ticket)(),
        exit_plan=None,
        position_operation=PositionOperation(action, "entry", target.record_hash()),
        sizing_recommendation=replace(target.sizing_recommendation, quantity=quantity),
    )


def run_cycle(service, broker, root, ideas):
    proposer = Mock(proposer_id="injected-analyst", propose=Mock(return_value=ideas))
    runner = PaperCycleRunner(
        service,
        cycle_root=root,
        proposers=[proposer],
        broker=broker,
        now_factory=lambda: NOW,
    )
    return runner.run(
        snapshot_provider(snapshot(crossover_series("BTC-USD", as_of=NOW), as_of=NOW))
    )


@pytest.mark.parametrize(
    "action,quantity", [("close", None), ("close", Decimal("1")), ("reduce", Decimal("0.4"))]
)
def test_cycle_records_targeted_operation_then_executes_only_after_approval(
    tmp_path, monkeypatch, action, quantity
):
    service, broker, _, _ = setup_position(tmp_path / "state", monkeypatch)
    idea = candidate(service, action=action, quantity=quantity)
    opening = replace(service.get("entry").idea, decision_id="duplicate-opening")
    first = run_cycle(service, broker, tmp_path / "cycle", [idea, opening])
    assert first.proposer_turns[0].proposed_decision_ids == ("close",)
    assert first.proposer_turns[0].skipped_open_instruments[0]["existing_decision_id"] == "entry"
    assert service.get("close").state is TradeIdeaState.PROPOSED
    assert service.get("close").events[0].actor_id == "injected-analyst"
    assert broker.place_order.call_count == 1  # Original entry only.

    service.approve("close", actor_id="operator", reason="reviewed targeted operation")
    second = run_cycle(service, broker, tmp_path / "cycle", [idea])
    assert second.proposer_turns[0].proposal_count == 0  # Same decision is idempotent.
    assert service.get("close").state is TradeIdeaState.FILLED
    assert broker.place_order.call_count == 2
    kwargs = broker.place_order.call_args.kwargs
    assert kwargs["reduce_only"] is True
    assert kwargs["side"].value == "SELL"
    assert kwargs["quantity"] == (quantity or Decimal("1"))
    assert position(service, "entry").remaining == Decimal("1") - (quantity or Decimal("1"))


@pytest.mark.parametrize("invalid", ["target", "hash", "symbol", "quantity"])
def test_cycle_refuses_invalid_targeted_candidate_before_recording(tmp_path, monkeypatch, invalid):
    service, broker, _, _ = setup_position(tmp_path / "state", monkeypatch)
    idea = candidate(service)
    if invalid == "target":
        idea = replace(
            idea, position_operation=replace(idea.position_operation, target_decision_id="absent")
        )
    elif invalid == "hash":
        idea = replace(
            idea, position_operation=replace(idea.position_operation, target_record_hash="0" * 64)
        )
    elif invalid == "symbol":
        idea = replace(idea, instrument="ETH-USD")
    else:
        idea = replace(
            idea, sizing_recommendation=replace(idea.sizing_recommendation, quantity=Decimal("2"))
        )
    result = run_cycle(service, broker, tmp_path / "cycle", [idea])
    turn = result.proposer_turns[0]
    assert turn.proposal_count == 0
    assert "position operation:" in turn.skipped_open_instruments[0]["reason"]
    assert {view.idea.decision_id for view in service.list_views()} == {"entry"}
    assert broker.place_order.call_count == 1


def test_cycle_respects_uncertain_reservation_and_never_resends(tmp_path, monkeypatch):
    service, broker, executor, _ = setup_position(tmp_path / "state", monkeypatch)
    proposal(service, "pending", "0.7")
    broker.place_order.side_effect = SystemExit("lost acknowledgment")
    with pytest.raises(SystemExit):
        executor.execute("pending")
    assert position(service, "entry").reserved == Decimal("0.7")
    idea = candidate(service, action="reduce", quantity=Decimal("0.4"))
    result = run_cycle(service, broker, tmp_path / "cycle", [idea])
    assert result.proposer_turns[0].proposal_count == 0
    assert "position operation:" in result.proposer_turns[0].skipped_open_instruments[0]["reason"]
    assert broker.place_order.call_count == 2
    assert position(service, "entry").remaining == Decimal("1")


def test_cycle_targeted_proposal_cannot_bypass_revoked_execution_authority(tmp_path, monkeypatch):
    service, broker, _, _ = setup_position(tmp_path / "state", monkeypatch)
    idea = candidate(service)
    run_cycle(service, broker, tmp_path / "cycle", [idea])
    service.approve("close", actor_id="operator", reason="reviewed close")
    service.set_autonomy_mode(
        AutonomyMode.RESEARCH_ONLY,
        actor_type=ActorType.HUMAN,
        actor_id="operator",
        reason="revoked before next cycle",
    )
    result = run_cycle(service, broker, tmp_path / "cycle", [])
    assert result.execution.skipped
    assert service.get("close").state is TradeIdeaState.APPROVED
    assert broker.place_order.call_count == 1
    assert position(service, "entry").remaining == Decimal("1")


def test_cycle_reopens_only_after_partial_reduction_and_final_target_close(tmp_path, monkeypatch):
    service, broker, _, _ = setup_position(tmp_path / "state", monkeypatch)
    opening = replace(service.get("entry").idea, decision_id="next-entry")
    partial = candidate(service, decision_id="partial", action="reduce", quantity=Decimal("0.4"))
    run_cycle(service, broker, tmp_path / "cycle", [partial])
    service.approve("partial", actor_id="operator", reason="partial target")
    run_cycle(service, broker, tmp_path / "cycle", [])
    blocked = run_cycle(service, broker, tmp_path / "cycle", [opening])
    assert blocked.proposer_turns[0].proposal_count == 0
    assert position(service, "entry").remaining == Decimal("0.6")
    finish = candidate(service, decision_id="finish")
    run_cycle(service, broker, tmp_path / "cycle", [finish])
    service.approve("finish", actor_id="operator", reason="remaining target")
    run_cycle(service, broker, tmp_path / "cycle", [])
    assert position(service, "entry").remaining == 0
    assert service.get("entry").closeout_attribution is not None
    assert service.get("partial").closeout_attribution is None
    assert service.get("finish").closeout_attribution is None
    reopened = run_cycle(service, broker, tmp_path / "cycle", [opening])
    assert reopened.proposer_turns[0].proposed_decision_ids == ("next-entry",)
    assert broker.place_order.call_count == 3  # Entry and two reductions only.
