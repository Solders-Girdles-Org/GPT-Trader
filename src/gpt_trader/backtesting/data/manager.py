"""Historical data manager with caching."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gpt_trader.backtesting.engine.bar_runner import IHistoricalDataProvider
from gpt_trader.core import Candle
from gpt_trader.features.brokerages.coinbase.historical_candles import (
    CoinbaseHistoricalFetcher,
)
from gpt_trader.features.data.quality import CandleQualityReport, DataQualityChecker
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.features.brokerages.coinbase.client.client import CoinbaseClient

logger = get_logger(__name__, component="historical_data")

# Granularity to timedelta mapping for quality checking
GRANULARITY_TO_TIMEDELTA = {
    "ONE_MINUTE": timedelta(minutes=1),
    "FIVE_MINUTE": timedelta(minutes=5),
    "FIFTEEN_MINUTE": timedelta(minutes=15),
    "THIRTY_MINUTE": timedelta(minutes=30),
    "ONE_HOUR": timedelta(hours=1),
    "TWO_HOUR": timedelta(hours=2),
    "SIX_HOUR": timedelta(hours=6),
    "ONE_DAY": timedelta(days=1),
}


class HistoricalDataManager(IHistoricalDataProvider):
    """
    Manage historical candle data with file-based caching and quality validation.

    This manager:
    - Checks cache for requested data
    - Fetches missing data from API
    - Validates data quality (gaps, spikes, anomalies)
    - Stores fetched data in cache
    - Provides efficient access to historical candles
    """

    def __init__(
        self,
        fetcher: CoinbaseHistoricalFetcher,
        cache_dir: Path,
        validate_quality: bool = True,
        reject_on_quality_failure: bool = False,
        spike_threshold_pct: float = 15.0,
        volume_anomaly_std: float = 3.0,
    ):
        """
        Initialize data manager.

        Args:
            fetcher: Historical data fetcher
            cache_dir: Directory for cache storage
            validate_quality: Whether to run quality checks on fetched data
            reject_on_quality_failure: If True, reject data that fails quality checks
            spike_threshold_pct: Price change threshold for spike detection
            volume_anomaly_std: Standard deviations for volume anomaly detection
        """
        self.fetcher = fetcher
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Quality validation settings
        self.validate_quality = validate_quality
        self.reject_on_quality_failure = reject_on_quality_failure
        self._quality_checker = DataQualityChecker(
            spike_threshold_pct=spike_threshold_pct,
            volume_anomaly_std=volume_anomaly_std,
        )

        # Cache metadata {symbol: {granularity: [(start, end)]}}
        self._coverage_index: dict[str, dict[str, list[tuple[datetime, datetime]]]] = {}
        self._load_coverage_index()

        # Track recent quality reports
        self._recent_quality_reports: dict[str, CandleQualityReport] = {}

    async def get_candles(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """
        Get candles from cache or fetch from API.

        Args:
            symbol: Trading pair
            granularity: Candle granularity
            start: Start time (inclusive)
            end: End time (exclusive)

        Returns:
            List of candles sorted by timestamp
        """
        # Check cache
        cached_candles = self._read_from_cache(symbol, granularity, start, end)

        # Identify gaps
        gaps = self._identify_gaps(symbol, granularity, start, end)

        if not gaps:
            # All data in cache
            return cached_candles

        # Fetch missing data
        fetched_candles = []
        for gap_start, gap_end in gaps:
            gap_candles = await self.fetcher.fetch_candles(
                symbol=symbol,
                granularity=granularity,
                start=gap_start,
                end=gap_end,
            )
            fetched_candles.extend(gap_candles)

            # Update cache
            self._write_to_cache(symbol, granularity, gap_candles)
            self._update_coverage(symbol, granularity, gap_start, gap_end)

        # Combine and sort
        all_candles = cached_candles + fetched_candles
        all_candles = sorted(all_candles, key=lambda c: c.ts)

        # Filter to requested range
        all_candles = [c for c in all_candles if start <= c.ts < end]

        # Run quality validation if enabled
        if self.validate_quality and all_candles:
            report = self._validate_candles(symbol, granularity, all_candles)
            self._recent_quality_reports[f"{symbol}:{granularity}"] = report

            if not report.is_acceptable and self.reject_on_quality_failure:
                logger.error(
                    "Data quality check failed - rejecting data",
                    symbol=symbol,
                    granularity=granularity,
                    score=report.overall_score,
                    gaps=len(report.gaps_detected),
                    spikes=len(report.spikes_detected),
                    volume_anomalies=len(report.volume_anomalies),
                    operation="get_candles",
                )
                return []

        return all_candles

    def _validate_candles(
        self,
        symbol: str,
        granularity: str,
        candles: list[Candle],
    ) -> CandleQualityReport:
        """
        Validate candle data quality and log any issues.

        Args:
            symbol: Trading pair symbol
            granularity: Candle granularity
            candles: List of candles to validate

        Returns:
            CandleQualityReport with detected issues
        """
        expected_interval = GRANULARITY_TO_TIMEDELTA.get(granularity, timedelta(hours=1))

        report = self._quality_checker.check_candles(candles, expected_interval)

        # Log issues if any detected
        if report.has_issues:
            for issue in report.gaps_detected:
                log_func = logger.error if issue.severity == "error" else logger.warning
                log_func(
                    issue.description,
                    issue_type=issue.issue_type,
                    symbol=symbol,
                    granularity=granularity,
                    timestamp=issue.timestamp.isoformat(),
                    operation="validate_candles",
                    **issue.details,
                )

            for issue in report.spikes_detected:
                log_func = logger.error if issue.severity == "error" else logger.warning
                log_func(
                    issue.description,
                    issue_type=issue.issue_type,
                    symbol=symbol,
                    granularity=granularity,
                    timestamp=issue.timestamp.isoformat(),
                    operation="validate_candles",
                    **issue.details,
                )

            for issue in report.volume_anomalies:
                log_func = logger.error if issue.severity == "error" else logger.warning
                log_func(
                    issue.description,
                    issue_type=issue.issue_type,
                    symbol=symbol,
                    granularity=granularity,
                    timestamp=issue.timestamp.isoformat(),
                    operation="validate_candles",
                    **issue.details,
                )

        return report

    def get_quality_report(self, symbol: str, granularity: str) -> CandleQualityReport | None:
        """
        Get the most recent quality report for a symbol/granularity.

        Args:
            symbol: Trading pair symbol
            granularity: Candle granularity

        Returns:
            Most recent CandleQualityReport or None if not available
        """
        return self._recent_quality_reports.get(f"{symbol}:{granularity}")

    def _get_cache_path(self, symbol: str, granularity: str) -> Path:
        """Get cache file path for symbol and granularity."""
        safe_symbol = symbol.replace("/", "_").replace("-", "_")
        return self.cache_dir / f"{safe_symbol}_{granularity}.json"

    def _read_from_cache(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Read candles from cache file."""
        cache_path = self._get_cache_path(symbol, granularity)

        if not cache_path.exists():
            return []

        try:
            with open(cache_path) as f:
                data = json.load(f)

            candles = []
            for item in data.get("candles", []):
                ts = datetime.fromisoformat(item["ts"])
                if start <= ts < end:
                    candles.append(
                        Candle(
                            ts=ts,
                            open=Decimal(str(item["open"])),
                            high=Decimal(str(item["high"])),
                            low=Decimal(str(item["low"])),
                            close=Decimal(str(item["close"])),
                            volume=Decimal(str(item["volume"])),
                        )
                    )

            return candles
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error(
                "Failed to read candles from cache",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="read_from_cache",
                symbol=symbol,
                granularity=granularity,
                cache_path=str(cache_path),
            )
            return []

    def _write_to_cache(
        self,
        symbol: str,
        granularity: str,
        candles: list[Candle],
    ) -> None:
        """Write candles to cache file (append mode)."""
        cache_path = self._get_cache_path(symbol, granularity)

        # Read existing
        existing = []
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    data = json.load(f)
                    existing = data.get("candles", [])
            except json.JSONDecodeError as exc:
                logger.error(
                    "Failed to decode existing cache file",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="write_to_cache",
                    symbol=symbol,
                    granularity=granularity,
                    cache_path=str(cache_path),
                )
                existing = []

        # Convert new candles to dicts
        new_items = [
            {
                "ts": c.ts.isoformat(),
                "open": str(c.open),
                "high": str(c.high),
                "low": str(c.low),
                "close": str(c.close),
                "volume": str(c.volume),
            }
            for c in candles
        ]

        # Merge and deduplicate
        existing_timestamps = {item["ts"] for item in existing}
        for item in new_items:
            if item["ts"] not in existing_timestamps:
                existing.append(item)

        # Sort by timestamp
        existing.sort(key=lambda x: x["ts"])

        # Write back
        with open(cache_path, "w") as f:
            json.dump({"candles": existing}, f, indent=2)

    def _identify_gaps(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """
        Identify time gaps not covered by cache.

        Args:
            symbol: Trading pair
            granularity: Candle granularity
            start: Requested start time
            end: Requested end time

        Returns:
            List of (gap_start, gap_end) tuples
        """
        if symbol not in self._coverage_index:
            # No coverage at all
            return [(start, end)]

        if granularity not in self._coverage_index[symbol]:
            # No coverage for this granularity
            return [(start, end)]

        covered_ranges = self._coverage_index[symbol][granularity]

        # Identify uncovered segments by merging coverage and subtracting from the request window.
        clamped: list[tuple[datetime, datetime]] = []
        for cov_start, cov_end in covered_ranges:
            if cov_start >= cov_end:
                continue
            if cov_end <= start or cov_start >= end:
                continue
            clamped.append((max(cov_start, start), min(cov_end, end)))

        if not clamped:
            return [(start, end)]

        clamped.sort(key=lambda r: r[0])
        merged: list[tuple[datetime, datetime]] = []
        for cov_start, cov_end in clamped:
            if not merged:
                merged.append((cov_start, cov_end))
                continue

            last_start, last_end = merged[-1]
            if cov_start <= last_end:
                merged[-1] = (last_start, max(last_end, cov_end))
            else:
                merged.append((cov_start, cov_end))

        gaps: list[tuple[datetime, datetime]] = []
        cursor = start
        for cov_start, cov_end in merged:
            if cov_start > cursor:
                gaps.append((cursor, cov_start))
            cursor = max(cursor, cov_end)

        if cursor < end:
            gaps.append((cursor, end))

        return gaps

    def _update_coverage(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> None:
        """Update coverage index with newly cached data."""
        if symbol not in self._coverage_index:
            self._coverage_index[symbol] = {}

        if granularity not in self._coverage_index[symbol]:
            self._coverage_index[symbol][granularity] = []

        self._coverage_index[symbol][granularity].append((start, end))
        self._save_coverage_index()

    def _load_coverage_index(self) -> None:
        """Load coverage index from disk."""
        index_path = self.cache_dir / "_coverage_index.json"

        if not index_path.exists():
            return

        try:
            with open(index_path) as f:
                data = json.load(f)

            # Deserialize datetimes
            for symbol, granularities in data.items():
                self._coverage_index[symbol] = {}
                for granularity, ranges in granularities.items():
                    self._coverage_index[symbol][granularity] = [
                        (datetime.fromisoformat(r[0]), datetime.fromisoformat(r[1])) for r in ranges
                    ]
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error(
                "Failed to load coverage index",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="load_coverage_index",
                index_path=str(index_path),
            )

    def _save_coverage_index(self) -> None:
        """Save coverage index to disk."""
        index_path = self.cache_dir / "_coverage_index.json"

        # Serialize datetimes
        data: dict[str, Any] = {}
        for symbol, granularities in self._coverage_index.items():
            data[symbol] = {}
            for granularity, ranges in granularities.items():
                data[symbol][granularity] = [(r[0].isoformat(), r[1].isoformat()) for r in ranges]

        with open(index_path, "w") as f:
            json.dump(data, f, indent=2)


def create_coinbase_data_provider(
    client: "CoinbaseClient",
    cache_dir: Path | str | None = None,
    rate_limit_rps: int = 10,
    validate_quality: bool = True,
    reject_on_quality_failure: bool = False,
    spike_threshold_pct: float = 15.0,
    volume_anomaly_std: float = 3.0,
) -> HistoricalDataManager:
    """
    Factory function to create a HistoricalDataManager with Coinbase fetcher.

    This is the primary way to get an IHistoricalDataProvider for backtesting.

    Args:
        client: Coinbase API client
        cache_dir: Directory for cache storage. Defaults to ~/.gpt_trader/cache/candles
        rate_limit_rps: Rate limit for Coinbase API (requests per second)
        validate_quality: Whether to run quality checks on fetched data
        reject_on_quality_failure: If True, reject data that fails quality checks
        spike_threshold_pct: Price change threshold for spike detection (default 15%)
        volume_anomaly_std: Standard deviations for volume anomaly detection

    Returns:
        Configured HistoricalDataManager instance

    Example:
        ```python
        from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient
        from gpt_trader.backtesting.data.manager import create_coinbase_data_provider
        from gpt_trader.backtesting.engine.bar_runner import ClockedBarRunner

        client = CoinbaseClient(api_key=..., api_secret=...)
        data_provider = create_coinbase_data_provider(client)

        runner = ClockedBarRunner(
            data_provider=data_provider,
            symbols=["BTC-USD"],
            granularity="FIVE_MINUTE",
            start_date=...,
            end_date=...,
        )
        ```
    """
    if cache_dir is None:
        cache_dir = Path.home() / ".gpt_trader" / "cache" / "candles"
    else:
        cache_dir = Path(cache_dir)

    fetcher = CoinbaseHistoricalFetcher(
        client=client,
        rate_limit_rps=rate_limit_rps,
    )

    return HistoricalDataManager(
        fetcher=fetcher,
        cache_dir=cache_dir,
        validate_quality=validate_quality,
        reject_on_quality_failure=reject_on_quality_failure,
        spike_threshold_pct=spike_threshold_pct,
        volume_anomaly_std=volume_anomaly_std,
    )
