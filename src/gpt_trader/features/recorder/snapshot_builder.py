"""Build proposer-safe market snapshots from historical candle sources.

Recorder-owned snapshot production: the builder turns read-only candle
history into the point-in-time ``MarketSnapshot`` artifact defined by the
trade-idea contract (``features/trade_ideas/snapshot.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from gpt_trader.core import Candle
from gpt_trader.features.trade_ideas.snapshot import (
    MarketSnapshot,
    SnapshotIntegrityError,
    SymbolSeries,
)

_CANONICAL_GRANULARITY_BY_ALIAS = {
    "1M": "ONE_MINUTE",
    "1MIN": "ONE_MINUTE",
    "1MINUTE": "ONE_MINUTE",
    "ONE_MINUTE": "ONE_MINUTE",
    "5M": "FIVE_MINUTE",
    "5MIN": "FIVE_MINUTE",
    "5MINUTE": "FIVE_MINUTE",
    "FIVE_MINUTE": "FIVE_MINUTE",
    "15M": "FIFTEEN_MINUTE",
    "15MIN": "FIFTEEN_MINUTE",
    "15MINUTE": "FIFTEEN_MINUTE",
    "FIFTEEN_MINUTE": "FIFTEEN_MINUTE",
    "30M": "THIRTY_MINUTE",
    "30MIN": "THIRTY_MINUTE",
    "30MINUTE": "THIRTY_MINUTE",
    "THIRTY_MINUTE": "THIRTY_MINUTE",
    "1H": "ONE_HOUR",
    "1HR": "ONE_HOUR",
    "1HOUR": "ONE_HOUR",
    "ONE_HOUR": "ONE_HOUR",
    "2H": "TWO_HOUR",
    "2HR": "TWO_HOUR",
    "2HOUR": "TWO_HOUR",
    "TWO_HOUR": "TWO_HOUR",
    "4H": "FOUR_HOUR",
    "4HR": "FOUR_HOUR",
    "4HOUR": "FOUR_HOUR",
    "FOUR_HOUR": "FOUR_HOUR",
    "6H": "SIX_HOUR",
    "6HR": "SIX_HOUR",
    "6HOUR": "SIX_HOUR",
    "SIX_HOUR": "SIX_HOUR",
    "1D": "ONE_DAY",
    "1DAY": "ONE_DAY",
    "ONE_DAY": "ONE_DAY",
}

_GRANULARITY_DURATION_BY_NAME = {
    "ONE_MINUTE": timedelta(minutes=1),
    "FIVE_MINUTE": timedelta(minutes=5),
    "FIFTEEN_MINUTE": timedelta(minutes=15),
    "THIRTY_MINUTE": timedelta(minutes=30),
    "ONE_HOUR": timedelta(hours=1),
    "TWO_HOUR": timedelta(hours=2),
    "FOUR_HOUR": timedelta(hours=4),
    "SIX_HOUR": timedelta(hours=6),
    "ONE_DAY": timedelta(days=1),
}


class HistoricalCandleSource(Protocol):
    """Read-only source for historical candles."""

    async def fetch_candles(
        self,
        *,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Return candles for ``symbol`` in ``[start, end)``."""


@dataclass(frozen=True, slots=True)
class MarketSnapshotBuildRequest:
    """Configuration for one point-in-time snapshot build."""

    symbols: tuple[str, ...]
    granularity: str
    lookback: int
    as_of: datetime

    def __post_init__(self) -> None:
        if not self.symbols:
            raise SnapshotIntegrityError(
                "Market snapshot build requires at least one symbol",
                field="symbols",
            )
        if any(not symbol.strip() for symbol in self.symbols):
            raise SnapshotIntegrityError(
                "Market snapshot symbols must be non-empty",
                field="symbols",
            )
        if len(set(self.symbols)) != len(self.symbols):
            raise SnapshotIntegrityError(
                "Market snapshot symbols must be unique",
                field="symbols",
            )
        if self.lookback < 1:
            raise SnapshotIntegrityError(
                "Market snapshot lookback must be at least 1",
                field="lookback",
            )
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise SnapshotIntegrityError(
                "Market snapshot as_of must include a timezone",
                field="as_of",
            )
        if granularity_duration(self.granularity) is None:
            raise SnapshotIntegrityError(
                f"Unsupported snapshot granularity: {self.granularity}",
                field="granularity",
            )


class MarketSnapshotBuilder:
    """Build ``MarketSnapshot`` objects without account or execution access."""

    def __init__(
        self,
        candle_source: HistoricalCandleSource,
        *,
        source_label: str = "coinbase:market-candles",
    ) -> None:
        if not source_label.strip():
            raise SnapshotIntegrityError(
                "Market snapshot source label must be non-empty",
                field="source",
            )
        self._candle_source = candle_source
        self._source_label = source_label

    async def build(self, request: MarketSnapshotBuildRequest) -> MarketSnapshot:
        """Fetch completed historical candles and build a point-in-time snapshot."""
        granularity = canonical_granularity(request.granularity)
        if granularity is None:
            raise SnapshotIntegrityError(
                f"Unsupported snapshot granularity: {request.granularity}",
                field="granularity",
            )
        duration = _GRANULARITY_DURATION_BY_NAME[granularity]
        as_of = _normalize_utc(request.as_of)
        completed_cutoff = _completed_candle_cutoff(as_of, duration)
        start = completed_cutoff - duration * request.lookback

        series: list[SymbolSeries] = []
        for symbol in request.symbols:
            candles = await self._candle_source.fetch_candles(
                symbol=symbol,
                granularity=granularity,
                start=start,
                end=as_of,
            )
            completed = _completed_window(
                symbol=symbol,
                candles=candles,
                start=start,
                as_of=as_of,
                duration=duration,
                lookback=request.lookback,
            )
            series.append(
                SymbolSeries(
                    symbol=symbol,
                    granularity=granularity,
                    candles=completed,
                )
            )

        return MarketSnapshot(
            as_of=as_of,
            source=_source_metadata(
                self._source_label,
                granularity=granularity,
                lookback=request.lookback,
                as_of=as_of,
            ),
            series=tuple(series),
        )


def granularity_duration(granularity: str) -> timedelta | None:
    """Return the duration for supported Coinbase candle granularities."""
    canonical = canonical_granularity(granularity)
    if canonical is None:
        return None
    return _GRANULARITY_DURATION_BY_NAME[canonical]


def canonical_granularity(granularity: str) -> str | None:
    """Return the Coinbase enum name for a supported candle granularity alias."""
    normalized = granularity.strip().upper().replace("-", "_")
    return _CANONICAL_GRANULARITY_BY_ALIAS.get(normalized)


def _completed_window(
    *,
    symbol: str,
    candles: Sequence[Candle],
    start: datetime,
    as_of: datetime,
    duration: timedelta,
    lookback: int,
) -> tuple[Candle, ...]:
    normalized = tuple(_normalize_candle(candle) for candle in candles)
    _require_ascending(symbol, normalized)
    completed = tuple(
        candle for candle in normalized if start <= candle.ts and candle.ts + duration <= as_of
    )
    if not completed:
        raise SnapshotIntegrityError(
            f"No completed candles for '{symbol}' before snapshot as_of {as_of.isoformat()}",
            field="candles",
        )
    return completed[-lookback:]


def _normalize_candle(candle: Candle) -> Candle:
    return Candle(
        ts=_normalize_utc(candle.ts),
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _completed_candle_cutoff(as_of: datetime, duration: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    intervals = (as_of - epoch) // duration
    return epoch + intervals * duration


def _require_ascending(symbol: str, candles: Sequence[Candle]) -> None:
    for earlier, later in zip(candles, candles[1:], strict=False):
        if later.ts <= earlier.ts:
            raise SnapshotIntegrityError(
                f"Candles for '{symbol}' must be strictly ascending by timestamp; "
                f"{later.ts.isoformat()} follows {earlier.ts.isoformat()}",
                field="candles",
            )


def _source_metadata(
    source_label: str,
    *,
    granularity: str,
    lookback: int,
    as_of: datetime,
) -> str:
    return (
        f"{source_label}:granularity={granularity}:lookback={lookback}"
        f":as_of={as_of.isoformat()}"
    )
