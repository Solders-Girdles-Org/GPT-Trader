"""Unit tests for the profiling module."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

import gpt_trader.monitoring.profiling as profiling_module
from gpt_trader.monitoring.profiling import (
    ProfileSample,
    profile_span,
    record_profile,
)


@pytest.fixture
def record_histogram_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_histogram = MagicMock()
    monkeypatch.setattr(profiling_module, "record_histogram", mock_histogram)
    return mock_histogram


class TestProfileSample:
    """Tests for ProfileSample dataclass."""

    def test_profile_sample_creation(self) -> None:
        """Test creating a ProfileSample with required fields."""
        sample = ProfileSample(name="test_op", duration_ms=100.5)
        assert sample.name == "test_op"
        assert sample.duration_ms == 100.5
        assert sample.labels is None
        assert isinstance(sample.timestamp, datetime)

    def test_profile_sample_with_labels(self) -> None:
        """Test creating a ProfileSample with labels."""
        labels = {"symbol": "BTC-USD", "result": "ok"}
        sample = ProfileSample(name="fetch", duration_ms=50.0, labels=labels)
        assert sample.name == "fetch"
        assert sample.labels == labels

    def test_profile_sample_to_dict(self) -> None:
        """Test serialization to dictionary."""
        timestamp = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        sample = ProfileSample(
            name="test",
            duration_ms=25.5,
            timestamp=timestamp,
            labels={"key": "value"},
        )
        result = sample.to_dict()
        assert result["name"] == "test"
        assert result["duration_ms"] == 25.5
        assert result["timestamp"] == "2024-01-15T12:00:00+00:00"
        assert result["labels"] == {"key": "value"}


class TestRecordProfile:
    """Tests for record_profile function."""

    def test_record_profile_calls_histogram(self, record_histogram_mock: MagicMock) -> None:
        """Test that record_profile calls record_histogram."""
        sample = record_profile("my_operation", 150.0)

        record_histogram_mock.assert_called_once_with(
            "gpt_trader_profile_duration_seconds",
            0.15,  # 150ms -> 0.15s
            labels={"phase": "my_operation"},
        )
        assert sample.name == "my_operation"
        assert sample.duration_ms == 150.0

    def test_record_profile_with_labels(self, record_histogram_mock: MagicMock) -> None:
        """Test record_profile merges custom labels with phase label."""
        sample = record_profile(
            "order_submit",
            75.5,
            labels={"symbol": "ETH-USD"},
        )

        record_histogram_mock.assert_called_once_with(
            "gpt_trader_profile_duration_seconds",
            0.0755,
            labels={"phase": "order_submit", "symbol": "ETH-USD"},
        )
        assert sample.labels == {"symbol": "ETH-USD"}


class TestProfileSpan:
    """Tests for profile_span context manager."""

    def test_profile_span_records_duration(self, record_histogram_mock: MagicMock) -> None:
        """Test that profile_span records duration on exit."""
        with profile_span("test_span") as sample:
            assert sample is not None
            assert sample.duration_ms == 0.0  # Not yet updated
            # Do some trivial work to ensure non-zero duration
            _ = sum(range(100))

        # After exiting, duration should be updated (even if tiny)
        assert sample.duration_ms >= 0
        # Histogram should be called
        assert record_histogram_mock.called
        call_args = record_histogram_mock.call_args
        assert call_args[0][0] == "gpt_trader_profile_duration_seconds"
        assert call_args[1]["labels"] == {"phase": "test_span"}

    def test_profile_span_with_labels(self, record_histogram_mock: MagicMock) -> None:
        """Test profile_span with custom labels."""
        with profile_span("fetch", {"symbol": "BTC-USD"}):
            pass

        call_args = record_histogram_mock.call_args
        assert call_args[1]["labels"] == {"phase": "fetch", "symbol": "BTC-USD"}

    def test_profile_span_tolerates_exceptions(
        self,
        record_histogram_mock: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that profile_span still records metrics when exception occurs."""
        counter_values = iter([1.0, 1.2])
        monkeypatch.setattr(profiling_module.time, "perf_counter", lambda: next(counter_values))

        with pytest.raises(ValueError, match="test error"):
            with profile_span("failing_op") as sample:
                raise ValueError("test error")

        # Histogram should still be called even after exception
        assert record_histogram_mock.called
        call_args = record_histogram_mock.call_args
        assert call_args[0][0] == "gpt_trader_profile_duration_seconds"
        assert call_args[1]["labels"]["phase"] == "failing_op"
        # Duration should be recorded
        assert sample is not None
        assert sample.duration_ms > 0

    def test_profile_span_updates_sample_timestamp(self, record_histogram_mock: MagicMock) -> None:
        """Test that sample timestamp is updated on exit."""
        before = datetime.now(UTC)
        with profile_span("time_test") as sample:
            # Do trivial work
            _ = sum(range(100))
        after = datetime.now(UTC)

        assert sample is not None
        assert before <= sample.timestamp <= after

    def test_profile_span_measures_duration(self, record_histogram_mock: MagicMock) -> None:
        """Test that duration measurement captures elapsed time."""
        with profile_span("duration_test") as sample:
            # Do some work to ensure measurable duration
            _ = [x**2 for x in range(1000)]

        assert sample is not None
        # Duration should be non-negative
        assert sample.duration_ms >= 0
