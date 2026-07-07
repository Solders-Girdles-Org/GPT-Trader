"""Stooq daily equity candle source: parsing, timestamps, and loud failures.

All tests run against a fake HTTP transport; nothing touches the network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.features.recorder.equities_candles import (
    EquitiesCandleFeedError,
    StooqDailyCandleFetcher,
)

START = datetime(2026, 6, 1, tzinfo=UTC)
END = datetime(2026, 6, 12, tzinfo=UTC)

NORMAL_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-06-08,100.0,102.5,99.5,101.25,1000000\n"
    "2026-06-09,101.3,103.0,100.8,102.75,1200000\n"
    "2026-06-10,102.8,104.2,102.0,103.5,900000\n"
)
WINTER_CSV = "Date,Open,High,Low,Close,Volume\n2026-01-05,100,101,99,100.5,500000\n"
NO_DATA_RESPONSE = "No data"
MALFORMED_ROW_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-06-08,100.0,102.5,99.5,101.25,1000000\n"
    "2026-06-09,not-a-price,103.0,100.8,102.75,1200000\n"
)


class FakeHttpTransport:
    def __init__(self, body: str) -> None:
        self.body = body
        self.urls: list[str] = []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        return self.body


def _fetcher(body: str) -> tuple[StooqDailyCandleFetcher, FakeHttpTransport]:
    transport = FakeHttpTransport(body)
    return StooqDailyCandleFetcher(http_get_text=transport), transport


async def _fetch(
    fetcher: StooqDailyCandleFetcher,
    *,
    symbol: str = "AAPL",
    granularity: str = "ONE_DAY",
    start: datetime = START,
    end: datetime = END,
):
    return await fetcher.fetch_candles(symbol=symbol, granularity=granularity, start=start, end=end)


@pytest.mark.asyncio
async def test_fetch_parses_csv_and_timestamps_at_session_close_utc() -> None:
    fetcher, transport = _fetcher(NORMAL_CSV)

    candles = await _fetch(fetcher)

    assert len(candles) == 3
    first = candles[0]
    # 2026-06-08 session close is 16:00 America/New_York (EDT) == 20:00 UTC.
    assert first.ts == datetime(2026, 6, 8, 20, 0, tzinfo=UTC)
    assert first.open == Decimal("100.0")
    assert first.high == Decimal("102.5")
    assert first.low == Decimal("99.5")
    assert first.close == Decimal("101.25")
    assert first.volume == Decimal("1000000")
    assert [candle.ts for candle in candles] == sorted(candle.ts for candle in candles)
    # Venue-neutral ticker maps to Stooq's <ticker>.us form inside the source.
    assert transport.urls == ["https://stooq.com/q/d/l/?s=aapl.us&i=d&d1=20260601&d2=20260612"]


@pytest.mark.asyncio
async def test_fetch_timestamps_winter_sessions_at_est_close() -> None:
    fetcher, _ = _fetcher(WINTER_CSV)

    candles = await _fetch(fetcher, start=datetime(2026, 1, 1, tzinfo=UTC), end=END)

    # 2026-01-05 session close is 16:00 America/New_York (EST) == 21:00 UTC.
    assert [candle.ts for candle in candles] == [datetime(2026, 1, 5, 21, 0, tzinfo=UTC)]


@pytest.mark.asyncio
async def test_fetch_filters_candles_outside_requested_window() -> None:
    fetcher, _ = _fetcher(NORMAL_CSV)

    candles = await _fetch(fetcher, end=datetime(2026, 6, 10, 0, 0, tzinfo=UTC))

    # The 2026-06-10 session closes after the window end; only earlier bars remain.
    assert [candle.ts for candle in candles] == [
        datetime(2026, 6, 8, 20, 0, tzinfo=UTC),
        datetime(2026, 6, 9, 20, 0, tzinfo=UTC),
    ]


@pytest.mark.asyncio
async def test_fetch_rejects_intraday_granularity_before_any_network_call() -> None:
    fetcher, transport = _fetcher(NORMAL_CSV)

    with pytest.raises(EquitiesCandleFeedError, match="ONE_DAY candles only"):
        await _fetch(fetcher, granularity="ONE_HOUR")

    assert transport.urls == []


@pytest.mark.asyncio
async def test_fetch_raises_loudly_for_unknown_symbol_response() -> None:
    fetcher, _ = _fetcher(NO_DATA_RESPONSE)

    with pytest.raises(EquitiesCandleFeedError, match="no daily candles for 'NOPE'"):
        await _fetch(fetcher, symbol="NOPE")


@pytest.mark.asyncio
async def test_fetch_raises_loudly_for_header_only_response() -> None:
    fetcher, _ = _fetcher("Date,Open,High,Low,Close,Volume\n")

    with pytest.raises(EquitiesCandleFeedError, match="no daily candles"):
        await _fetch(fetcher)


@pytest.mark.asyncio
async def test_fetch_raises_loudly_for_bot_gate_or_access_denial() -> None:
    for body in ("Access denied", "<!DOCTYPE html><html>challenge</html>"):
        fetcher, _ = _fetcher(body)

        with pytest.raises(EquitiesCandleFeedError, match="refused programmatic access"):
            await _fetch(fetcher)


@pytest.mark.asyncio
async def test_fetch_raises_loudly_for_malformed_row() -> None:
    fetcher, _ = _fetcher(MALFORMED_ROW_CSV)

    with pytest.raises(EquitiesCandleFeedError, match="Malformed Stooq candle row"):
        await _fetch(fetcher)


@pytest.mark.asyncio
async def test_fetch_rejects_non_plain_ticker_symbols() -> None:
    fetcher, transport = _fetcher(NORMAL_CSV)

    with pytest.raises(EquitiesCandleFeedError, match="plain tickers"):
        await _fetch(fetcher, symbol="aapl.us")

    assert transport.urls == []
