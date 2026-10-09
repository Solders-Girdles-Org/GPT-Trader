from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from gpt_trader.core import Candle


@dataclass
class QualityIssue:
    """Represents a data quality issue detected in the time series."""

    issue_type: str  # "gap", "spike", "volume_anomaly", "stale"
    timestamp: datetime
    severity: str  # "warning", "error"
    description: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandleQualityReport:
    """Quality report for candle data."""

    total_candles: int
    gaps_detected: list[QualityIssue]
    spikes_detected: list[QualityIssue]
    volume_anomalies: list[QualityIssue]
    overall_score: float
    is_acceptable: bool

    @property
    def all_issues(self) -> list[QualityIssue]:
        """Return all issues combined."""
        return self.gaps_detected + self.spikes_detected + self.volume_anomalies

    @property
    def has_issues(self) -> bool:
        """Check if any issues were detected."""
        return len(self.all_issues) > 0


class DataQualityChecker:
    """
    Validates data quality for historical candle data.

    Features:
    - Gap detection for missing candles in time series
    - Spike detection for anomalous price moves
    - Volume anomaly detection
    - Quality scoring
    """

    def __init__(
        self,
        spike_threshold_pct: float = 15.0,
        volume_anomaly_std: float = 3.0,
        min_acceptable_score: float = 0.8,
    ) -> None:
        """
        Initialize quality checker.

        Args:
            spike_threshold_pct: Price change threshold for spike detection (default 15%)
            volume_anomaly_std: Standard deviations for volume anomaly (default 3)
            min_acceptable_score: Minimum score to consider data acceptable
        """
        self.spike_threshold_pct = spike_threshold_pct
        self.volume_anomaly_std = volume_anomaly_std
        self.min_acceptable_score = min_acceptable_score

        self.quality_history: list[DataQualityChecker.QualityResult] = []
        self.scores: list[float] = []

    class QualityResult:
        def __init__(self, acceptable: bool = True, score: float = 1.0):
            self._acceptable = acceptable
            self._score = score

        def overall_score(self) -> float:
            return self._score

        def is_acceptable(self, threshold: float = 0.8) -> bool:
            return self._acceptable and self._score >= threshold

        @property
        def completeness(self) -> float:
            return self._score

    def check_quality(self, data: pd.DataFrame) -> QualityResult:
        if data is None or data.empty:
            result = self.QualityResult(acceptable=False, score=0.0)
            self.scores.append(result._score)
            return result

        if data.isnull().any().any():
            result = self.QualityResult(acceptable=False, score=0.5)
        else:
            result = self.QualityResult(acceptable=True, score=1.0)

        self.quality_history.append(result)
        self.scores.append(result._score)
        return result

    def validate_ohlcv(self, data: pd.DataFrame) -> list[str]:
        issues = []
        if data is None or data.empty:
            return ["Empty dataframe"]

        required_columns = ["open", "high", "low", "close", "volume"]
        missing = [col for col in required_columns if col not in data.columns]
        if missing:
            issues.append(f"Missing columns: {missing}")

        # Check for negative prices specific columns
        if "open" in data.columns and (data["open"] < 0).any():
            issues.append("Negative open found")
        if "high" in data.columns and (data["high"] < 0).any():
            issues.append("Negative high found")
        if "low" in data.columns and (data["low"] < 0).any():
            issues.append("Negative low found")
        if "close" in data.columns and (data["close"] < 0).any():
            issues.append("Negative close found")
        if "volume" in data.columns and (data["volume"] < 0).any():
            issues.append("Negative volume found")

        # Check high >= low
        if "high" in data.columns and "low" in data.columns and (data["high"] < data["low"]).any():
            issues.append("High < Low")

        return issues

    def get_quality_trend(self) -> dict[str, float]:
        avg_score = 0.0
        if self.scores:
            avg_score = sum(self.scores) / len(self.scores)

        return {"timeliness": 1.0, "completeness": avg_score, "overall": avg_score}

    def check_candles(
        self,
        candles: list[Candle],
        expected_interval: timedelta,
    ) -> CandleQualityReport:
        """
        Perform comprehensive quality checks on candle data.

        Args:
            candles: List of Candle objects to check
            expected_interval: Expected time between candles

        Returns:
            CandleQualityReport with detected issues and overall score
        """
        if not candles:
            return CandleQualityReport(
                total_candles=0,
                gaps_detected=[],
                spikes_detected=[],
                volume_anomalies=[],
                overall_score=0.0,
                is_acceptable=False,
            )

        # Sort by timestamp
        sorted_candles = sorted(candles, key=lambda c: c.ts)

        # Run detection methods
        gaps = self._detect_gaps(sorted_candles, expected_interval)
        spikes = self._detect_spikes(sorted_candles)
        volume_issues = self._detect_volume_anomalies(sorted_candles)

        # Calculate quality score
        total_issues = len(gaps) + len(spikes) + len(volume_issues)
        error_count = sum(1 for issue in gaps + spikes + volume_issues if issue.severity == "error")

        # Score calculation: start at 1.0, deduct for issues
        # Errors deduct more than warnings
        score = 1.0
        score -= error_count * 0.1  # 10% per error
        score -= (total_issues - error_count) * 0.02  # 2% per warning
        score = max(0.0, min(1.0, score))

        return CandleQualityReport(
            total_candles=len(candles),
            gaps_detected=gaps,
            spikes_detected=spikes,
            volume_anomalies=volume_issues,
            overall_score=score,
            is_acceptable=score >= self.min_acceptable_score,
        )

    def _detect_gaps(
        self,
        candles: list[Candle],
        expected_interval: timedelta,
    ) -> list[QualityIssue]:
        """
        Detect missing candles (gaps) in the time series.

        Args:
            candles: Sorted list of candles
            expected_interval: Expected time between consecutive candles

        Returns:
            List of QualityIssue for each detected gap
        """
        gaps: list[QualityIssue] = []
        tolerance = expected_interval * 0.1  # 10% tolerance

        for i in range(1, len(candles)):
            prev_ts = candles[i - 1].ts
            curr_ts = candles[i].ts
            actual_interval = curr_ts - prev_ts

            # Check if gap exceeds expected interval + tolerance
            if actual_interval > expected_interval + tolerance:
                missing_candles = int(actual_interval / expected_interval) - 1
                severity = "error" if missing_candles > 5 else "warning"

                gaps.append(
                    QualityIssue(
                        issue_type="gap",
                        timestamp=prev_ts,
                        severity=severity,
                        description=f"Gap detected: {missing_candles} candle(s) missing",
                        details={
                            "gap_start": prev_ts.isoformat(),
                            "gap_end": curr_ts.isoformat(),
                            "expected_interval_seconds": expected_interval.total_seconds(),
                            "actual_interval_seconds": actual_interval.total_seconds(),
                            "missing_candles": missing_candles,
                        },
                    )
                )

        return gaps

    def _detect_spikes(self, candles: list[Candle]) -> list[QualityIssue]:
        """
        Detect anomalous price spikes in the time series.

        A spike is when the price change between consecutive candles
        exceeds the configured threshold percentage.

        Args:
            candles: Sorted list of candles

        Returns:
            List of QualityIssue for each detected spike
        """
        spikes: list[QualityIssue] = []
        threshold = Decimal(str(self.spike_threshold_pct / 100))

        for i in range(1, len(candles)):
            prev_close = candles[i - 1].close
            curr_open = candles[i].open

            if prev_close == 0:
                continue

            # Check open gap
            open_change = abs(curr_open - prev_close) / prev_close
            if open_change > threshold:
                change_pct = float(open_change) * 100
                severity = "error" if change_pct > self.spike_threshold_pct * 2 else "warning"

                spikes.append(
                    QualityIssue(
                        issue_type="spike",
                        timestamp=candles[i].ts,
                        severity=severity,
                        description=f"Price gap: {change_pct:.2f}% between close and open",
                        details={
                            "prev_close": str(prev_close),
                            "curr_open": str(curr_open),
                            "change_pct": change_pct,
                        },
                    )
                )

            # Check intra-candle range
            if candles[i].low > 0:
                candle_range = (candles[i].high - candles[i].low) / candles[i].low
                if candle_range > threshold:
                    range_pct = float(candle_range) * 100
                    severity = "error" if range_pct > self.spike_threshold_pct * 2 else "warning"

                    spikes.append(
                        QualityIssue(
                            issue_type="spike",
                            timestamp=candles[i].ts,
                            severity=severity,
                            description=f"Large candle range: {range_pct:.2f}%",
                            details={
                                "high": str(candles[i].high),
                                "low": str(candles[i].low),
                                "range_pct": range_pct,
                            },
                        )
                    )

        return spikes

    def _detect_volume_anomalies(self, candles: list[Candle]) -> list[QualityIssue]:
        """
        Detect anomalous volume in the time series.

        A volume anomaly is when the volume deviates significantly
        from the mean (more than configured standard deviations).

        Args:
            candles: Sorted list of candles

        Returns:
            List of QualityIssue for each detected volume anomaly
        """
        if len(candles) < 10:  # Need enough data for meaningful stats
            return []

        anomalies: list[QualityIssue] = []
        volumes = [float(c.volume) for c in candles]

        # Calculate statistics
        mean_volume = sum(volumes) / len(volumes)
        if mean_volume == 0:
            return []

        variance = sum((v - mean_volume) ** 2 for v in volumes) / len(volumes)
        std_dev = variance**0.5

        if std_dev == 0:
            return []

        threshold = self.volume_anomaly_std * std_dev

        for candle in candles:
            volume = float(candle.volume)
            deviation = abs(volume - mean_volume)

            if deviation > threshold:
                z_score = deviation / std_dev
                is_spike = volume > mean_volume
                severity = "error" if z_score > self.volume_anomaly_std * 2 else "warning"

                anomalies.append(
                    QualityIssue(
                        issue_type="volume_anomaly",
                        timestamp=candle.ts,
                        severity=severity,
                        description=f"Volume {'spike' if is_spike else 'drop'}: {z_score:.1f} std devs",
                        details={
                            "volume": str(candle.volume),
                            "mean_volume": mean_volume,
                            "std_dev": std_dev,
                            "z_score": z_score,
                            "is_spike": is_spike,
                        },
                    )
                )

        return anomalies
