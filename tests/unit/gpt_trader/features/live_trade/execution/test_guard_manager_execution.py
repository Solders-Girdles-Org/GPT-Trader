"""Tests for GuardManager order cancellation, safe-run, guard steps, and daily-loss escalation."""

import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import gpt_trader.features.live_trade.execution.guard_manager as guard_manager_module
from gpt_trader.features.live_trade.execution.guards import RuntimeGuardState
from gpt_trader.features.live_trade.guard_errors import (
    RiskGuardActionError,
    RiskGuardComputationError,
    RiskGuardDataUnavailable,
    RiskGuardTelemetryError,
)


def test_cancel_all_orders_success(guard_manager, mock_broker):
    mock_broker.cancel_order.return_value = True

    cancelled_count = guard_manager.cancel_all_orders()

    assert cancelled_count == 2
    assert mock_broker.cancel_order.call_count == 2
    assert len(guard_manager.open_orders) == 0
    guard_manager._invalidate_cache_callback.assert_called()


def test_cancel_all_orders_partial_failure(guard_manager, mock_broker):
    mock_broker.cancel_order.side_effect = [Exception("Failed"), True]

    cancelled_count = guard_manager.cancel_all_orders()

    assert cancelled_count == 1
    assert "order1" in guard_manager.open_orders
    assert "order2" not in guard_manager.open_orders


def test_cancel_all_orders_uses_broker_list_orders(guard_manager, mock_broker):
    mock_broker.list_orders = MagicMock(
        return_value={"orders": [{"id": "order3"}, {"id": "order4"}]}
    )
    mock_broker.cancel_order.return_value = True

    cancelled_count = guard_manager.cancel_all_orders()

    assert cancelled_count == 2
    cancelled_ids = [call_args[0][0] for call_args in mock_broker.cancel_order.call_args_list]
    assert cancelled_ids == ["order3", "order4"]
    assert guard_manager.open_orders == []


def test_safe_run_runtime_guards_recoverable_error(
    guard_manager, mock_risk_manager, monkeypatch: pytest.MonkeyPatch
):
    error = RiskGuardTelemetryError(guard_name="test", message="Recoverable", details={})

    mock_run = MagicMock(side_effect=error)
    monkeypatch.setattr(guard_manager, "run_runtime_guards", mock_run)
    guard_manager.safe_run_runtime_guards()

    mock_risk_manager.set_reduce_only_mode.assert_not_called()


def test_safe_run_runtime_guards_unrecoverable_error(
    guard_manager, mock_risk_manager, monkeypatch: pytest.MonkeyPatch
):
    error = RiskGuardActionError(guard_name="test", message="Fatal", details={})

    mock_run = MagicMock(side_effect=error)
    monkeypatch.setattr(guard_manager, "run_runtime_guards", mock_run)
    guard_manager.safe_run_runtime_guards()

    mock_risk_manager.set_reduce_only_mode.assert_called_with(True, reason="guard_failure")
    guard_manager._invalidate_cache_callback.assert_called()


def test_safe_run_runtime_guards_unexpected_error(guard_manager, monkeypatch: pytest.MonkeyPatch):
    mock_run = MagicMock(side_effect=ValueError("Unexpected"))
    monkeypatch.setattr(guard_manager, "run_runtime_guards", mock_run)
    guard_manager.safe_run_runtime_guards()
    mock_run.assert_called_once_with(force_full=False)


def test_safe_run_runtime_guards_reduce_only_failure(
    guard_manager, mock_risk_manager, monkeypatch: pytest.MonkeyPatch
):
    error = RiskGuardActionError(guard_name="test", message="Fatal", details={})
    mock_risk_manager.set_reduce_only_mode.side_effect = Exception("Failed")

    mock_run = MagicMock(side_effect=error)
    monkeypatch.setattr(guard_manager, "run_runtime_guards", mock_run)
    guard_manager.safe_run_runtime_guards()

    guard_manager._invalidate_cache_callback.assert_called()


@pytest.mark.usefixtures("disable_api_health_guard")
def test_guard_daily_loss_escalation_boundary_triggers_once(
    guard_manager, mock_risk_manager, mock_broker
):
    mock_risk_manager.set_reduce_only_mode = MagicMock()
    reduce_only_triggered = False

    def track_daily_pnl(equity, positions_pnl):
        nonlocal reduce_only_triggered
        if equity < Decimal("9500"):
            if not reduce_only_triggered:
                mock_risk_manager.set_reduce_only_mode(True, reason="daily_loss_limit_breached")
                reduce_only_triggered = True
            return True
        return False

    mock_risk_manager.track_daily_pnl.side_effect = track_daily_pnl

    safe_state = RuntimeGuardState(
        timestamp=time.time(),
        balances=[],
        equity=Decimal("9600"),
        positions=[],
        positions_pnl={},
        positions_dict={},
        guard_events=[],
    )

    guard_manager.run_guards_for_state(safe_state, incremental=False)

    assert mock_broker.cancel_order.call_count == 0
    mock_risk_manager.set_reduce_only_mode.assert_not_called()
    guard_manager._invalidate_cache_callback.assert_not_called()

    breach_state = RuntimeGuardState(
        timestamp=time.time(),
        balances=[],
        equity=Decimal("9300"),
        positions=[],
        positions_pnl={},
        positions_dict={},
        guard_events=[],
    )

    guard_manager.run_guards_for_state(breach_state, incremental=False)

    assert mock_broker.cancel_order.call_count == 2
    assert guard_manager.open_orders == []
    mock_risk_manager.set_reduce_only_mode.assert_called_once_with(
        True, reason="daily_loss_limit_breached"
    )
    assert guard_manager._invalidate_cache_callback.call_count == 2

    guard_manager.run_guards_for_state(breach_state, incremental=False)

    assert mock_broker.cancel_order.call_count == 2
    assert guard_manager._invalidate_cache_callback.call_count == 3
    mock_risk_manager.set_reduce_only_mode.assert_called_once()


@pytest.fixture
def record_guard_success_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_success = MagicMock()
    monkeypatch.setattr(guard_manager_module, "record_guard_success", mock_success)
    return mock_success


@pytest.fixture
def record_guard_failure_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_failure = MagicMock()
    monkeypatch.setattr(guard_manager_module, "record_guard_failure", mock_failure)
    return mock_failure


def test_run_guard_step_success(guard_manager, record_guard_success_mock):
    func = MagicMock()

    guard_manager.run_guard_step("test_guard", func)

    func.assert_called_once()
    record_guard_success_mock.assert_called_with("test_guard")


def test_run_guard_step_recoverable_error(guard_manager, record_guard_failure_mock):
    error = RiskGuardTelemetryError(
        guard_name="test_guard",
        message="Recoverable error",
        details={},
    )
    func = MagicMock(side_effect=error)

    guard_manager.run_guard_step("test_guard", func)

    record_guard_failure_mock.assert_called_once()


def test_run_guard_step_unrecoverable_error(guard_manager, record_guard_failure_mock):
    error = RiskGuardActionError(
        guard_name="test_guard",
        message="Fatal error",
        details={},
    )
    func = MagicMock(side_effect=error)

    with pytest.raises(RiskGuardActionError):
        guard_manager.run_guard_step("test_guard", func)


def test_run_guard_step_unexpected_error(guard_manager, record_guard_failure_mock):
    func = MagicMock(side_effect=ValueError("Unexpected"))

    with pytest.raises(RiskGuardComputationError):
        guard_manager.run_guard_step("test_guard", func)

    assert record_guard_failure_mock.called


def test_run_runtime_guards_incremental_resets_guard_events(
    guard_manager, mock_broker, mock_risk_manager, monkeypatch: pytest.MonkeyPatch
):
    api_guard = next((guard for guard in guard_manager._guards if guard.name == "api_health"), None)
    if api_guard is not None:
        monkeypatch.setattr(api_guard, "check", lambda state, incremental=False: None)
    mock_broker.client = None
    mock_broker._client = None

    mock_risk_manager.last_mark_update = {"BTC-PERP": time.time()}
    mock_risk_manager.config.volatility_window_periods = 20

    mock_candle = MagicMock()
    mock_candle.close = Decimal("50000")
    mock_broker.get_candles.return_value = [mock_candle] * 20

    first_outcome = MagicMock()
    first_outcome.triggered = True
    first_outcome.to_payload.return_value = {
        "triggered": True,
        "symbol": "BTC-PERP",
        "reason": "volatility spike",
    }
    second_outcome = MagicMock()
    second_outcome.triggered = False
    second_outcome.to_payload.return_value = {
        "triggered": False,
        "symbol": "BTC-PERP",
        "reason": "calm",
    }
    mock_risk_manager.check_volatility_circuit_breaker.side_effect = [
        first_outcome,
        second_outcome,
    ]

    full_state = guard_manager.run_runtime_guards(force_full=True)

    assert len(full_state.guard_events) == 1
    assert full_state.guard_events[0] == first_outcome.to_payload.return_value

    incremental_state = guard_manager.run_runtime_guards(force_full=False)

    assert incremental_state.guard_events == []
    assert mock_risk_manager.check_volatility_circuit_breaker.call_count == 2
    assert mock_broker.get_candles.call_count == 2


def test_run_runtime_guards_preserves_invalidation_from_guard_actions(
    guard_manager, monkeypatch: pytest.MonkeyPatch
):
    state = RuntimeGuardState(
        timestamp=time.time(),
        balances=[],
        equity=Decimal("10000"),
        positions=[],
        positions_pnl={},
        positions_dict={},
        guard_events=[],
    )
    monkeypatch.setattr(guard_manager, "collect_runtime_guard_state", lambda: state)

    def invalidate_cache(_state: RuntimeGuardState, _incremental: bool) -> None:
        guard_manager._cache.invalidate()

    monkeypatch.setattr(guard_manager, "run_guards_for_state", invalidate_cache)

    observed_state = guard_manager.run_runtime_guards(force_full=True)

    assert observed_state is state
    assert guard_manager._runtime_guard_state is None
    assert guard_manager._runtime_guard_dirty is True


def test_run_runtime_guards_handles_non_dataclass_cached_state(
    guard_manager, monkeypatch: pytest.MonkeyPatch
):
    class LegacyState:
        def __init__(self) -> None:
            self.guard_events = [{"reason": "legacy"}]

    state = LegacyState()
    guard_manager._cache.update(state, time.time())  # type: ignore[arg-type]
    monkeypatch.setattr(guard_manager, "run_guards_for_state", lambda *_: None)

    observed_state = guard_manager.run_runtime_guards(force_full=False)

    assert observed_state is state
    assert state.guard_events == []


class TestGuardManagerEdgeCases:
    def test_data_unavailable_is_recorded_and_surfaces(
        self, guard_manager, sample_guard_state, monkeypatch: pytest.MonkeyPatch
    ):
        error = RiskGuardDataUnavailable(
            guard_name="test_guard",
            message="Data temporarily unavailable",
            details={"reason": "network"},
        )
        func = MagicMock(side_effect=error)

        mock_record = MagicMock()
        monkeypatch.setattr(guard_manager_module, "record_guard_failure", mock_record)

        guard_manager.run_guard_step("test_guard", func)

        mock_record.assert_called_once()
        recorded_error = mock_record.call_args[0][0]
        assert recorded_error.guard_name == "test_guard"
        assert recorded_error.recoverable is True

    def test_guard_order_is_stable(self, guard_manager):
        expected_order = [
            "pnl_telemetry",
            "daily_loss",
            "liquidation_buffer",
            "mark_staleness",
            "risk_metrics",
            "volatility_circuit_breaker",
            "api_health",
        ]

        actual_order = [guard.name for guard in guard_manager._guards]
        assert actual_order == expected_order

    def test_run_guards_for_state_stops_on_unrecoverable_error(
        self, guard_manager, sample_guard_state
    ):
        call_order: list[str] = []

        def make_check(name: str, should_fail: bool = False):
            def check(state, incremental: bool = False):
                call_order.append(name)
                if should_fail:
                    raise RiskGuardActionError(
                        guard_name=name,
                        message="Fatal",
                        details={},
                    )

            return check

        guard_manager._guards = []
        for i, name in enumerate(["guard_a", "guard_b", "guard_c"]):
            mock_guard = MagicMock()
            mock_guard.name = name
            mock_guard.check = make_check(name, should_fail=(i == 1))
            guard_manager._guards.append(mock_guard)

        with pytest.raises(RiskGuardActionError):
            guard_manager.run_guards_for_state(sample_guard_state, incremental=False)

        assert call_order == ["guard_a", "guard_b"]
