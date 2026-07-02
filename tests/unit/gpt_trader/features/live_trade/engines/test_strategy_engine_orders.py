"""Tests for TradingEngine order flow, guards, and quantity calculations.

These tests run against the engine's REAL validator/submitter/state-collector
stack (``real_flow_engine``). Behavior is steered only at the broker and
risk-manager boundaries — never by patching engine internals — so the suite
keeps its teeth through strategy.py decomposition refactors.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from strategy_engine_chaos_helpers import make_position

import gpt_trader.security.validate as security_validate_module
from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.live_trade.execution.decision_trace import OrderDecisionTrace
from gpt_trader.features.live_trade.execution.submission_result import OrderSubmissionStatus
from gpt_trader.features.live_trade.risk.manager import ValidationError
from gpt_trader.features.live_trade.strategies.perps_baseline import Action, Decision


async def _place_order(engine, action: Action = Action.BUY):
    return await engine._validate_and_place_order(
        symbol="BTC-USD",
        decision=Decision(action, "test"),
        price=Decimal("50000"),
        equity=Decimal("10000"),
    )


def _mock_security_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_validator = MagicMock()
    mock_validator.validate_order_request.return_value.is_valid = True
    monkeypatch.setattr(security_validate_module, "get_validator", lambda: mock_validator)


def _breach_slippage_guard(broker) -> None:
    """Give the real slippage guard a snapshot whose L1 depth is so shallow
    that any order's expected impact exceeds the 50 bps guard limit."""
    broker.get_market_snapshot.return_value = {"spread_bps": 10, "depth_l1": 100}


def _setup_pre_trade_validation_block(engine) -> None:
    engine.context.risk_manager.pre_trade_validate.side_effect = ValidationError(
        "Leverage exceeds limit"
    )


def _setup_mark_staleness_block(engine) -> None:
    engine.context.risk_manager.check_mark_staleness.return_value = True
    engine.context.risk_manager.config.mark_staleness_allow_reduce_only = False


def _gate_blocked_events(engine) -> list[dict]:
    return [
        event
        for event in engine._event_store.list_events()
        if event.get("type") == "trade_gate_blocked"
    ]


@pytest.fixture
def reset_metrics():
    from gpt_trader.monitoring.metrics_collector import reset_all

    reset_all()
    yield
    reset_all()


def test_finalize_decision_trace_records_blocked_metric(real_flow_engine, reset_metrics) -> None:
    from gpt_trader.monitoring.metrics_collector import get_metrics_collector

    trace = OrderDecisionTrace(
        symbol="BTC-USD",
        side="BUY",
        price=Decimal("50000"),
        equity=Decimal("10000"),
        quantity=Decimal("0.1"),
        reduce_only=False,
        reason="test",
    )

    result = real_flow_engine._finalize_decision_trace(
        trace,
        status=OrderSubmissionStatus.BLOCKED,
        reason="guard_block",
    )

    assert result.status is OrderSubmissionStatus.BLOCKED
    collector = get_metrics_collector()
    assert collector.counters["gpt_trader_trades_blocked_total"] == 1


@pytest.mark.asyncio
async def test_order_placed_with_dynamic_quantity(
    real_flow_engine, monkeypatch: pytest.MonkeyPatch
):
    """Full flow from decision through the real guard stack to broker submission."""
    from gpt_trader.core import Balance

    engine = real_flow_engine
    engine.strategy.decide.return_value = Decision(Action.BUY, "test")
    engine.strategy.config.position_fraction = Decimal("0.1")
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("10000"), available=Decimal("10000"))
    ]
    _mock_security_validation(monkeypatch)

    await engine._cycle()

    engine.context.broker.place_order.assert_called_once()
    call_kwargs = engine.context.broker.place_order.call_args[1]
    assert call_kwargs["symbol"] == "BTC-USD"
    assert call_kwargs["side"] == OrderSide.BUY
    assert call_kwargs["order_type"] == OrderType.MARKET
    assert call_kwargs["quantity"] == Decimal("0.02")
    assert call_kwargs["client_id"]  # decision-linked client order id
    assert "order-1" in engine._open_orders


@pytest.mark.asyncio
async def test_mark_staleness_seeded_from_rest_fetch(real_flow_engine):
    """REST price fetch seeds the mark staleness timestamp."""
    engine = real_flow_engine
    assert "BTC-USD" not in engine.context.risk_manager.last_mark_update

    engine.strategy.decide.return_value = Decision(Action.HOLD, "test")

    await engine._cycle()

    assert "BTC-USD" in engine.context.risk_manager.last_mark_update
    assert engine.context.risk_manager.last_mark_update["BTC-USD"] > 0


@pytest.mark.asyncio
async def test_exchange_rules_bumps_undersized_market_order_to_min_size(
    real_flow_engine, monkeypatch: pytest.MonkeyPatch
):
    """Undersized market orders are auto-bumped to the product minimum, not blocked.

    The mock-era predecessor of this test asserted a below-minimum rejection
    that the production spec validator does not implement: market orders carry
    no price, so the min-notional rejection never applies, and quantities that
    undershoot ``min_size`` are quantized up to the minimum tradable size
    (specs.validate_order) and submitted.
    """
    from gpt_trader.core import Balance

    engine = real_flow_engine
    engine.strategy.decide.return_value = Decision(Action.BUY, "test")
    # 100 USD * 0.001 / 50000 = 0.000002 BTC, below the 0.0001 product minimum.
    engine.strategy.config.position_fraction = Decimal("0.001")
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("100"), available=Decimal("100"))
    ]
    _mock_security_validation(monkeypatch)

    await engine._cycle()

    engine.context.broker.place_order.assert_called_once()
    call_kwargs = engine.context.broker.place_order.call_args[1]
    assert call_kwargs["quantity"] == Decimal("0.0001")
    assert not _gate_blocked_events(engine)


@pytest.mark.asyncio
async def test_slippage_guard_blocks_order(real_flow_engine, monkeypatch: pytest.MonkeyPatch):
    """The real slippage guard blocks orders whose market impact breaches the limit."""
    from gpt_trader.core import Balance

    engine = real_flow_engine
    engine.strategy.decide.return_value = Decision(Action.BUY, "test")
    engine.strategy.config.position_fraction = Decimal("0.1")
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("10000"), available=Decimal("10000"))
    ]
    _breach_slippage_guard(engine.context.broker)
    _mock_security_validation(monkeypatch)

    await engine._cycle()

    engine.context.broker.place_order.assert_not_called()
    events = _gate_blocked_events(engine)
    assert events
    payload = events[-1].get("data", {})
    assert payload.get("gate") == "slippage_guard"


@pytest.mark.asyncio
async def test_stale_mark_pauses_symbol(real_flow_engine) -> None:
    engine = real_flow_engine
    engine.context.risk_manager.check_mark_staleness.return_value = True
    await _place_order(engine)
    assert engine._degradation.is_paused(symbol="BTC-USD")
    assert "mark_staleness" in (engine._degradation.get_pause_reason("BTC-USD") or "")
    assert any(e.get("type") == "stale_mark_detected" for e in engine._event_store.list_events())
    events = _gate_blocked_events(engine)
    assert events
    payload = events[-1].get("data", {})
    assert payload.get("gate") == "mark_staleness"


@pytest.mark.asyncio
async def test_stale_mark_allows_reduce_only_when_configured(real_flow_engine) -> None:
    engine = real_flow_engine
    engine.context.risk_manager.check_mark_staleness.return_value = True
    engine.context.risk_manager.config.mark_staleness_allow_reduce_only = True
    engine._current_positions = {"BTC-USD": make_position()}
    await _place_order(engine, Action.SELL)
    engine.context.broker.place_order.assert_called()
    assert engine.context.broker.place_order.call_args[1]["reduce_only"] is True


@pytest.mark.asyncio
async def test_close_signal_submits_reduce_only_exit_for_long_position(
    real_flow_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = real_flow_engine
    _mock_security_validation(monkeypatch)
    engine._current_positions = {"BTC-USD": make_position(qty="0.75", side="long")}

    await engine._handle_decision(
        symbol="BTC-USD",
        decision=Decision(Action.CLOSE, "exit_long"),
        price=Decimal("50000"),
        equity=Decimal("10000"),
        position_state={
            "quantity": Decimal("0.75"),
            "entry_price": Decimal("40000"),
            "side": "long",
        },
    )

    engine.context.broker.place_order.assert_called_once()
    call_kwargs = engine.context.broker.place_order.call_args[1]
    assert call_kwargs["side"] == OrderSide.SELL
    assert call_kwargs["quantity"] == Decimal("0.75")
    assert call_kwargs["reduce_only"] is True


@pytest.mark.asyncio
async def test_close_signal_submits_reduce_only_exit_for_short_position(
    real_flow_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = real_flow_engine
    _mock_security_validation(monkeypatch)
    engine._current_positions = {"BTC-USD": make_position(qty="0.5", side="short")}

    await engine._handle_decision(
        symbol="BTC-USD",
        decision=Decision(Action.CLOSE, "exit_short"),
        price=Decimal("50000"),
        equity=Decimal("10000"),
        position_state={
            "quantity": Decimal("0.5"),
            "entry_price": Decimal("40000"),
            "side": "short",
        },
    )

    engine.context.broker.place_order.assert_called_once()
    call_kwargs = engine.context.broker.place_order.call_args[1]
    assert call_kwargs["side"] == OrderSide.BUY
    assert call_kwargs["quantity"] == Decimal("0.5")
    assert call_kwargs["reduce_only"] is True


@pytest.mark.asyncio
async def test_order_blocked_when_risk_manager_unavailable(
    real_flow_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = real_flow_engine
    _mock_security_validation(monkeypatch)
    engine.context.risk_manager = None

    result = await engine._validate_and_place_order(
        symbol="BTC-USD",
        decision=Decision(Action.BUY, "test"),
        price=Decimal("50000"),
        equity=Decimal("10000"),
    )

    assert result.status == OrderSubmissionStatus.BLOCKED
    assert result.reason == "risk_manager_unavailable"
    engine.context.broker.place_order.assert_not_called()


def test_resolve_close_order_legacy_signed_quantity_fallback(real_flow_engine) -> None:
    close_for_short = real_flow_engine._resolve_close_order({"quantity": Decimal("-0.75")})
    close_for_long = real_flow_engine._resolve_close_order({"quantity": Decimal("0.75")})

    assert close_for_short == (OrderSide.BUY, Decimal("0.75"))
    assert close_for_long == (OrderSide.SELL, Decimal("0.75"))


@pytest.mark.asyncio
async def test_slippage_failures_pause_symbol_after_threshold(real_flow_engine) -> None:
    engine = real_flow_engine
    _breach_slippage_guard(engine.context.broker)
    for _ in range(3):
        await _place_order(engine)
    assert engine._degradation.is_paused(symbol="BTC-USD")


@pytest.mark.asyncio
async def test_preview_disabled_after_threshold_failures(real_flow_engine) -> None:
    from gpt_trader.features.live_trade.execution.validation import get_failure_tracker

    engine = real_flow_engine
    tracker = get_failure_tracker()
    for _ in range(3):
        tracker.record_failure("order_preview")
    engine._order_validator.enable_order_preview = True
    result = await _place_order(engine)
    assert result.status in (OrderSubmissionStatus.SUCCESS, OrderSubmissionStatus.BLOCKED)
    assert engine._order_validator.enable_order_preview is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup_guard, expected_gate, expected_blocked_stage",
    [
        (_setup_pre_trade_validation_block, "pre_trade_validation", "pre_trade_validation"),
        (_setup_mark_staleness_block, "mark_staleness", None),
    ],
)
async def test_guard_block_records_blocked_reason(
    real_flow_engine,
    monkeypatch: pytest.MonkeyPatch,
    setup_guard,
    expected_gate: str,
    expected_blocked_stage: str | None,
) -> None:
    """Guard blocks should emit telemetry with the blocked reason tag."""
    engine = real_flow_engine
    _mock_security_validation(monkeypatch)
    setup_guard(engine)

    result = await engine._validate_and_place_order(
        symbol="BTC-USD",
        decision=Decision(Action.BUY, "test"),
        price=Decimal("50000"),
        equity=Decimal("10000"),
    )

    assert result.status == OrderSubmissionStatus.BLOCKED
    engine.context.broker.place_order.assert_not_called()

    events = _gate_blocked_events(engine)
    assert events
    payload = events[-1].get("data", {})
    assert payload.get("gate") == expected_gate
    if expected_blocked_stage is not None:
        params = payload.get("params", {})
        assert params.get("blocked_stage") == expected_blocked_stage


@pytest.mark.asyncio
async def test_mark_staleness_allowed_emits_allowed_telemetry(
    real_flow_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reduce-only stale mark path should record an allowed telemetry label."""
    engine = real_flow_engine
    _mock_security_validation(monkeypatch)
    engine.context.risk_manager.check_mark_staleness.return_value = True
    engine.context.risk_manager.config.mark_staleness_allow_reduce_only = True
    engine._current_positions = {"BTC-USD": make_position()}

    result = await engine._validate_and_place_order(
        symbol="BTC-USD",
        decision=Decision(Action.SELL, "test"),
        price=Decimal("50000"),
        equity=Decimal("10000"),
    )

    assert result.status == OrderSubmissionStatus.SUCCESS
    engine.context.broker.place_order.assert_called_once()
    assert engine.context.broker.place_order.call_args[1]["reduce_only"] is True
    assert result.decision_trace is not None
    assert result.decision_trace.outcomes["mark_staleness"]["status"] == "allowed"
