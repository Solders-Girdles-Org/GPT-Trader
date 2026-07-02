"""Tests for TradingEngine health, risk format, and runtime guard sweep."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

import gpt_trader.security.validate as security_validate_module
from gpt_trader.core import Balance, Position
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision


def test_health_check_runner_initialized(engine):
    """Test that health check runner is initialized with engine."""
    from gpt_trader.monitoring.health_checks import HealthCheckRunner

    assert isinstance(engine._health_check_runner, HealthCheckRunner)
    assert engine._health_check_runner._broker is engine.context.broker
    assert engine._health_check_runner._degradation_state is engine._degradation
    assert engine._health_check_runner._risk_manager is engine.context.risk_manager


@pytest.mark.asyncio
async def test_health_check_runner_started_and_stopped(engine, monkeypatch):
    """Test that health check runner starts/stops with engine lifecycle."""
    start_mock = AsyncMock()
    stop_mock = AsyncMock()

    monkeypatch.setattr(engine._health_check_runner, "start", start_mock)
    monkeypatch.setattr(engine._health_check_runner, "stop", stop_mock)

    engine._heartbeat.start = AsyncMock(return_value=None)
    engine._status_reporter.start = AsyncMock(return_value=None)
    engine._system_maintenance.start_prune_loop = AsyncMock(
        return_value=asyncio.create_task(asyncio.sleep(0))
    )
    engine._heartbeat.stop = AsyncMock()
    engine._status_reporter.stop = AsyncMock()
    engine._system_maintenance.stop = AsyncMock()

    engine.running = False

    async def mock_run_loop():
        pass

    monkeypatch.setattr(engine, "_run_loop", mock_run_loop)

    async def mock_monitor_ws_health():
        pass

    monkeypatch.setattr(engine, "_monitor_ws_health", mock_monitor_ws_health)

    monkeypatch.setattr(engine, "_should_enable_streaming", lambda: False)

    await engine.start_background_tasks()
    start_mock.assert_called_once()

    await engine.shutdown()
    stop_mock.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_preserves_broker_calls_when_requested(engine):
    broker_calls = MagicMock()
    broker_calls.shutdown = MagicMock()
    engine._broker_calls = broker_calls
    engine._heartbeat.stop = AsyncMock()
    engine._status_reporter.stop = AsyncMock()
    engine._system_maintenance.stop = AsyncMock()
    engine._health_check_runner.stop = AsyncMock()

    engine.preserve_broker_calls_on_shutdown()
    await engine.shutdown()

    broker_calls.shutdown.assert_not_called()


def test_positions_to_risk_format(engine):
    """Test conversion of positions to risk manager dict format."""
    positions = {
        "BTC-USD": Position(
            symbol="BTC-USD",
            quantity=Decimal("0.5"),
            entry_price=Decimal("40000"),
            mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
            side="long",
        )
    }

    risk_format = engine._positions_to_risk_format(positions)
    assert risk_format["BTC-USD"]["quantity"] == Decimal("0.5")
    assert risk_format["BTC-USD"]["mark"] == Decimal("50000")
    assert not isinstance(risk_format["BTC-USD"], Position)


@pytest.mark.asyncio
async def test_risk_manager_receives_dict_format(engine, monkeypatch: pytest.MonkeyPatch):
    """Test that risk manager receives correctly formatted dicts."""
    mock_risk_manager = MagicMock()
    mock_risk_manager._start_of_day_equity = Decimal("1000.0")
    mock_risk_manager.check_mark_staleness.return_value = False
    mock_risk_manager.track_daily_pnl.return_value = False
    mock_risk_manager.is_reduce_only_mode.return_value = False
    mock_risk_manager.check_order.return_value = True
    engine.context.risk_manager = mock_risk_manager

    mock_validator = MagicMock()
    mock_validator.validate_order_request.return_value.is_valid = True
    monkeypatch.setattr(security_validate_module, "get_validator", lambda: mock_validator)

    engine.strategy.decide.return_value = Decision(Action.BUY, "test")
    engine.strategy.config.position_fraction = Decimal("0.1")

    engine.context.broker.list_positions.return_value = [
        Position(
            symbol="BTC-USD",
            quantity=Decimal("1.0"),
            entry_price=Decimal("40000"),
            mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
            side="long",
        )
    ]
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("10000"), available=Decimal("10000"))
    ]
    engine._state_collector.build_positions_dict.side_effect = (
        lambda positions: engine._positions_to_risk_format({pos.symbol: pos for pos in positions})
    )

    await engine._cycle()

    engine._order_validator.run_pre_trade_validation.assert_called_once()
    call_args = engine._order_validator.run_pre_trade_validation.call_args
    current_positions = call_args.kwargs["current_positions"]

    assert "BTC-USD" in current_positions
    assert isinstance(current_positions["BTC-USD"], dict)
    assert current_positions["BTC-USD"]["quantity"] == Decimal("1.0")


@pytest.mark.asyncio
async def test_runtime_guard_sweep_calls_guard_manager(engine, monkeypatch):
    """Test that runtime guard sweep calls GuardManager.run_runtime_guards."""
    engine._guard_manager = MagicMock()
    engine.running = True

    async def _sleep(_):
        engine.running = False
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await engine._runtime_guard_sweep()

    engine._guard_manager.run_runtime_guards.assert_called_once()


@pytest.mark.asyncio
async def test_runtime_guard_sweep_skips_without_guard_manager(engine, monkeypatch):
    """Test that runtime guard sweep handles missing guard manager gracefully."""
    engine._guard_manager = None
    engine.running = True

    async def _sleep(_):
        engine.running = False
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await engine._runtime_guard_sweep()


@pytest.mark.asyncio
async def test_runtime_guard_sweep_uses_config_interval(engine, monkeypatch):
    """Test that runtime guard sweep uses configured interval."""
    engine._guard_manager = MagicMock()
    engine.running = True
    engine.context.config.runtime_guard_interval = 30

    sleep_intervals = []

    async def _sleep(interval):
        sleep_intervals.append(interval)
        engine.running = False
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await engine._runtime_guard_sweep()

    assert sleep_intervals == [30]


@pytest.mark.asyncio
async def test_runtime_guard_sweep_handles_exceptions(engine, monkeypatch):
    """Test that runtime guard sweep continues after generic exceptions."""
    engine._guard_manager = MagicMock()
    engine._guard_manager.run_runtime_guards.side_effect = RuntimeError("test error")
    engine.running = True

    call_count = 0

    async def _sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            engine.running = False
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await engine._runtime_guard_sweep()

    assert engine._guard_manager.run_runtime_guards.call_count == 2


@pytest.mark.asyncio
async def test_unfilled_order_alert_emitted_once(engine) -> None:
    engine.context.risk_manager.config.unfilled_order_alert_seconds = 1
    engine.context.broker.list_orders.return_value = {
        "orders": [
            {
                "order_id": "order-1",
                "product_id": "BTC-USD",
                "side": "BUY",
                "status": "OPEN",
                "created_time": time.time() - 10,
            }
        ]
    }

    await engine._audit_orders()
    alert_events = [
        e for e in engine._event_store.list_events() if e.get("type") == "unfilled_order_alert"
    ]
    assert len(alert_events) == 1

    await engine._audit_orders()
    alert_events = [
        e for e in engine._event_store.list_events() if e.get("type") == "unfilled_order_alert"
    ]
    assert len(alert_events) == 1


@pytest.mark.asyncio
async def test_unfilled_order_alert_dedupes_client_and_order_ids(engine) -> None:
    engine.context.risk_manager.config.unfilled_order_alert_seconds = 1
    engine.context.broker.list_orders.side_effect = [
        {
            "orders": [
                {
                    "client_order_id": "submit-1",
                    "product_id": "BTC-USD",
                    "side": "BUY",
                    "status": "OPEN",
                    "created_time": time.time() - 10,
                }
            ]
        },
        {
            "orders": [
                {
                    "order_id": "order-1",
                    "client_order_id": "submit-1",
                    "product_id": "BTC-USD",
                    "side": "BUY",
                    "status": "OPEN",
                    "created_time": time.time() - 10,
                }
            ]
        },
    ]

    await engine._audit_orders()
    await engine._audit_orders()

    alert_events = [
        e for e in engine._event_store.list_events() if e.get("type") == "unfilled_order_alert"
    ]
    assert len(alert_events) == 1
    assert alert_events[0]["data"]["order_id"] == "submit-1"
