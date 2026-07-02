"""Chaos tests for TradingEngine guard failures and pause behavior."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from strategy_engine_chaos_helpers import make_position
from tests.support.chaos import ChaosBroker, api_outage_scenario, broker_read_failures_scenario

from gpt_trader.features.live_trade.execution.order_submission import (
    OrderSubmissionOutcome,
    OrderSubmissionOutcomeStatus,
)
from gpt_trader.features.live_trade.execution.submission_result import OrderSubmissionStatus
from gpt_trader.features.live_trade.guard_errors import GuardError
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision

pytest_plugins = ["strategy_engine_chaos_fixtures"]


class TestGuardFailureDegradation:
    @pytest.mark.asyncio
    async def test_guard_failure_triggers_pause_and_reduce_only(
        self, engine, mock_broker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard_manager = MagicMock()
        guard_manager.run_runtime_guards.side_effect = GuardError(
            guard_name="api_health", message="degraded"
        )
        guard_manager.cancel_all_orders.return_value = 2
        engine._guard_manager, engine.running = guard_manager, True

        async def stop(_):
            engine.running = False
            raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", stop)
        with pytest.raises(asyncio.CancelledError):
            await engine._runtime_guard_sweep()
        assert engine._degradation.is_paused()
        engine.context.risk_manager.set_reduce_only_mode.assert_called_with(
            True, reason="guard_failure:api_health"
        )

    @pytest.mark.asyncio
    async def test_api_outage_scenario_triggers_degradation(
        self, engine, mock_broker, mock_risk_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gpt_trader.features.live_trade.execution.guard_manager import GuardManager

        chaos_broker = ChaosBroker(
            mock_broker, api_outage_scenario(error_rate=0.3, open_breakers=["orders"])
        )
        engine._guard_manager = GuardManager(
            broker=chaos_broker,
            risk_manager=engine.context.risk_manager,
            equity_calculator=lambda b: (Decimal("10000"), b, Decimal("0")),
            open_orders=[],
            invalidate_cache_callback=lambda: None,
        )
        engine.running = True

        async def stop(_):
            engine.running = False
            raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", stop)
        with pytest.raises(asyncio.CancelledError):
            await engine._runtime_guard_sweep()
        assert engine._degradation.is_paused()
        events = engine._event_store.list_events()
        assert any(e.get("type") == "guard_triggered" for e in events)

    @pytest.mark.asyncio
    async def test_api_health_guard_emits_api_error_event(self, engine) -> None:
        from gpt_trader.features.live_trade.guard_errors import RiskLimitExceeded

        err = RiskLimitExceeded(
            guard_name="api_health",
            message="API health degraded",
            details={"error_rate": 0.5},
        )
        await engine._handle_guard_failure(err)
        events = engine._event_store.list_events()
        assert any(
            e.get("type") == "guard_triggered" and e.get("data", {}).get("guard") == "api_health"
            for e in events
        )
        assert any(e.get("type") == "api_error" for e in events)


class TestBrokerOutageDegradation:
    @pytest.mark.asyncio
    async def test_broker_failures_trigger_pause_after_threshold(
        self, engine, mock_broker, mock_risk_config
    ) -> None:
        engine.context.broker = ChaosBroker(mock_broker, broker_read_failures_scenario(times=3))
        await engine._fetch_total_equity({})
        assert engine._degradation._broker_failures == 1
        await engine._fetch_total_equity({})
        assert engine._degradation._broker_failures == 2
        await engine._fetch_total_equity({})
        assert engine._degradation.is_paused() and "broker_outage" in (
            engine._degradation.get_pause_reason() or ""
        )

    @pytest.mark.asyncio
    async def test_successful_broker_call_resets_counter(self, engine, mock_broker) -> None:
        engine._degradation._broker_failures = 2
        await engine._fetch_total_equity({})
        assert engine._degradation._broker_failures == 0


@pytest.mark.asyncio
async def test_kill_switch_blocks_submission(engine) -> None:
    engine.context.risk_manager.config.kill_switch_enabled = True
    engine._order_submitter.submit_order_with_result = MagicMock(
        return_value=OrderSubmissionOutcome(
            status=OrderSubmissionOutcomeStatus.SUCCESS,
            order_id="order-123",
        )
    )

    decision = Decision(Action.BUY, "kill switch test")
    result = await engine._validate_and_place_order(
        symbol="BTC-USD",
        decision=decision,
        price=Decimal("50000"),
        equity=Decimal("10000"),
        quantity_override=Decimal("1"),
        reduce_only_requested=False,
    )

    assert result.status == OrderSubmissionStatus.BLOCKED
    assert result.reason == "kill_switch"
    assert result.decision_trace and result.decision_trace.decision_id
    assert result.decision_trace.outcomes["kill_switch"]["status"] == "blocked"
    engine._order_submitter.submit_order_with_result.assert_not_called()
    engine._order_submitter.record_rejection.assert_called_with(
        "BTC-USD",
        "BUY",
        Decimal("0"),
        Decimal("50000"),
        "kill_switch",
        client_order_id=result.decision_trace.decision_id,
    )


class TestPausedOrderRejection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("pause_all", [True, False])
    async def test_order_rejected_when_paused(self, engine, pause_all: bool) -> None:
        if pause_all:
            engine._degradation.pause_all(seconds=60, reason="test_pause")
        else:
            engine._degradation.pause_symbol("BTC-USD", seconds=60, reason="test_pause")
        await engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=Decision(Action.BUY, "test"),
            price=Decimal("50000"),
            equity=Decimal("10000"),
        )
        engine.context.broker.place_order.assert_not_called()
        if pause_all:
            engine._order_submitter.record_rejection.assert_called()
        events = [
            event
            for event in engine._event_store.list_events()
            if event.get("type") == "trade_gate_blocked"
        ]
        assert events
        payload = events[-1].get("data", {})
        assert payload.get("gate") == "degradation_gate"
        assert payload.get("symbol") == "BTC-USD"
        assert payload.get("side") == "BUY"

    @pytest.mark.asyncio
    async def test_reduce_only_allowed_through_pause(self, engine) -> None:
        engine._degradation.pause_all(seconds=60, reason="test", allow_reduce_only=True)
        engine._current_positions = {"BTC-USD": make_position()}
        await engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=Decision(Action.SELL, "test"),
            price=Decimal("50000"),
            equity=Decimal("10000"),
        )
        engine._order_submitter.submit_order_with_result.assert_called()


@pytest.mark.asyncio
async def test_reduce_only_mode_blocked_emits_gate_event(engine) -> None:
    engine.context.risk_manager.is_reduce_only_mode = MagicMock(return_value=True)
    engine.context.risk_manager.check_order = MagicMock(return_value=False)

    result = await engine._validate_and_place_order(
        symbol="BTC-USD",
        decision=Decision(Action.BUY, "test"),
        price=Decimal("50000"),
        equity=Decimal("10000"),
    )

    assert result.status == OrderSubmissionStatus.BLOCKED
    events = [
        event
        for event in engine._event_store.list_events()
        if event.get("type") == "trade_gate_blocked"
    ]
    assert events
    payload = events[-1].get("data", {})
    assert payload.get("gate") == "reduce_only"
    assert payload.get("reason") == "reduce_only_mode_blocked"


class TestGuardOutcomeInvariants:
    @pytest.mark.asyncio
    async def test_degradation_blocks_before_validation(self, engine) -> None:
        engine._degradation.pause_all(seconds=60, reason="test_pause")
        engine._order_validator.run_pre_trade_validation = MagicMock()
        engine._order_validator.validate_exchange_rules = MagicMock()

        result = await engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=Decision(Action.BUY, "test"),
            price=Decimal("50000"),
            equity=Decimal("10000"),
        )

        assert result.status == OrderSubmissionStatus.BLOCKED
        engine._order_validator.run_pre_trade_validation.assert_not_called()
        engine._order_validator.validate_exchange_rules.assert_not_called()
        engine._order_submitter.submit_order_with_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_validation_blocked_vs_failed(self, engine) -> None:
        from gpt_trader.features.live_trade.risk.manager import ValidationError

        engine._order_validator.run_pre_trade_validation.side_effect = ValidationError("risk")

        result = await engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=Decision(Action.BUY, "test"),
            price=Decimal("50000"),
            equity=Decimal("10000"),
        )

        assert result.status == OrderSubmissionStatus.BLOCKED
        assert result.reason is not None
        engine._order_submitter.submit_order_with_result.assert_not_called()

        engine._order_validator.run_pre_trade_validation.side_effect = Exception("boom")

        result = await engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=Decision(Action.BUY, "test"),
            price=Decimal("50000"),
            equity=Decimal("10000"),
        )

        assert result.status == OrderSubmissionStatus.FAILED
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_reduce_only_requested_allows_stale_mark(self, engine) -> None:
        engine.context.risk_manager.check_mark_staleness.return_value = True
        engine.context.risk_manager.config.mark_staleness_allow_reduce_only = True
        engine._order_validator.finalize_reduce_only_flag.return_value = True
        engine._order_submitter.submit_order_with_result.return_value = OrderSubmissionOutcome(
            status=OrderSubmissionOutcomeStatus.SUCCESS,
            order_id="order-1",
        )
        engine._current_positions = {"BTC-USD": make_position()}

        result = await engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=Decision(Action.SELL, "test"),
            price=Decimal("50000"),
            equity=Decimal("10000"),
            reduce_only_requested=True,
        )

        assert result.status == OrderSubmissionStatus.SUCCESS
        call_kwargs = engine._order_submitter.submit_order_with_result.call_args.kwargs
        assert call_kwargs["reduce_only"] is True
