"""Shared actual-service builders for reduction tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.core.order_intent import PositionOperation
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import PaperIdeaExecutor
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AutonomyMode,
    EntryZone,
    ExitPlan,
    SizingRecommendation,
    TimeHorizon,
    TradeDirection,
    TradeIdeaService,
)

NOW = datetime(2026, 9, 5, 9, tzinfo=UTC)


def setup_position(
    tmp_path, monkeypatch, direction=TradeDirection.LONG, *, instrument="BTC-USD", now=NOW
):
    service = TradeIdeaService(tmp_path, now_factory=lambda: now)
    service.update_budget(
        replace(
            DEFAULT_RISK_BUDGET, version=2, account_equity=Decimal("50000"), allow_naked_shorts=True
        ),
        ActorType.HUMAN,
        "operator",
    )
    service.set_autonomy_mode(
        AutonomyMode.HUMAN_APPROVED_EXECUTION,
        actor_type=ActorType.HUMAN,
        actor_id="operator",
        reason="isolated paper test",
    )
    idea = build_trade_idea(
        decision_id="entry",
        instrument=instrument,
        entry_zone=EntryZone(lower=Decimal("99"), upper=Decimal("101")),
        exit_plan=ExitPlan(
            stop=Decimal("90") if direction is TradeDirection.LONG else Decimal("110"),
            target=Decimal("110") if direction is TradeDirection.LONG else Decimal("90"),
        ),
        direction=direction,
        sizing_recommendation=SizingRecommendation(quantity=Decimal("1"), notional=Decimal("100")),
        time_horizon=TimeHorizon(expires_at=now + timedelta(days=7)),
    )
    service.propose(idea, actor_id="proposer")
    service.approve("entry", actor_id="operator", reason="paper test")
    broker = DeterministicBroker()
    price = [Decimal("100")]
    original = broker.place_order

    def place(**kwargs):
        order = original(**kwargs)
        return replace(order, avg_fill_price=price[0], updated_at=now)

    monkeypatch.setattr(broker, "place_order", Mock(side_effect=place))
    executor = PaperIdeaExecutor(service, broker, now_factory=lambda: now)
    executor.execute("entry")
    return service, broker, executor, price


def proposal(service, decision_id, quantity, action="reduce"):
    target = service.get("entry").idea
    idea = replace(
        target,
        decision_id=decision_id,
        broker_ticket=type(target.broker_ticket)(),
        position_operation=PositionOperation(action, "entry", target.record_hash()),
        sizing_recommendation=replace(
            target.sizing_recommendation,
            quantity=Decimal(quantity) if quantity is not None else None,
        ),
    )
    service.propose(idea, actor_id="proposer")
    service.approve(decision_id, actor_id="operator", reason="reduce target")
    return idea
