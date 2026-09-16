"""Tests for PnLTelemetryGuard behavior."""

from unittest.mock import MagicMock

import pytest

import gpt_trader.features.live_trade.execution.guards.pnl_telemetry as pnl_telemetry_module
from gpt_trader.features.live_trade.execution.guards.pnl_telemetry import PnLTelemetryGuard
from gpt_trader.features.live_trade.guard_errors import RiskGuardTelemetryError


@pytest.fixture
def plog_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_plog = MagicMock()
    monkeypatch.setattr(pnl_telemetry_module, "_get_plog", lambda: mock_plog)
    return mock_plog


def test_pnl_telemetry_guard_logs_pnl(sample_guard_state, plog_mock):
    PnLTelemetryGuard().check(sample_guard_state)

    plog_mock.log_pnl.assert_called_once()


def test_pnl_telemetry_guard_failure_raises(sample_guard_state, plog_mock):
    plog_mock.log_pnl.side_effect = Exception("Telemetry failed")

    with pytest.raises(RiskGuardTelemetryError) as exc_info:
        PnLTelemetryGuard().check(sample_guard_state)

    assert "BTC-PERP" in str(exc_info.value.details)
