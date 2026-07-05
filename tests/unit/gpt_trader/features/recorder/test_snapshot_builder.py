from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gpt_trader.core import Candle
from gpt_trader.features.recorder import (
    MarketSnapshotBuilder,
    MarketSnapshotBuildRequest,
    canonical_granularity,
    granularity_duration,
)
from gpt_trader.features.trade_ideas import (
    SnapshotIntegrityError,
    market_snapshot_to_payload,
)

AS_OF = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


class FakeCandleSource:
    def __init__(self, candles_by_symbol: dict[str, Sequence[Candle]]) -> None:
        self.candles_by_symbol = candles_by_symbol
        self.calls: list[dict[str, object]] = []

    async def fetch_candles(
        self,
        *,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        self.calls.append(
            {
                "symbol": symbol,
                "granularity": granularity,
                "start": start,
                "end": end,
            }
        )
        return self.candles_by_symbol.get(symbol, ())


def candle(offset_hours: int, close: str = "100") -> Candle:
    price = Decimal(close)
    return Candle(
        ts=AS_OF + timedelta(hours=offset_hours),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1000"),
    )


def request(
    *,
    symbols: tuple[str, ...] = ("BTC-USD",),
    granularity: str = "ONE_HOUR",
    lookback: int = 3,
    as_of: datetime = AS_OF,
) -> MarketSnapshotBuildRequest:
    return MarketSnapshotBuildRequest(
        symbols=symbols,
        granularity=granularity,
        lookback=lookback,
        as_of=as_of,
    )


@pytest.mark.asyncio
async def test_builder_fetches_window_and_builds_auditable_snapshot_metadata() -> None:
    source = FakeCandleSource(
        {
            "BTC-USD": (candle(-3), candle(-2), candle(-1)),
            "ETH-USD": (candle(-3, "200"), candle(-2, "201"), candle(-1, "202")),
        }
    )

    snapshot = await MarketSnapshotBuilder(source).build(request(symbols=("BTC-USD", "ETH-USD")))

    assert snapshot.as_of == AS_OF
    assert snapshot.symbols() == ("BTC-USD", "ETH-USD")
    assert snapshot.source == (
        "coinbase:market-candles:granularity=ONE_HOUR:lookback=3" f":as_of={AS_OF.isoformat()}"
    )
    assert [call["symbol"] for call in source.calls] == ["BTC-USD", "ETH-USD"]
    assert source.calls[0]["start"] == AS_OF - timedelta(hours=3)
    assert source.calls[0]["end"] == AS_OF


@pytest.mark.asyncio
async def test_builder_normalizes_granularity_alias_before_fetching() -> None:
    source = FakeCandleSource({"BTC-USD": (candle(-2), candle(-1))})

    snapshot = await MarketSnapshotBuilder(source).build(request(granularity="1H", lookback=2))

    assert source.calls[0]["granularity"] == "ONE_HOUR"
    series = snapshot.series_for("BTC-USD")
    assert series is not None
    assert series.granularity == "ONE_HOUR"
    assert snapshot.source == (
        "coinbase:market-candles:granularity=ONE_HOUR:lookback=2" f":as_of={AS_OF.isoformat()}"
    )


@pytest.mark.asyncio
async def test_builder_supports_four_hour_granularity_alias() -> None:
    source = FakeCandleSource({"BTC-USD": (candle(-8), candle(-4))})

    snapshot = await MarketSnapshotBuilder(source).build(request(granularity="4HR", lookback=2))

    assert source.calls[0]["granularity"] == "FOUR_HOUR"
    assert source.calls[0]["start"] == AS_OF - timedelta(hours=8)
    series = snapshot.series_for("BTC-USD")
    assert series is not None
    assert series.granularity == "FOUR_HOUR"
    assert [item.ts for item in series.candles] == [
        AS_OF - timedelta(hours=8),
        AS_OF - timedelta(hours=4),
    ]


@pytest.mark.asyncio
async def test_builder_skips_candles_at_or_after_as_of_without_lookahead() -> None:
    source = FakeCandleSource({"BTC-USD": (candle(-2), candle(-1), candle(0), candle(1))})

    snapshot = await MarketSnapshotBuilder(source).build(request(lookback=4))

    series = snapshot.series_for("BTC-USD")
    assert series is not None
    assert [item.ts for item in series.candles] == [
        AS_OF - timedelta(hours=2),
        AS_OF - timedelta(hours=1),
    ]
    assert all(item.ts < snapshot.as_of for item in series.candles)


@pytest.mark.asyncio
async def test_builder_fetches_full_completed_lookback_for_unaligned_as_of() -> None:
    unaligned_as_of = AS_OF + timedelta(minutes=30)
    source = FakeCandleSource({"BTC-USD": (candle(-3), candle(-2), candle(-1), candle(0))})

    snapshot = await MarketSnapshotBuilder(source).build(request(lookback=3, as_of=unaligned_as_of))

    assert source.calls[0]["start"] == AS_OF - timedelta(hours=3)
    assert source.calls[0]["end"] == unaligned_as_of
    series = snapshot.series_for("BTC-USD")
    assert series is not None
    assert [item.ts for item in series.candles] == [
        AS_OF - timedelta(hours=3),
        AS_OF - timedelta(hours=2),
        AS_OF - timedelta(hours=1),
    ]
    assert all(item.ts + timedelta(hours=1) <= snapshot.as_of for item in series.candles)


@pytest.mark.asyncio
async def test_builder_rejects_non_ascending_source_candles() -> None:
    source = FakeCandleSource({"BTC-USD": (candle(-1), candle(-2))})

    with pytest.raises(SnapshotIntegrityError, match="ascending") as exc_info:
        await MarketSnapshotBuilder(source).build(request())

    assert exc_info.value.context["field"] == "candles"


@pytest.mark.asyncio
async def test_builder_rejects_symbols_with_no_completed_candles() -> None:
    source = FakeCandleSource({"BTC-USD": (candle(0), candle(1))})

    with pytest.raises(SnapshotIntegrityError, match="No completed candles") as exc_info:
        await MarketSnapshotBuilder(source).build(request())

    assert exc_info.value.context["field"] == "candles"


def test_build_request_rejects_unsupported_granularity() -> None:
    with pytest.raises(
        SnapshotIntegrityError, match="Unsupported snapshot granularity"
    ) as exc_info:
        request(granularity="BAD")

    assert exc_info.value.context["field"] == "granularity"
    assert canonical_granularity("1H") == "ONE_HOUR"
    assert canonical_granularity("4H") == "FOUR_HOUR"
    assert canonical_granularity("4HOUR") == "FOUR_HOUR"
    assert canonical_granularity("1D") == "ONE_DAY"
    assert granularity_duration("FOUR_HOUR") == timedelta(hours=4)
    assert granularity_duration("ONE_DAY") == timedelta(days=1)


@pytest.mark.asyncio
async def test_market_snapshot_payload_serializes_existing_fixture_shape() -> None:
    source = FakeCandleSource({"BTC-USD": (candle(-2), candle(-1))})
    snapshot = await MarketSnapshotBuilder(source, source_label="test:source").build(
        request(lookback=2)
    )

    payload = market_snapshot_to_payload(snapshot)

    assert payload == {
        "as_of": AS_OF.isoformat(),
        "source": f"test:source:granularity=ONE_HOUR:lookback=2:as_of={AS_OF.isoformat()}",
        "series": [
            {
                "symbol": "BTC-USD",
                "granularity": "ONE_HOUR",
                "candles": [
                    {
                        "ts": (AS_OF - timedelta(hours=2)).isoformat(),
                        "open": "100",
                        "high": "100",
                        "low": "100",
                        "close": "100",
                        "volume": "1000",
                    },
                    {
                        "ts": (AS_OF - timedelta(hours=1)).isoformat(),
                        "open": "100",
                        "high": "100",
                        "low": "100",
                        "close": "100",
                        "volume": "1000",
                    },
                ],
            }
        ],
    }
