"""
Lightweight profiling hooks for performance monitoring.

Provides ProfileSample dataclass and profile_span context manager for
timing critical code paths and recording metrics to Prometheus histograms.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from gpt_trader.monitoring.metrics_collector import record_histogram


@dataclass
class ProfileSample:
    """A single profiling sample representing a timed operation.

    Attributes:
        name: Name of the profiled operation (e.g., "fetch_positions")
        duration_ms: Duration of the operation in milliseconds
        timestamp: When the sample was recorded (UTC)
        labels: Optional labels for metric categorization
    """

    name: str
    duration_ms: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    labels: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "labels": self.labels,
        }


def record_profile(
    name: str,
    duration_ms: float,
    labels: dict[str, str] | None = None,
) -> ProfileSample:
    """Record a profiling sample and emit to Prometheus histogram.

    Converts duration from milliseconds to seconds for the histogram
    (following Prometheus naming conventions for _seconds suffix).

    Args:
        name: Name of the profiled operation
        duration_ms: Duration in milliseconds
        labels: Optional labels for metric categorization

    Returns:
        The ProfileSample that was recorded
    """
    duration_seconds = duration_ms / 1000.0

    # Record to histogram with standardized metric name
    record_histogram(
        "gpt_trader_profile_duration_seconds",
        duration_seconds,
        labels={"phase": name, **(labels or {})},
    )

    return ProfileSample(
        name=name,
        duration_ms=duration_ms,
        labels=labels,
    )


@contextmanager
def profile_span(
    name: str,
    labels: dict[str, str] | None = None,
) -> Iterator[ProfileSample | None]:
    """Context manager for timing a code block and recording metrics.

    Automatically records the duration to the Prometheus histogram
    when the block exits (including on exception).

    Args:
        name: Name of the profiled operation (used as "phase" label)
        labels: Optional additional labels for metric categorization

    Yields:
        A ProfileSample that will be populated with duration on exit,
        or None if profiling fails during setup.

    Example:
        with profile_span("fetch_positions") as sample:
            positions = await broker.list_positions()
        # sample.duration_ms now contains the timing
    """
    start_time = time.perf_counter()
    sample: ProfileSample | None = None

    try:
        # Create a placeholder sample that will be updated on exit
        sample = ProfileSample(
            name=name,
            duration_ms=0.0,  # Will be updated on exit
            labels=labels,
        )
        yield sample
    finally:
        # Always record duration, even if an exception occurred
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        if sample is not None:
            # Update the yielded sample with actual duration
            sample.duration_ms = duration_ms
            sample.timestamp = datetime.now(UTC)

        # Record to histogram
        record_profile(name, duration_ms, labels)


__all__ = [
    "ProfileSample",
    "record_profile",
    "profile_span",
]
