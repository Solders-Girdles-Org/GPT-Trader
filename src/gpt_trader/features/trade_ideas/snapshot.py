"""Point-in-time market snapshots: the only input a proposer may see.

A snapshot is frozen "as of" a moment. Construction rejects any candle that
starts at or after ``as_of``, which makes look-ahead bias structurally
impossible rather than a discipline: a proposer fed snapshots from last year
cannot peek at what happened next, so the same proposer is replayable over
history for calibration scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gpt_trader.core import Candle
from gpt_trader.errors import ValidationError


class SnapshotIntegrityError(ValidationError):
    """Raised when snapshot data violates point-in-time guarantees."""


@dataclass(frozen=True, slots=True)
class SymbolSeries:
    """Ordered candle history for one symbol at one granularity."""

    symbol: str
    granularity: str
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        for earlier, later in zip(self.candles, self.candles[1:], strict=False):
            if later.ts <= earlier.ts:
                raise SnapshotIntegrityError(
                    f"Candles for '{self.symbol}' must be strictly ascending by timestamp; "
                    f"{later.ts.isoformat()} follows {earlier.ts.isoformat()}",
                    field="candles",
                )

    @property
    def closes(self) -> tuple[Candle, ...]:
        return self.candles

    def last_close(self) -> Candle:
        if not self.candles:
            raise SnapshotIntegrityError(
                f"Series for '{self.symbol}' has no candles", field="candles"
            )
        return self.candles[-1]


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Frozen view of market data as of a single moment."""

    as_of: datetime
    source: str
    series: tuple[SymbolSeries, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for symbol_series in self.series:
            if symbol_series.symbol in seen:
                raise SnapshotIntegrityError(
                    f"Duplicate series for symbol '{symbol_series.symbol}'", field="series"
                )
            seen.add(symbol_series.symbol)
            for candle in symbol_series.candles:
                if candle.ts >= self.as_of:
                    raise SnapshotIntegrityError(
                        f"Candle for '{symbol_series.symbol}' starting {candle.ts.isoformat()} "
                        f"is not strictly before snapshot as_of {self.as_of.isoformat()}; "
                        "future or incomplete bars are look-ahead data",
                        field="as_of",
                    )

    def symbols(self) -> tuple[str, ...]:
        return tuple(symbol_series.symbol for symbol_series in self.series)

    def series_for(self, symbol: str) -> SymbolSeries | None:
        for symbol_series in self.series:
            if symbol_series.symbol == symbol:
                return symbol_series
        return None


def market_snapshot_to_payload(snapshot: MarketSnapshot) -> dict[str, Any]:
    """Serialize a ``MarketSnapshot`` into the fixture shape accepted by the CLI."""
    return {
        "as_of": snapshot.as_of.isoformat(),
        "source": snapshot.source,
        "series": [
            {
                "symbol": symbol_series.symbol,
                "granularity": symbol_series.granularity,
                "candles": [
                    {
                        "ts": candle.ts.isoformat(),
                        "open": str(candle.open),
                        "high": str(candle.high),
                        "low": str(candle.low),
                        "close": str(candle.close),
                        "volume": str(candle.volume),
                    }
                    for candle in symbol_series.candles
                ],
            }
            for symbol_series in snapshot.series
        ],
    }
