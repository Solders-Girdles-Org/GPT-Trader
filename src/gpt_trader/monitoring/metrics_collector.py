"""
Lightweight metrics tracking for runtime guard telemetry.

Supports counters, gauges, and histograms with optional labels.
Naming convention: gpt_trader_{metric}_{unit} with snake_case labels.

The previous implementation modeled a full in-memory metrics time series store.
The trading runtime only consumed counter increments to confirm guard
executions, so we keep a tiny façade that preserves the public API used in
tests (`get_metrics_collector`, `record_counter`, `reset_all`,
`get_metrics_summary`) without the historical baggage.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Default histogram buckets for latency measurements (seconds)
DEFAULT_LATENCY_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# Trade execution counters
TRADE_EXECUTED_COUNTER = "gpt_trader_trades_executed_total"
TRADE_BLOCKED_COUNTER = "gpt_trader_trades_blocked_total"


def format_metric_key(name: str, labels: dict[str, str] | None) -> str:
    """Format a metric name with labels into a stable key.

    Labels are sorted alphabetically and stringified for consistent ordering.

    Args:
        name: Metric name (e.g., "gpt_trader_order_submission_total")
        labels: Optional label dict (e.g., {"result": "success", "side": "buy"})

    Returns:
        Formatted key like "gpt_trader_order_submission_total{result=success,side=buy}"
        or just the name if no labels.
    """
    if not labels:
        return name
    # Sort keys for stable ordering, stringify values
    sorted_pairs = sorted((k, str(v)) for k, v in labels.items())
    label_str = ",".join(f"{k}={v}" for k, v in sorted_pairs)
    return f"{name}{{{label_str}}}"


@dataclass
class HistogramData:
    """Histogram data with bucket counts and statistics."""

    buckets: tuple[float, ...]
    bucket_counts: list[int]
    count: int = 0
    total: float = 0.0

    def record(self, value: float) -> None:
        """Record a value in the histogram."""
        self.count += 1
        self.total += value
        # Increment bucket counts for all buckets >= value
        for i, bound in enumerate(self.buckets):
            if value <= bound:
                self.bucket_counts[i] += 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for summary."""
        return {
            "count": self.count,
            "sum": self.total,
            "mean": self.total / self.count if self.count > 0 else 0.0,
            "buckets": {str(b): c for b, c in zip(self.buckets, self.bucket_counts)},
        }


@dataclass
class _GuardMetricsCollector:
    """Metrics collector supporting counters, gauges, and histograms with labels."""

    counters: Counter[str] = field(default_factory=Counter)
    gauges: dict[str, float] = field(default_factory=dict)
    histograms: dict[str, HistogramData] = field(default_factory=dict)

    # Configurable buckets per histogram name
    _histogram_buckets: dict[str, tuple[float, ...]] = field(default_factory=dict)

    def record_counter(
        self, name: str, increment: int = 1, labels: dict[str, str] | None = None
    ) -> None:
        """Increment a counter, optionally with labels.

        Args:
            name: Counter name (e.g., "gpt_trader_order_submission_total")
            increment: Value to increment by (default 1)
            labels: Optional labels (e.g., {"result": "success", "side": "buy"})
        """
        key = format_metric_key(name, labels)
        self.counters[key] += increment

    def record_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge value, optionally with labels.

        Args:
            name: Gauge name (e.g., "gpt_trader_equity_dollars")
            value: Current value
            labels: Optional labels
        """
        key = format_metric_key(name, labels)
        self.gauges[key] = value

    def record_histogram(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        """Record a value in a histogram, optionally with labels.

        Args:
            name: Histogram name (e.g., "gpt_trader_cycle_duration_seconds")
            value: Value to record
            labels: Optional labels (e.g., {"result": "ok"})
            buckets: Optional custom buckets (only used on first call for this name)
        """
        key = format_metric_key(name, labels)

        if key not in self.histograms:
            # Determine buckets: custom > configured > default
            effective_buckets = buckets or self._histogram_buckets.get(
                name, DEFAULT_LATENCY_BUCKETS
            )
            self.histograms[key] = HistogramData(
                buckets=effective_buckets,
                bucket_counts=[0] * len(effective_buckets),
            )

        self.histograms[key].record(value)

    def configure_histogram_buckets(self, name: str, buckets: tuple[float, ...]) -> None:
        """Configure default buckets for a histogram name.

        Args:
            name: Histogram name (without labels)
            buckets: Tuple of bucket boundaries
        """
        self._histogram_buckets[name] = buckets

    def reset_all(self) -> None:
        """Reset all tracked metrics."""
        self.counters.clear()
        self.gauges.clear()
        self.histograms.clear()
        self._histogram_buckets.clear()

    def get_metrics_summary(self) -> dict[str, object]:
        """Get summary of all metrics.

        Returns:
            Dictionary with timestamp, counters, gauges, and histograms.
            Counters without labels maintain backward-compatible dict format.
        """
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "histograms": {k: v.to_dict() for k, v in self.histograms.items()},
        }


_GLOBAL_COLLECTOR = _GuardMetricsCollector()


def get_metrics_collector() -> _GuardMetricsCollector:
    """Return the singleton guard metrics collector."""
    return _GLOBAL_COLLECTOR


def record_counter(name: str, increment: int = 1, labels: dict[str, str] | None = None) -> None:
    """Increment the named counter, optionally with labels."""
    _GLOBAL_COLLECTOR.record_counter(name, increment, labels)


def record_trade_executed() -> None:
    """Increment the trade executed counter."""
    record_counter(TRADE_EXECUTED_COUNTER)


def record_trade_blocked() -> None:
    """Increment the trade blocked counter."""
    record_counter(TRADE_BLOCKED_COUNTER)


def record_gauge(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """Set a gauge value, optionally with labels."""
    _GLOBAL_COLLECTOR.record_gauge(name, value, labels)


def record_histogram(
    name: str,
    value: float,
    labels: dict[str, str] | None = None,
    buckets: tuple[float, ...] | None = None,
) -> None:
    """Record a value in a histogram, optionally with labels."""
    _GLOBAL_COLLECTOR.record_histogram(name, value, labels, buckets)


def reset_all() -> None:
    """Reset all tracked metrics."""
    _GLOBAL_COLLECTOR.reset_all()
