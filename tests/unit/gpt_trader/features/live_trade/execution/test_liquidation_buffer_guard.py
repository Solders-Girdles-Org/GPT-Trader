"""Tests for liquidation buffer guard behavior."""

import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.features.live_trade.execution.guards import RuntimeGuardState
from gpt_trader.features.live_trade.execution.guards.liquidation_buffer import (
    LiquidationBufferGuard,
)
from gpt_trader.features.live_trade.guard_errors import (
    RiskGuardDataCorrupt,
    RiskGuardDataUnavailable,
)


@pytest.fixture
def liquidation_buffer_guard(mock_broker, mock_risk_manager) -> LiquidationBufferGuard:
    return LiquidationBufferGuard(broker=mock_broker, risk_manager=mock_risk_manager)


def test_guard_liquidation_buffers_basic(
    liquidation_buffer_guard, sample_guard_state, mock_risk_manager
):
    liquidation_buffer_guard.check(sample_guard_state, incremental=True)

    mock_risk_manager.check_liquidation_buffer.assert_called_once()


def test_guard_liquidation_buffers_full_with_risk_info(
    liquidation_buffer_guard, sample_guard_state, mock_broker, mock_risk_manager
):
    mock_broker.get_position_risk.return_value = {"liquidation_price": "45000"}

    liquidation_buffer_guard.check(sample_guard_state, incremental=False)

    mock_broker.get_position_risk.assert_called_once()
    mock_risk_manager.check_liquidation_buffer.assert_called_once()


def test_guard_liquidation_buffers_corrupt_data(liquidation_buffer_guard):
    bad_position = MagicMock()
    bad_position.symbol = "BAD"
    bad_position.mark_price = "invalid"
    del bad_position.quantity

    state = RuntimeGuardState(
        timestamp=time.time(),
        balances=[],
        equity=Decimal("10000"),
        positions=[bad_position],
        positions_pnl={},
        positions_dict={},
        guard_events=[],
    )

    with pytest.raises(RiskGuardDataCorrupt):
        liquidation_buffer_guard.check(state, incremental=True)


def test_guard_liquidation_buffers_risk_fetch_failure(
    liquidation_buffer_guard, sample_guard_state, mock_broker
):
    mock_broker.get_position_risk.side_effect = Exception("API error")

    with pytest.raises(RiskGuardDataUnavailable):
        liquidation_buffer_guard.check(sample_guard_state, incremental=False)


class TestLiquidationBufferGuardEdgeCases:
    def test_incremental_mode_does_not_call_get_position_risk(
        self, mock_broker, mock_risk_manager, mock_position
    ):
        from gpt_trader.features.live_trade.execution.guards.liquidation_buffer import (
            LiquidationBufferGuard,
        )

        mock_broker.get_position_risk = MagicMock(return_value={"liquidation_price": "45000"})
        guard = LiquidationBufferGuard(broker=mock_broker, risk_manager=mock_risk_manager)

        state = RuntimeGuardState(
            timestamp=time.time(),
            balances=[],
            equity=Decimal("10000"),
            positions=[mock_position],
            positions_pnl={},
            positions_dict={},
            guard_events=[],
        )

        guard.check(state, incremental=True)

        mock_broker.get_position_risk.assert_not_called()
        mock_risk_manager.check_liquidation_buffer.assert_called_once()

    def test_check_liquidation_buffer_receives_correct_pos_data(
        self, mock_broker, mock_risk_manager
    ):
        from gpt_trader.features.live_trade.execution.guards.liquidation_buffer import (
            LiquidationBufferGuard,
        )

        position = MagicMock()
        position.symbol = "ETH-PERP"
        position.quantity = "2.5"
        position.mark_price = "3000.50"

        mock_broker.get_position_risk.return_value = {"liquidation_price": "2500.00"}
        guard = LiquidationBufferGuard(broker=mock_broker, risk_manager=mock_risk_manager)

        state = RuntimeGuardState(
            timestamp=time.time(),
            balances=[],
            equity=Decimal("50000"),
            positions=[position],
            positions_pnl={},
            positions_dict={},
            guard_events=[],
        )

        guard.check(state, incremental=False)

        mock_risk_manager.check_liquidation_buffer.assert_called_once()
        symbol, pos_data, equity = mock_risk_manager.check_liquidation_buffer.call_args[0]

        assert symbol == "ETH-PERP"
        assert pos_data["quantity"] == Decimal("2.5")
        assert pos_data["mark"] == Decimal("3000.50")
        assert pos_data["liquidation_price"] == "2500.00"
        assert equity == Decimal("50000")

    def test_invalid_mark_price_raises_data_corrupt(self, mock_broker, mock_risk_manager):
        from gpt_trader.features.live_trade.execution.guards.liquidation_buffer import (
            LiquidationBufferGuard,
        )

        position = MagicMock()
        position.symbol = "BAD-PERP"
        position.quantity = "1.0"
        position.mark_price = "not-a-number"

        guard = LiquidationBufferGuard(broker=mock_broker, risk_manager=mock_risk_manager)

        state = RuntimeGuardState(
            timestamp=time.time(),
            balances=[],
            equity=Decimal("10000"),
            positions=[position],
            positions_pnl={},
            positions_dict={},
            guard_events=[],
        )

        with pytest.raises(RiskGuardDataCorrupt) as exc_info:
            guard.check(state, incremental=True)

        assert exc_info.value.guard_name == "liquidation_buffer"
        assert "BAD" in str(exc_info.value.details.get("symbol", ""))
