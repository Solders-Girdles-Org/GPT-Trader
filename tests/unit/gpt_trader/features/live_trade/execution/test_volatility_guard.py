"""Tests for volatility circuit breaker guard behavior."""

import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.features.live_trade.execution.guards import RuntimeGuardState
from gpt_trader.features.live_trade.execution.guards.volatility import VolatilityGuard
from gpt_trader.features.live_trade.guard_errors import RiskGuardDataUnavailable


@pytest.fixture
def volatility_guard(mock_broker, mock_risk_manager) -> VolatilityGuard:
    return VolatilityGuard(broker=mock_broker, risk_manager=mock_risk_manager)


def test_guard_volatility_skips_short_window(
    volatility_guard, sample_guard_state, mock_broker, mock_risk_manager
):
    mock_risk_manager.config.volatility_window_periods = 3

    volatility_guard.check(sample_guard_state)

    mock_broker.get_candles.assert_not_called()


def test_guard_volatility_skips_no_symbols(volatility_guard, mock_broker, mock_risk_manager):
    state = RuntimeGuardState(
        timestamp=time.time(),
        balances=[],
        equity=Decimal("10000"),
        positions=[],
        positions_pnl={},
        positions_dict={},
        guard_events=[],
    )
    mock_risk_manager.last_mark_update = {}

    volatility_guard.check(state)

    mock_broker.get_candles.assert_not_called()


def test_guard_volatility_checks_symbols(
    volatility_guard, sample_guard_state, mock_broker, mock_risk_manager
):
    mock_risk_manager.last_mark_update = {"BTC-PERP": time.time()}
    mock_risk_manager.config.volatility_window_periods = 20

    mock_candle = MagicMock()
    mock_candle.close = Decimal("50000")
    mock_broker.get_candles.return_value = [mock_candle] * 20

    mock_risk_manager.check_volatility_circuit_breaker.return_value = MagicMock(triggered=False)

    volatility_guard.check(sample_guard_state)

    mock_broker.get_candles.assert_called()
    mock_risk_manager.check_volatility_circuit_breaker.assert_called()


def test_guard_volatility_records_triggered_events(
    volatility_guard, sample_guard_state, mock_broker, mock_risk_manager
):
    mock_risk_manager.last_mark_update = {"BTC-PERP": time.time()}
    mock_risk_manager.config.volatility_window_periods = 20

    mock_candle = MagicMock()
    mock_candle.close = Decimal("50000")
    mock_broker.get_candles.return_value = [mock_candle] * 20

    outcome = MagicMock()
    outcome.triggered = True
    outcome.to_payload.return_value = {"type": "volatility_breach"}
    mock_risk_manager.check_volatility_circuit_breaker.return_value = outcome

    volatility_guard.check(sample_guard_state)

    assert len(sample_guard_state.guard_events) == 1
    assert sample_guard_state.guard_events[0]["type"] == "volatility_breach"


def test_guard_volatility_fetch_failure(
    volatility_guard, sample_guard_state, mock_broker, mock_risk_manager
):
    mock_risk_manager.last_mark_update = {"BTC-PERP": time.time()}
    mock_risk_manager.config.volatility_window_periods = 20
    mock_broker.get_candles.side_effect = Exception("API error")

    with pytest.raises(RiskGuardDataUnavailable):
        volatility_guard.check(sample_guard_state)


class TestVolatilityGuardEdgeCases:
    def test_broker_missing_get_candles_skips_without_error(
        self, mock_risk_manager, sample_guard_state
    ):
        from gpt_trader.features.live_trade.execution.guards.volatility import VolatilityGuard

        broker = MagicMock(spec=["list_balances", "list_positions"])
        mock_risk_manager.last_mark_update = {"BTC-PERP": time.time()}
        mock_risk_manager.config.volatility_window_periods = 20

        guard = VolatilityGuard(broker=broker, risk_manager=mock_risk_manager)
        guard.check(sample_guard_state, incremental=False)

        mock_risk_manager.check_volatility_circuit_breaker.assert_not_called()

    def test_candles_less_than_window_does_not_call_circuit_breaker(
        self, mock_broker, mock_risk_manager, sample_guard_state
    ):
        from gpt_trader.features.live_trade.execution.guards.volatility import VolatilityGuard

        mock_risk_manager.last_mark_update = {"BTC-PERP": time.time()}
        mock_risk_manager.config.volatility_window_periods = 20

        mock_candle = MagicMock()
        mock_candle.close = Decimal("50000")
        mock_broker.get_candles.return_value = [mock_candle] * 10

        guard = VolatilityGuard(broker=mock_broker, risk_manager=mock_risk_manager)
        guard.check(sample_guard_state, incremental=False)

        mock_broker.get_candles.assert_called()
        mock_risk_manager.check_volatility_circuit_breaker.assert_not_called()

    def test_multiple_symbols_one_fails_raises_with_failures_list(
        self, mock_broker, mock_risk_manager
    ):
        from gpt_trader.features.live_trade.execution.guards.volatility import VolatilityGuard

        mock_risk_manager.last_mark_update = {
            "BTC-PERP": time.time(),
            "ETH-PERP": time.time(),
        }
        mock_risk_manager.config.volatility_window_periods = 20

        def get_candles_side_effect(symbol, **kwargs):
            if symbol == "ETH-PERP":
                raise ConnectionError("Network error")
            mock_candle = MagicMock()
            mock_candle.close = Decimal("50000")
            return [mock_candle] * 20

        mock_broker.get_candles.side_effect = get_candles_side_effect
        mock_risk_manager.check_volatility_circuit_breaker.return_value = MagicMock(triggered=False)

        guard = VolatilityGuard(broker=mock_broker, risk_manager=mock_risk_manager)

        state = RuntimeGuardState(
            timestamp=time.time(),
            balances=[],
            equity=Decimal("10000"),
            positions=[],
            positions_pnl={},
            positions_dict={},
            guard_events=[],
        )

        with pytest.raises(RiskGuardDataUnavailable) as exc_info:
            guard.check(state, incremental=False)

        assert exc_info.value.guard_name == "volatility_circuit_breaker"
        failures = exc_info.value.details.get("failures", [])
        assert len(failures) == 1
        assert failures[0]["symbol"] == "ETH-PERP"
