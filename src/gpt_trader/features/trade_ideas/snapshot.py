"""Point-in-time market snapshots: the only input a proposer may see.

A snapshot freezes the supplied observations. Construction rejects candle starts
at or after ``as_of``; the recorder additionally selects completed bars. This
bounds supplied market data, not a model's learned knowledge. Venue increments
carry their acquisition time in provenance and must not be represented as
historical rules known at an earlier replay cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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
    price_increment: Decimal | None = None
    price_increment_source: str | None = None
    price_increment_error: str | None = None

    def proposal_increment(self, offline_fallback: Decimal) -> Decimal:
        """Use captured venue rules; legacy offline fixtures retain their precision."""
        if self.price_increment_error:
            raise SnapshotIntegrityError(
                f"Price increment unavailable for {self.symbol}: {self.price_increment_error}",
                field="price_increment",
            )
        return self.price_increment if self.price_increment is not None else offline_fallback

    def __post_init__(self) -> None:
        if self.price_increment is not None and (
            not self.price_increment.is_finite() or self.price_increment <= 0
        ):
            raise SnapshotIntegrityError(
                "Price increment must be finite and positive", field="price_increment"
            )
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
                **(
                    {"price_increment": str(symbol_series.price_increment)}
                    if symbol_series.price_increment is not None
                    else {}
                ),
                **(
                    {"price_increment_source": symbol_series.price_increment_source}
                    if symbol_series.price_increment_source
                    else {}
                ),
                **(
                    {"price_increment_error": symbol_series.price_increment_error}
                    if symbol_series.price_increment_error
                    else {}
                ),
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
