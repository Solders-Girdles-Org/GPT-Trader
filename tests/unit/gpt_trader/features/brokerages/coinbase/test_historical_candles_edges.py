"""Edge coverage for CoinbaseHistoricalFetcher chunking and deduplication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from gpt_trader.core import Candle
from gpt_trader.features.brokerages.coinbase.historical_candles import (
    GRANULARITY_SECONDS,
    CoinbaseHistoricalFetcher,
)
from gpt_trader.features.recorder.snapshot_builder import (
    _CANONICAL_GRANULARITY_BY_ALIAS,
    granularity_duration,
)


@pytest.mark.asyncio
async def test_fetch_candles_deduplicates_and_sorts_chunks() -> None:
    client = Mock()
    fetcher = CoinbaseHistoricalFetcher(client=client)

    start = datetime(2024, 1, 1, 0, 0, 0)
    mid = datetime(2024, 1, 1, 0, 2, 0)
    end = datetime(2024, 1, 1, 0, 4, 0)

    candles_chunk_1 = [
        Candle(
            ts=start,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        ),
        Candle(
            ts=start + timedelta(minutes=1),
            open=Decimal("2"),
            high=Decimal("2"),
            low=Decimal("2"),
            close=Decimal("2"),
            volume=Decimal("2"),
        ),
    ]
    candles_chunk_2 = [
        Candle(
            ts=start + timedelta(minutes=1),
            open=Decimal("2"),
            high=Decimal("2"),
            low=Decimal("2"),
            close=Decimal("2"),
            volume=Decimal("2"),
        ),
        Candle(
            ts=start + timedelta(minutes=2),
            open=Decimal("3"),
            high=Decimal("3"),
            low=Decimal("3"),
            close=Decimal("3"),
            volume=Decimal("3"),
        ),
    ]

    fetcher._create_chunks = Mock(return_value=[(start, mid), (mid, end)])
    fetcher._fetch_chunk = AsyncMock(side_effect=[candles_chunk_1, candles_chunk_2])
    fetcher._rate_limit = AsyncMock()

    result = await fetcher.fetch_candles(
        symbol="BTC-USD",
        granularity="ONE_MINUTE",
        start=start,
        end=end,
    )

    assert [c.ts for c in result] == [
        start,
        start + timedelta(minutes=1),
        start + timedelta(minutes=2),
    ]
    fetcher._fetch_chunk.assert_any_call(
        symbol="BTC-USD",
        granularity="ONE_MINUTE",
        start=start,
        end=mid,
    )
    fetcher._fetch_chunk.assert_any_call(
        symbol="BTC-USD",
        granularity="ONE_MINUTE",
        start=mid,
        end=end,
    )


def test_create_chunks_splits_over_limit() -> None:
    fetcher = CoinbaseHistoricalFetcher(client=Mock())
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(seconds=60 * 301)

    chunks = fetcher._create_chunks(start, end, candle_seconds=60, max_candles=300)

    assert len(chunks) == 2
    assert chunks[0] == (start, start + timedelta(seconds=60 * 299))
    assert chunks[1] == (start + timedelta(seconds=60 * 299), end)


def test_granularity_to_seconds_unknown_defaults() -> None:
    fetcher = CoinbaseHistoricalFetcher(client=Mock())

    assert fetcher._granularity_to_seconds("UNKNOWN") == 60


def test_every_builder_granularity_is_fetchable() -> None:
    """Every granularity the snapshot builder accepts must be fetchable.

    The builder normalizes aliases to Coinbase enum names before calling the
    candle source, so its canonical set must exactly match the fetcher's
    granularity map — a builder-only entry would pass input validation and
    then fail at the REST fetch, while a fetcher-only entry is unreachable.
    """
    builder_granularities = set(_CANONICAL_GRANULARITY_BY_ALIAS.values())

    assert builder_granularities == set(GRANULARITY_SECONDS)
    for alias, canonical in _CANONICAL_GRANULARITY_BY_ALIAS.items():
        duration = granularity_duration(alias)
        assert duration is not None, alias
        assert duration.total_seconds() == GRANULARITY_SECONDS[canonical]


def test_granularity_to_seconds_supports_four_hour() -> None:
    fetcher = CoinbaseHistoricalFetcher(client=Mock())

    assert fetcher._granularity_to_seconds("FOUR_HOUR") == 14400


def test_create_chunks_uses_four_hour_duration() -> None:
    fetcher = CoinbaseHistoricalFetcher(client=Mock())
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(hours=4 * 301)

    chunks = fetcher._create_chunks(
        start,
        end,
        candle_seconds=fetcher._granularity_to_seconds("FOUR_HOUR"),
        max_candles=300,
    )

    assert chunks == [
        (start, start + timedelta(hours=4 * 299)),
        (start + timedelta(hours=4 * 299), end),
    ]


@pytest.mark.asyncio
async def test_fetch_chunk_calls_client_get_candles_and_parses_timestamps() -> None:
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    epoch_start = int(start.timestamp())

    client = Mock()
    client.get_candles = Mock(
        return_value={
            "candles": [
                {
                    "start": "2024-01-01T00:00:00Z",
                    "open": "1",
                    "high": "1.5",
                    "low": "0.5",
                    "close": "1.2",
                    "volume": "10",
                },
                {
                    "start": epoch_start + 60,
                    "open": "2",
                    "high": "2.5",
                    "low": "1.5",
                    "close": "2.2",
                    "volume": "20",
                },
            ]
        }
    )

    fetcher = CoinbaseHistoricalFetcher(client=client)
    candles = await fetcher._fetch_chunk(
        symbol="BTC-USD",
        granularity="ONE_MINUTE",
        start=start,
        end=end,
    )

    args, kwargs = client.get_candles.call_args
    assert args == ("BTC-USD", "ONE_MINUTE", 300)
    assert kwargs["start"] == start
    assert kwargs["end"] == end

    assert [c.ts.isoformat() for c in candles] == [
        start.isoformat(),
        (start + timedelta(minutes=1)).isoformat(),
    ]


class _InclusiveCandlesClient:
    """Mimics Coinbase: bars starting in [start, end], newest `limit` kept."""

    def __init__(self, step: timedelta) -> None:
        self.step = step

    def get_candles(self, symbol, granularity, limit, *, start, end):  # noqa: ANN001
        bars = []
        ts = start
        while ts <= end:
            bars.append({"start": str(int(ts.timestamp())), **dict.fromkeys(_OHLCV, "1")})
            ts += self.step
        return {"candles": list(reversed(bars))[:limit]}


_OHLCV = ("open", "high", "low", "close", "volume")


@pytest.mark.asyncio
async def test_fetch_candles_keeps_first_bar_when_api_end_is_inclusive() -> None:
    """A 300-bar chunk must not span 301 inclusive bars, or the API drops its first."""
    start = datetime(2026, 9, 3, tzinfo=UTC)
    end = start + timedelta(hours=912)
    fetcher = CoinbaseHistoricalFetcher(client=_InclusiveCandlesClient(timedelta(hours=1)))
    fetcher._rate_limit = AsyncMock()

    result = await fetcher.fetch_candles("BTC-USD", "ONE_HOUR", start, end)

    in_window = [c.ts for c in result if c.ts < end]
    assert in_window == [start + timedelta(hours=i) for i in range(912)]
