from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.errors import ValidationError
from gpt_trader.features.live_trade.strategies.baseline.strategy import (
    Action,
    Decision,
)
from gpt_trader.features.strategy_tools import (
    StrategyDecisionSignal,
    StrategySignalContext,
    StrategySignalToTradeIdeaAdapter,
    StrategySignalToTradeIdeaAdapterConfig,
)
from gpt_trader.features.trade_ideas import (
    ActorType,
    ProductType,
    TradeDirection,
    TradeIdeaPositionSizingBridge,
    TradeIdeaService,
    TradeIdeaState,
    evaluate_eligibility,
)


def _context(**overrides) -> StrategySignalContext:
    fields = {
        "symbol": "BTC-USD",
        "current_mark": Decimal("60000"),
        "as_of": datetime(2026, 6, 29, 9, 0, tzinfo=UTC),
        "strategy_name": "baseline-perps",
        "data_source": "unit-test:strategy-decision",
    }
    fields.update(overrides)
    return StrategySignalContext(**fields)


def _buy_decision(**overrides) -> Decision:
    fields = {
        "action": Action.BUY,
        "reason": "RSI recovered while price reclaimed the long moving average",
        "confidence": 0.82,
        "indicators": {"rsi": "44", "trend": "bullish"},
    }
    fields.update(overrides)
    return Decision(**fields)


def _enabled_adapter() -> StrategySignalToTradeIdeaAdapter:
    return StrategySignalToTradeIdeaAdapter(StrategySignalToTradeIdeaAdapterConfig(enabled=True))


def test_accepts_shipped_baseline_strategy_decision_shape() -> None:
    assert isinstance(_buy_decision(), StrategyDecisionSignal)


def test_default_off_adapter_does_not_map_or_propose(tmp_path: Path) -> None:
    adapter = StrategySignalToTradeIdeaAdapter()
    service = TradeIdeaService(tmp_path / "trade_ideas")

    assert adapter.map_decision(_buy_decision(), _context()) is None
    assert adapter.propose_decision(_buy_decision(), _context(), service) is None
    assert service.list_views() == []


def test_buy_decision_maps_to_eligible_broker_neutral_trade_idea() -> None:
    idea = _enabled_adapter().map_decision(_buy_decision(), _context())

    assert idea is not None
    assert idea.decision_id.startswith("trade-20260629-baseline-perps-btc-usd-")
    assert idea.autonomy_mode.value == "human_approved_execution"
    assert idea.instrument == "BTC-USD"
    assert idea.product_type is ProductType.SPOT
    assert idea.direction is TradeDirection.LONG
    assert idea.entry_zone.lower == Decimal("59400.00")
    assert idea.entry_zone.upper == Decimal("60600.00")
    assert idea.max_loss.percent_of_account == Decimal("2")
    assert "unit-test:strategy-decision:BTC-USD" in idea.data_used[0]
    assert evaluate_eligibility(idea) == []


def test_propose_decision_enters_workflow_as_ai_proposed_only(tmp_path: Path) -> None:
    adapter = _enabled_adapter()
    service = TradeIdeaService(tmp_path / "trade_ideas")

    view = adapter.propose_decision(_buy_decision(), _context(), service)

    assert view is not None
    assert view.state is TradeIdeaState.PROPOSED
    assert [event.actor_type for event in view.events] == [ActorType.AI]
    assert service.get(view.idea.decision_id).state is TradeIdeaState.PROPOSED


@pytest.mark.parametrize("action", [Action.HOLD, Action.SELL, Action.CLOSE])
def test_non_buy_decisions_are_not_mapped(action: Action, tmp_path: Path) -> None:
    adapter = _enabled_adapter()
    service = TradeIdeaService(tmp_path / "trade_ideas")
    decision = _buy_decision(action=action)

    assert adapter.map_decision(decision, _context()) is None
    assert adapter.propose_decision(decision, _context(), service) is None
    assert service.list_views() == []


def test_subcent_mark_with_default_precision_is_rejected() -> None:
    adapter = _enabled_adapter()
    context = _context(symbol="SHIB-USD", current_mark=Decimal("0.00001234"))

    with pytest.raises(ValidationError, match="price_precision is too coarse"):
        adapter.map_decision(_buy_decision(), context)


def test_subcent_mark_with_fine_precision_preserves_levels() -> None:
    adapter = StrategySignalToTradeIdeaAdapter(
        StrategySignalToTradeIdeaAdapterConfig(enabled=True, price_precision=Decimal("0.00000001"))
    )
    context = _context(symbol="SHIB-USD", current_mark=Decimal("0.00001234"))

    idea = adapter.map_decision(_buy_decision(), context)

    assert idea is not None
    assert idea.entry_zone.lower > 0
    assert idea.entry_zone.lower < idea.entry_zone.upper
    assert evaluate_eligibility(idea) == []


def test_decision_id_is_stable_across_equivalent_marks_and_reasons() -> None:
    adapter = _enabled_adapter()

    compact = adapter.map_decision(
        _buy_decision(reason="breakout"), _context(current_mark=Decimal("60000"))
    )
    padded = adapter.map_decision(
        _buy_decision(reason="  breakout  "), _context(current_mark=Decimal("60000.00"))
    )

    assert compact is not None and padded is not None
    assert compact.decision_id == padded.decision_id


def test_naive_as_of_is_rejected() -> None:
    adapter = _enabled_adapter()
    context = _context(as_of=datetime(2026, 6, 29, 9, 0))

    with pytest.raises(ValidationError, match="as_of must be timezone-aware"):
        adapter.map_decision(_buy_decision(), context)


def test_decision_id_is_invariant_to_equivalent_utc_offsets() -> None:
    adapter = _enabled_adapter()

    utc = adapter.map_decision(
        _buy_decision(), _context(as_of=datetime(2026, 6, 29, 9, 0, tzinfo=UTC))
    )
    eastern = adapter.map_decision(
        _buy_decision(),
        _context(as_of=datetime(2026, 6, 29, 5, 0, tzinfo=timezone(timedelta(hours=-4)))),
    )

    assert utc is not None and eastern is not None
    assert utc.decision_id == eastern.decision_id
    assert utc.time_horizon.expires_at == eastern.time_horizon.expires_at


def test_non_spot_context_is_rejected_before_proposal(tmp_path: Path) -> None:
    adapter = _enabled_adapter()
    service = TradeIdeaService(tmp_path / "trade_ideas")
    context = _context(product_type=ProductType.FUTURES)

    with pytest.raises(ValidationError, match="supports spot ideas only"):
        adapter.propose_decision(_buy_decision(), context, service)

    assert service.list_views() == []


def test_sizing_bridge_makes_mapped_ideas_executable() -> None:
    adapter = StrategySignalToTradeIdeaAdapter(
        StrategySignalToTradeIdeaAdapterConfig(enabled=True),
        sizing_bridge=TradeIdeaPositionSizingBridge(),
    )

    idea = adapter.map_decision(_buy_decision(), _context())

    assert idea is not None
    assert idea.sizing_recommendation.quantity is not None
    assert idea.sizing_recommendation.quantity > 0
    assert idea.sizing_recommendation.notional is not None
    assert idea.max_loss.amount is not None
    assert idea.max_loss.amount > 0
    assert idea.max_loss.percent_of_account is not None
    assert any("engine=position-sizer-bridge-v1" in item for item in idea.data_used)
    assert any("Sized at the worst permitted entry" in item for item in idea.max_loss.assumptions)
    assert evaluate_eligibility(idea) == []


def test_default_adapter_sizing_stays_advisory() -> None:
    idea = _enabled_adapter().map_decision(_buy_decision(), _context())

    assert idea is not None
    assert idea.sizing_recommendation.quantity is None
    assert idea.sizing_recommendation.notional is None
    assert idea.max_loss.amount is None
    assert any(
        "Sizing remains advisory until a human approves the idea" in item
        for item in idea.max_loss.assumptions
    )
