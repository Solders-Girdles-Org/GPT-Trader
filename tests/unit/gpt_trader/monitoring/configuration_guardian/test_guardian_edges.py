"""Edge-case tests for ConfigurationGuardian."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from gpt_trader.monitoring.configuration_guardian.guardian import ConfigurationGuardian
from gpt_trader.monitoring.configuration_guardian.models import BaselineSnapshot, DriftEvent


class _MonitorStub:
    def __init__(self, name: str, events: list[DriftEvent] | None = None) -> None:
        self.monitor_name = name
        self._events = events or []
        self.called = 0

    def check_changes(self) -> list[DriftEvent]:
        self.called += 1
        return list(self._events)


class _FailingMonitor:
    monitor_name = "failing_monitor"

    def __init__(self) -> None:
        self.called = 0

    def check_changes(self) -> list[DriftEvent]:
        self.called += 1
        raise RuntimeError("boom")


class _DetectorStub:
    def __init__(self, summary: dict | None = None) -> None:
        self.summary = summary or {"total_events": 0}
        self.recorded: list[list[DriftEvent]] = []

    def record_drift_events(self, events: list[DriftEvent]) -> None:
        self.recorded.append(list(events))

    def update_baseline(self, baseline: BaselineSnapshot) -> None:
        self.baseline = baseline

    def get_drift_summary(self) -> dict:
        return dict(self.summary)


@pytest.fixture
def guardian_logger_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    from gpt_trader.monitoring.configuration_guardian import guardian as guardian_module

    mock = MagicMock()
    monkeypatch.setattr(guardian_module, "logger", mock)
    return mock


def test_monitor_exception_logged_and_other_monitors_continue(
    guardian_logger_mock: MagicMock,
) -> None:
    baseline = BaselineSnapshot()
    good_event = DriftEvent(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        component="good",
        drift_type="ok",
        severity="low",
    )
    good_monitor = _MonitorStub("good_monitor", events=[good_event])
    failing_monitor = _FailingMonitor()
    detector = _DetectorStub()
    guardian = ConfigurationGuardian(
        baseline,
        monitors=[failing_monitor, good_monitor],
        detector=detector,
    )

    events = guardian.check()

    assert events == [good_event]
    assert failing_monitor.called == 1
    assert good_monitor.called == 1
    guardian_logger_mock.warning.assert_called_once()


def test_check_records_events_only_when_present() -> None:
    baseline = BaselineSnapshot()
    detector = _DetectorStub()
    guardian = ConfigurationGuardian(
        baseline,
        monitors=[_MonitorStub("empty_monitor")],
        detector=detector,
    )

    guardian.check()
    assert detector.recorded == []

    event = DriftEvent(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        component="monitor",
        drift_type="drift",
        severity="high",
    )
    detector_with_events = _DetectorStub()
    guardian_with_events = ConfigurationGuardian(
        baseline,
        monitors=[_MonitorStub("event_monitor", events=[event])],
        detector=detector_with_events,
    )

    guardian_with_events.check()
    assert detector_with_events.recorded == [[event]]


def test_reset_baseline_updates_monitors_and_logs_user(
    guardian_logger_mock: MagicMock,
) -> None:
    baseline = BaselineSnapshot(timestamp=datetime(2024, 1, 1, tzinfo=UTC))
    new_baseline = BaselineSnapshot(timestamp=datetime(2024, 1, 2, tzinfo=UTC))
    detector = _DetectorStub()
    monitor_with_update = Mock()
    monitor_with_update.monitor_name = "with_update"
    monitor_without_update = SimpleNamespace(monitor_name="without_update")

    guardian = ConfigurationGuardian(
        baseline,
        monitors=[monitor_with_update, monitor_without_update],
        detector=detector,
    )

    guardian.reset_baseline(new_baseline, user_id="user-1")

    monitor_with_update.update_baseline.assert_called_once_with(new_baseline)
    assert not hasattr(monitor_without_update, "update_baseline")
    guardian_logger_mock.info.assert_called_once()


def test_get_state_includes_baseline_monitor_count_and_summary() -> None:
    baseline = BaselineSnapshot(timestamp=datetime(2024, 1, 3, tzinfo=UTC))
    detector = _DetectorStub(summary={"total_events": 2, "critical": 1})
    guardian = ConfigurationGuardian(
        baseline,
        monitors=[_MonitorStub("m1"), _MonitorStub("m2")],
        detector=detector,
    )

    state = guardian.get_state()

    assert state["baseline_timestamp"] == baseline.timestamp.isoformat()
    assert state["monitor_count"] == 2
    assert state["drift_summary"] == {"total_events": 2, "critical": 1}
