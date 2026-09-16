"""Tests for mark staleness and risk metrics guards."""

import time
from unittest.mock import MagicMock

import pytest

from gpt_trader.features.live_trade.execution.guards.mark_staleness import MarkStalenessGuard
from gpt_trader.features.live_trade.execution.guards.risk_metrics import RiskMetricsGuard
from gpt_trader.features.live_trade.guard_errors import (
    RiskGuardComputationError,
    RiskGuardDataUnavailable,
    RiskGuardTelemetryError,
)


@pytest.fixture
def mark_staleness_guard(mock_broker, mock_risk_manager) -> MarkStalenessGuard:
    return MarkStalenessGuard(broker=mock_broker, risk_manager=mock_risk_manager)


@pytest.fixture
def risk_metrics_guard(mock_risk_manager) -> RiskMetricsGuard:
    return RiskMetricsGuard(risk_manager=mock_risk_manager)


def test_guard_mark_staleness_no_cache(
    mark_staleness_guard, sample_guard_state, mock_broker, mock_risk_manager
):
    del mock_broker._mark_cache

    mark_staleness_guard.check(sample_guard_state)
    mock_risk_manager.check_mark_staleness.assert_not_called()


def test_guard_mark_staleness_with_cache(
    mark_staleness_guard, sample_guard_state, mock_broker, mock_risk_manager
):
    mock_broker._mark_cache = MagicMock()
    mock_broker._mark_cache.get_mark.return_value = None
    mock_risk_manager.last_mark_update = {"BTC-PERP": time.time()}

    mark_staleness_guard.check(sample_guard_state)

    mock_risk_manager.check_mark_staleness.assert_called_with("BTC-PERP")


def test_guard_mark_staleness_fetch_failure(
    mark_staleness_guard, sample_guard_state, mock_broker, mock_risk_manager
):
    mock_broker._mark_cache = MagicMock()
    mock_broker._mark_cache.get_mark.side_effect = Exception("Cache error")
    mock_risk_manager.last_mark_update = {"BTC-PERP": time.time()}

    with pytest.raises(RiskGuardDataUnavailable):
        mark_staleness_guard.check(sample_guard_state)


def test_guard_risk_metrics_success(risk_metrics_guard, sample_guard_state, mock_risk_manager):
    risk_metrics_guard.check(sample_guard_state)

    mock_risk_manager.append_risk_metrics.assert_called_once()


def test_guard_risk_metrics_failure(risk_metrics_guard, sample_guard_state, mock_risk_manager):
    mock_risk_manager.append_risk_metrics.side_effect = Exception("Metrics error")

    with pytest.raises(RiskGuardTelemetryError):
        risk_metrics_guard.check(sample_guard_state)


def test_guard_risk_metrics_propagates_guard_error(
    risk_metrics_guard, sample_guard_state, mock_risk_manager
):
    error = RiskGuardComputationError(guard_name="risk_metrics", message="Test", details={})
    mock_risk_manager.append_risk_metrics.side_effect = error

    with pytest.raises(RiskGuardComputationError):
        risk_metrics_guard.check(sample_guard_state)
