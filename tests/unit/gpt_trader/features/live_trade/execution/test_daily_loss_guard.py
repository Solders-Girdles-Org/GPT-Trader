"""Tests for daily loss guard behavior."""

import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.features.live_trade.execution.guards import RuntimeGuardState
from gpt_trader.features.live_trade.execution.guards.daily_loss import DailyLossGuard
from gpt_trader.features.live_trade.guard_errors import RiskGuardActionError


def _empty_state(equity: str = "10000") -> RuntimeGuardState:
    return RuntimeGuardState(
        timestamp=time.time(),
        balances=[],
        equity=Decimal(equity),
        positions=[],
        positions_pnl={},
        positions_dict={},
        guard_events=[],
    )


def test_guard_daily_loss_not_triggered(sample_guard_state, mock_risk_manager):
    mock_risk_manager.track_daily_pnl.return_value = False
    cancel_callback = MagicMock()
    invalidate_callback = MagicMock()
    guard = DailyLossGuard(
        risk_manager=mock_risk_manager,
        cancel_all_orders=cancel_callback,
        invalidate_cache=invalidate_callback,
    )

    guard.check(sample_guard_state)

    mock_risk_manager.track_daily_pnl.assert_called_once_with(
        sample_guard_state.equity, sample_guard_state.positions_pnl
    )
    cancel_callback.assert_not_called()
    invalidate_callback.assert_not_called()


@pytest.mark.usefixtures("disable_api_health_guard")
class TestDailyLossGuardViaManager:
    """The guard's callbacks are wired to the manager's order cancellation and cache."""

    def test_triggered_cancels_open_orders(self, guard_manager, mock_risk_manager, mock_broker):
        mock_risk_manager.track_daily_pnl.return_value = True
        mock_broker.cancel_order.return_value = True

        assert len(guard_manager.open_orders) == 2

        guard_manager.run_guards_for_state(_empty_state(), incremental=False)

        assert mock_broker.cancel_order.call_count == 2
        assert guard_manager.open_orders == []
        guard_manager._invalidate_cache_callback.assert_called()

    def test_cancel_failure_keeps_orders_tracked(
        self, guard_manager, mock_risk_manager, mock_broker
    ):
        mock_risk_manager.track_daily_pnl.return_value = True
        mock_broker.cancel_order.side_effect = Exception("Cancel failed")

        guard_manager.run_guards_for_state(_empty_state(), incremental=False)

        assert mock_broker.cancel_order.call_count == 2
        assert len(guard_manager.open_orders) == 2


class TestDailyLossGuardEdgeCases:
    def test_cancel_all_orders_raises_triggers_risk_guard_action_error(self, mock_risk_manager):
        from gpt_trader.features.live_trade.execution.guards.daily_loss import DailyLossGuard

        mock_risk_manager.track_daily_pnl.return_value = True

        cancel_callback = MagicMock(side_effect=Exception("Cancel failed"))
        invalidate_callback = MagicMock()

        guard = DailyLossGuard(
            risk_manager=mock_risk_manager,
            cancel_all_orders=cancel_callback,
            invalidate_cache=invalidate_callback,
        )

        state = RuntimeGuardState(
            timestamp=time.time(),
            balances=[],
            equity=Decimal("10000"),
            positions=[],
            positions_pnl={},
            positions_dict={},
            guard_events=[],
        )

        with pytest.raises(RiskGuardActionError) as exc_info:
            guard.check(state)

        assert exc_info.value.guard_name == "daily_loss"
        assert "cancel orders" in str(exc_info.value.message).lower()
        invalidate_callback.assert_not_called()

    def test_successful_trigger_calls_both_callbacks(self, mock_risk_manager):
        from gpt_trader.features.live_trade.execution.guards.daily_loss import DailyLossGuard

        mock_risk_manager.track_daily_pnl.return_value = True

        cancel_callback = MagicMock()
        invalidate_callback = MagicMock()

        guard = DailyLossGuard(
            risk_manager=mock_risk_manager,
            cancel_all_orders=cancel_callback,
            invalidate_cache=invalidate_callback,
        )

        state = RuntimeGuardState(
            timestamp=time.time(),
            balances=[],
            equity=Decimal("5000"),
            positions=[],
            positions_pnl={},
            positions_dict={},
            guard_events=[],
        )

        guard.check(state)

        cancel_callback.assert_called_once()
        invalidate_callback.assert_called_once()
