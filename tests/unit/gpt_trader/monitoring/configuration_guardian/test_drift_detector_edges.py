"""Edge-case tests for DriftDetector."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

import gpt_trader.monitoring.configuration_guardian.detector as detector_module
from gpt_trader.monitoring.configuration_guardian.detector import DriftDetector
from gpt_trader.monitoring.configuration_guardian.models import BaselineSnapshot, DriftEvent


def test_drift_summary_empty_and_state_mirror() -> None:
    detector = DriftDetector(BaselineSnapshot())

    summary = detector.get_drift_summary()

    assert summary == {
        "total_events": 0,
        "critical": 0,
        "high": 0,
        "last_event": None,
    }
    assert detector.get_current_state() == summary


def test_record_drift_events_logs_only_when_non_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = DriftDetector(BaselineSnapshot())
    event = DriftEvent(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        component="test",
        drift_type="critical_env_changed",
        severity="critical",
    )

    mock_logger = MagicMock()
    monkeypatch.setattr(detector_module, "logger", mock_logger)

    detector.record_drift_events([])
    detector.record_drift_events([event])

    assert detector.drift_history == [event]
    mock_logger.info.assert_called_once()


def test_drift_summary_counts_and_last_event_fields() -> None:
    detector = DriftDetector(BaselineSnapshot())
    first = DriftEvent(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        component="env",
        drift_type="critical_env_changed",
        severity="critical",
    )
    last = DriftEvent(
        timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        component="state",
        drift_type="risk_env_changed",
        severity="high",
    )

    detector.record_drift_events([first, last])
    summary = detector.get_drift_summary()

    assert summary["total_events"] == 2
    assert summary["critical"] == 1
    assert summary["high"] == 1
    assert summary["last_event"] == {
        "timestamp": last.timestamp.isoformat(),
        "component": last.component,
        "drift_type": last.drift_type,
        "severity": last.severity,
    }
