"""Routing outcomes through real collaborators, with broker boundaries isolated."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock

import pytest

from gpt_trader.core import OrderSide
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ENV_VAR,
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    AutonomyMode,
    create_trade_idea_service,
)


@pytest.fixture
def routing_engine(real_flow_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(tmp_path / "ideas"))
    monkeypatch.delenv(AUTO_APPROVAL_ENV_VAR, raising=False)
    monkeypatch.delenv("GPT_TRADER_IDEAS_AUTO_EXECUTION", raising=False)
    return real_flow_engine


def _configure(engine, monkeypatch, mode):
    engine.context.config.strategy_signal_proposals_enabled = mode in {"proposal", "paper-both"}
    engine.context.config.event_driven_paper_lane_enabled = mode.startswith("paper")
    engine._init_strategy_proposal_bridge()
    if mode.startswith("paper"):
        monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
        monkeypatch.setenv("GPT_TRADER_IDEAS_AUTO_EXECUTION", "1")
        service = engine._trade_idea_service
        service.update_budget(
            replace(DEFAULT_RISK_BUDGET, version=2, account_equity=Decimal("25000")),
            actor_type=ActorType.HUMAN,
            actor_id="test-operator",
        )
        if mode in {"paper", "paper-both"}:
            service.set_autonomy_mode(
                AutonomyMode.BOUNDED_AUTONOMY,
                actor_type=ActorType.HUMAN,
                actor_id="test-operator",
                reason="isolated routing test",
            )


async def _decide(engine, action=Action.BUY):
    await engine._handle_decision(
        symbol="BTC-USD",
        decision=Decision(action, "routing contract", 0.82),
        price=Decimal("50000"),
        equity=Decimal("1000"),
        position_state={"side": "long", "quantity": Decimal("0.5")},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "action", "engine_calls", "paper_calls", "actions"),
    [
        ("direct", Action.BUY, 1, 0, []),
        ("proposal", Action.BUY, 0, 0, [AuditAction.PROPOSED]),
        (
            "paper",
            Action.BUY,
            0,
            1,
            [AuditAction.PROPOSED, AuditAction.APPROVED, AuditAction.SUBMITTED, AuditAction.FILLED],
        ),
        (
            "paper-both",
            Action.BUY,
            0,
            1,
            [AuditAction.PROPOSED, AuditAction.APPROVED, AuditAction.SUBMITTED, AuditAction.FILLED],
        ),
        (
            "paper-denied",
            Action.BUY,
            0,
            0,
            [AuditAction.PROPOSED, AuditAction.AUTO_APPROVAL_SKIPPED],
        ),
        ("proposal", Action.SELL, 0, 0, []),
        ("proposal", Action.CLOSE, 0, 0, []),
        ("paper", Action.SELL, 0, 0, []),
        ("paper", Action.CLOSE, 0, 0, []),
    ],
)
async def test_strategy_route_records_actual_calls_and_audit(
    routing_engine, real_flow_broker, monkeypatch, mode, action, engine_calls, paper_calls, actions
):
    engine = routing_engine
    _configure(engine, monkeypatch, mode)
    paper_place = Mock()
    if engine._event_idea_lane is not None:
        paper_broker = engine._event_idea_lane._broker
        paper_place = Mock(wraps=paper_broker.place_order)
        monkeypatch.setattr(paper_broker, "place_order", paper_place)

    await _decide(engine, action)

    assert real_flow_broker.place_order.call_count == engine_calls
    assert paper_place.call_count == paper_calls
    # Reopen storage: assertions concern durable evidence, not a returned view.
    views = create_trade_idea_service().list_views()
    assert len(views) == bool(actions)
    assert [event.action for view in views for event in view.events] == actions
    if engine_calls:
        events = engine._event_store.list_events()
        assert any(event["type"] == "order_decision_trace" for event in events)
    if paper_calls:
        assert views[0].events[-1].action is AuditAction.FILLED
        assert paper_place.call_args.kwargs["client_id"] == views[0].idea.decision_id


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_boundary", ["proposal", "paper"])
async def test_route_failure_never_falls_through_to_engine_broker(
    routing_engine, real_flow_broker, monkeypatch, caplog, failure_boundary
):
    engine = routing_engine
    _configure(engine, monkeypatch, failure_boundary)
    if failure_boundary == "proposal":
        # Fail at persistence, after real adapter mapping.
        monkeypatch.setattr(
            engine._trade_idea_service, "propose", Mock(side_effect=OSError("disk"))
        )
    else:
        monkeypatch.setattr(
            engine._event_idea_lane._broker, "place_order", Mock(side_effect=OSError("paper"))
        )

    await _decide(engine)

    real_flow_broker.place_order.assert_not_called()
    views = create_trade_idea_service().list_views()
    expected = (
        []
        if failure_boundary == "proposal"
        else [AuditAction.PROPOSED, AuditAction.APPROVED, AuditAction.SUBMITTED]
    )
    assert [event.action for view in views for event in view.events] == expected
    operation = "strategy_proposal" if failure_boundary == "proposal" else "event_idea_lane"
    assert any(
        getattr(record, "operation", None) == operation
        and getattr(record, "stage", None) == "failed"
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct", "proposal", "paper"])
@pytest.mark.parametrize("kill_switch", [False, True])
async def test_public_submit_is_separate_from_strategy_routing_but_keeps_guards(
    routing_engine, real_flow_broker, monkeypatch, mode, kill_switch
):
    engine = routing_engine
    _configure(engine, monkeypatch, mode)
    engine.context.risk_manager.config.kill_switch_enabled = kill_switch

    result = await engine.submit_order("BTC-USD", OrderSide.BUY, Decimal("50000"), Decimal("1000"))

    assert result.blocked is kill_switch
    assert real_flow_broker.place_order.call_count == (0 if kill_switch else 1)
    assert create_trade_idea_service().list_views() == []
    assert result.decision_trace is not None
    if kill_switch:
        assert result.reason == "kill_switch"
        assert result.decision_trace.outcomes["kill_switch"]["status"] == "blocked"
