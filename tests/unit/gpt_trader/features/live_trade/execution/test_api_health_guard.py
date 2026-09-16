"""Tests for ApiHealthGuard behavior."""

from unittest.mock import MagicMock

import pytest

from gpt_trader.features.live_trade.execution.guards.api_health import ApiHealthGuard
from gpt_trader.features.live_trade.guard_errors import (
    RiskGuardDataUnavailable,
    RiskLimitExceeded,
)


def _broker_with_client(mock_client: MagicMock) -> MagicMock:
    broker = MagicMock()
    broker.list_balances.return_value = []
    broker.list_positions.return_value = []
    broker.client = mock_client
    return broker


def test_api_health_guard_skips_without_client(mock_risk_manager, sample_guard_state):
    broker = MagicMock(spec=["list_balances", "list_positions", "cancel_order"])
    broker.list_balances.return_value = []
    broker.list_positions.return_value = []

    guard = ApiHealthGuard(broker=broker, risk_manager=mock_risk_manager)

    guard.check(sample_guard_state)
    assert guard._client is None
    mock_risk_manager.set_reduce_only_mode.assert_not_called()


def test_api_health_guard_triggers_on_open_breaker(mock_risk_manager, sample_guard_state):
    mock_client = MagicMock()
    mock_client.get_resilience_status.return_value = {
        "metrics": {"error_rate": 0.05},
        "circuit_breakers": {
            "orders": {"state": "open"},
            "market_data": {"state": "closed"},
        },
        "rate_limit_usage": 0.5,
    }

    guard = ApiHealthGuard(broker=_broker_with_client(mock_client), risk_manager=mock_risk_manager)

    with pytest.raises(RiskLimitExceeded) as exc_info:
        guard.check(sample_guard_state)

    assert "circuit breakers open" in str(exc_info.value)
    assert "orders" in str(exc_info.value.details)


def test_api_health_guard_triggers_on_high_error_rate(mock_risk_manager, sample_guard_state):
    mock_client = MagicMock()
    mock_client.get_resilience_status.return_value = {
        "metrics": {"error_rate": 0.25},
        "circuit_breakers": {},
        "rate_limit_usage": 0.5,
    }

    mock_risk_manager.config.api_error_rate_threshold = 0.2

    guard = ApiHealthGuard(broker=_broker_with_client(mock_client), risk_manager=mock_risk_manager)

    with pytest.raises(RiskLimitExceeded) as exc_info:
        guard.check(sample_guard_state)

    assert "error rate" in str(exc_info.value)


def test_api_health_guard_triggers_on_high_rate_limit_usage(mock_risk_manager, sample_guard_state):
    mock_client = MagicMock()
    mock_client.get_resilience_status.return_value = {
        "metrics": {"error_rate": 0.05},
        "circuit_breakers": {},
        "rate_limit_usage": 0.95,
    }

    mock_risk_manager.config.api_rate_limit_usage_threshold = 0.9

    guard = ApiHealthGuard(broker=_broker_with_client(mock_client), risk_manager=mock_risk_manager)

    with pytest.raises(RiskLimitExceeded) as exc_info:
        guard.check(sample_guard_state)

    assert "rate limit usage" in str(exc_info.value)


def test_api_health_guard_handles_status_fetch_failure(mock_risk_manager, sample_guard_state):
    mock_client = MagicMock()
    mock_client.get_resilience_status.side_effect = Exception("Network error")

    guard = ApiHealthGuard(broker=_broker_with_client(mock_client), risk_manager=mock_risk_manager)

    with pytest.raises(RiskGuardDataUnavailable) as exc_info:
        guard.check(sample_guard_state)

    assert "Failed to get API resilience status" in str(exc_info.value)


def test_api_health_guard_passes_when_healthy(mock_risk_manager, sample_guard_state):
    mock_client = MagicMock()
    mock_client.get_resilience_status.return_value = {
        "metrics": {"error_rate": 0.05},
        "circuit_breakers": {
            "orders": {"state": "closed"},
            "market_data": {"state": "closed"},
        },
        "rate_limit_usage": 0.5,
    }

    mock_risk_manager.config.api_error_rate_threshold = 0.2
    mock_risk_manager.config.api_rate_limit_usage_threshold = 0.9

    guard = ApiHealthGuard(broker=_broker_with_client(mock_client), risk_manager=mock_risk_manager)

    guard.check(sample_guard_state)
    mock_client.get_resilience_status.assert_called_once()
    mock_risk_manager.set_reduce_only_mode.assert_not_called()
