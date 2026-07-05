"""Event-driven paper lane engine wiring (#1191).

Covers the default-off ``event_driven_paper_lane_enabled`` gate:

- disabled gate leaves the lane unbuilt (behavior identical to today),
- enabled gate implies proposal routing and carries a buy decision through
  kernel approval to a paper fill inside the same ``_handle_decision`` call,
  without touching the engine's live broker or order path,
- with the Stage 2 operator env gates off, the lane degrades to today's
  proposal-only behavior (idea stays queued for review),
- the order-audit skip treats lane mode like proposal-only mode (no broker
  mutation on a live profile).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from gpt_trader.features.idea_execution import EVENT_LANE_ACTOR_ID
from gpt_trader.features.live_trade.engines.cycle_runner import _fetch_positions_and_audit
from gpt_trader.features.live_trade.engines.strategy import TradingEngine
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ENV_VAR,
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    AutonomyMode,
    TradeIdeaState,
    create_trade_idea_service,
)


def _enable_lane(engine: TradingEngine, tmp_path, monkeypatch) -> None:
    """Turn the lane gate on and point the trade-idea store at an isolated root."""
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(tmp_path))
    engine.context.config.event_driven_paper_lane_enabled = True
    engine._init_strategy_proposal_bridge()


def _arm_stage2(monkeypatch) -> None:
    """Operator acts: env gates on, equity attested, bounded autonomy entered."""
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    monkeypatch.setenv("GPT_TRADER_IDEAS_AUTO_EXECUTION", "1")
    service = create_trade_idea_service()
    service.update_budget(
        replace(
            DEFAULT_RISK_BUDGET,
            version=2,
            account_equity=Decimal("25000"),
            reason="test: attest scratch equity",
        ),
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
    )
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="Test: enter bounded autonomy for the event lane",
    )


def test_disabled_gate_builds_no_lane(engine) -> None:
    assert engine._event_idea_lane is None
    assert engine._strategy_proposal_adapter is None
    assert engine.context.config.event_driven_paper_lane_enabled is False


@pytest.mark.asyncio
async def test_enabled_lane_paper_executes_buy_in_same_call(
    engine, mock_broker, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_lane(engine, tmp_path, monkeypatch)
    _arm_stage2(monkeypatch)

    validate = AsyncMock()
    submit = AsyncMock()
    monkeypatch.setattr(engine, "_validate_and_place_order", validate)
    monkeypatch.setattr(engine, "submit_order", submit)
    engine.strategy.active_strategies = "baseline"

    await engine._handle_decision(
        symbol="BTC-USD",
        decision=Decision(Action.BUY, "RSI reclaimed the long MA", 0.82),
        price=Decimal("50000"),
        equity=Decimal("1000"),
        position_state=None,
    )

    # The engine's live order path and broker were never touched.
    validate.assert_not_called()
    submit.assert_not_called()
    engine._order_submitter.submit_order_with_result.assert_not_called()
    mock_broker.place_order.assert_not_called()

    service = create_trade_idea_service()
    filled = service.list_views(state=TradeIdeaState.FILLED)
    assert len(filled) == 1
    view = filled[0]
    # Executable sizing was wired: the executed idea carries a positive quantity.
    assert view.idea.sizing_recommendation.quantity is not None
    assert view.idea.sizing_recommendation.quantity > 0
    actions = [event.action for event in view.events]
    assert actions == [
        AuditAction.PROPOSED,
        AuditAction.APPROVED,
        AuditAction.SUBMITTED,
        AuditAction.FILLED,
    ]
    approval = view.events[1]
    assert approval.actor_type is ActorType.SYSTEM
    assert approval.actor_id == EVENT_LANE_ACTOR_ID


@pytest.mark.asyncio
async def test_enabled_lane_without_operator_gates_leaves_idea_queued(
    engine, mock_broker, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_lane(engine, tmp_path, monkeypatch)
    monkeypatch.delenv(AUTO_APPROVAL_ENV_VAR, raising=False)
    monkeypatch.delenv("GPT_TRADER_IDEAS_AUTO_EXECUTION", raising=False)

    validate = AsyncMock()
    monkeypatch.setattr(engine, "_validate_and_place_order", validate)
    monkeypatch.setattr(engine, "submit_order", AsyncMock())

    await engine._handle_decision(
        symbol="BTC-USD",
        decision=Decision(Action.BUY, "reclaim", 0.82),
        price=Decimal("50000"),
        equity=Decimal("1000"),
        position_state=None,
    )

    validate.assert_not_called()
    mock_broker.place_order.assert_not_called()
    # Lane mode implies proposal routing even though the Stage 1 flag is off.
    assert engine.context.config.strategy_signal_proposals_enabled is False
    service = create_trade_idea_service()
    proposed = service.list_views(state=TradeIdeaState.PROPOSED)
    assert len(proposed) == 1
    assert [event.action for event in proposed[0].events] == [AuditAction.PROPOSED]


@pytest.mark.asyncio
async def test_lane_mode_skips_order_audit_on_live_profile(
    engine, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_lane(engine, tmp_path, monkeypatch)
    engine.context.config.dry_run = False
    assert engine.context.config.strategy_signal_proposals_enabled is False

    audit = AsyncMock()
    monkeypatch.setattr(engine, "_audit_orders", audit)
    monkeypatch.setattr(engine, "_fetch_positions", AsyncMock(return_value={}))

    positions, audit_task = await _fetch_positions_and_audit(engine)
    await audit_task

    audit.assert_not_called()
