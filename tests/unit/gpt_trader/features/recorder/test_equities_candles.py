"""Daily equity candle sources: parsing, timestamps, and loud failures.

Covers both vendors behind the ``HistoricalCandleSource`` seam — Alpaca
(vendor of record, #1238) and the dormant Stooq source. All tests run
against a fake HTTP transport; nothing touches the network or needs keys.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.features.recorder.equities_candles import (
    AlpacaDailyCandleFetcher,
    AlpacaDataCredentials,
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


# --- Alpaca (vendor of record, #1238) ---

ALPACA_BARS_JSON = (
    '{"bars": ['
    '{"t": "2026-06-08T04:00:00Z", "o": 100.0, "h": 102.5, "l": 99.5,'
    ' "c": 101.25, "v": 1000000, "n": 5000, "vw": 101.0},'
    '{"t": "2026-06-09T04:00:00Z", "o": 101.3, "h": 103.0, "l": 100.8,'
    ' "c": 102.75, "v": 1200000, "n": 6000, "vw": 102.0},'
    '{"t": "2026-06-10T04:00:00Z", "o": 102.8, "h": 104.2, "l": 102.0,'
    ' "c": 103.5, "v": 900000, "n": 4000, "vw": 103.0}'
    '], "symbol": "AAPL", "next_page_token": null}'
)
ALPACA_WINTER_BARS_JSON = (
    '{"bars": [{"t": "2026-01-05T05:00:00Z", "o": 100, "h": 101, "l": 99,'
    ' "c": 100.5, "v": 500000}], "symbol": "AAPL", "next_page_token": null}'
)
ALPACA_EMPTY_BARS_JSON = '{"bars": [], "symbol": "NOPE", "next_page_token": null}'
ALPACA_NULL_BARS_JSON = '{"bars": null, "symbol": "NOPE", "next_page_token": null}'
ALPACA_ERROR_JSON = '{"message": "forbidden."}'
ALPACA_TRUNCATED_JSON = (
    '{"bars": [{"t": "2026-06-08T04:00:00Z", "o": 100.0, "h": 102.5, "l": 99.5,'
    ' "c": 101.25, "v": 1000000}], "symbol": "AAPL", "next_page_token": "abc123"}'
)
ALPACA_MALFORMED_BAR_JSON = (
    '{"bars": [{"t": "2026-06-08T04:00:00Z", "o": "not-a-price", "h": 102.5,'
    ' "l": 99.5, "c": 101.25, "v": 1000000}], "symbol": "AAPL", "next_page_token": null}'
)


def _alpaca_fetcher(body: str) -> tuple[AlpacaDailyCandleFetcher, FakeHttpTransport]:
    transport = FakeHttpTransport(body)
    return AlpacaDailyCandleFetcher(http_get_text=transport), transport


async def _alpaca_fetch(
    fetcher: AlpacaDailyCandleFetcher,
    *,
    symbol: str = "AAPL",
    granularity: str = "ONE_DAY",
    start: datetime = START,
    end: datetime = END,
):
    return await fetcher.fetch_candles(symbol=symbol, granularity=granularity, start=start, end=end)


@pytest.mark.asyncio
async def test_alpaca_fetch_parses_bars_and_timestamps_at_session_close_utc() -> None:
    fetcher, transport = _alpaca_fetcher(ALPACA_BARS_JSON)

    candles = await _alpaca_fetch(fetcher, symbol="aapl")

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
    # Lowercase input normalizes to the plain uppercase ticker in the URL.
    assert transport.urls == [
        "https://data.alpaca.markets/v2/stocks/AAPL/bars"
        "?timeframe=1Day&adjustment=split&feed=iex&limit=10000&sort=asc"
        "&start=2026-06-01&end=2026-06-12"
    ]


@pytest.mark.asyncio
async def test_alpaca_fetch_timestamps_winter_sessions_at_est_close() -> None:
    fetcher, _ = _alpaca_fetcher(ALPACA_WINTER_BARS_JSON)

    candles = await _alpaca_fetch(fetcher, start=datetime(2026, 1, 1, tzinfo=UTC), end=END)

    # 2026-01-05 session close is 16:00 America/New_York (EST) == 21:00 UTC.
    assert [candle.ts for candle in candles] == [datetime(2026, 1, 5, 21, 0, tzinfo=UTC)]


@pytest.mark.asyncio
async def test_alpaca_fetch_filters_candles_outside_requested_window() -> None:
    fetcher, _ = _alpaca_fetcher(ALPACA_BARS_JSON)

    candles = await _alpaca_fetch(fetcher, end=datetime(2026, 6, 10, 0, 0, tzinfo=UTC))

    # The 2026-06-10 session closes after the window end; only earlier bars remain.
    assert [candle.ts for candle in candles] == [
        datetime(2026, 6, 8, 20, 0, tzinfo=UTC),
        datetime(2026, 6, 9, 20, 0, tzinfo=UTC),
    ]


@pytest.mark.asyncio
async def test_alpaca_fetch_rejects_intraday_granularity_before_any_network_call() -> None:
    fetcher, transport = _alpaca_fetcher(ALPACA_BARS_JSON)

    with pytest.raises(EquitiesCandleFeedError, match="ONE_DAY candles only"):
        await _alpaca_fetch(fetcher, granularity="ONE_HOUR")

    assert transport.urls == []


@pytest.mark.asyncio
async def test_alpaca_fetch_rejects_non_plain_ticker_symbols() -> None:
    fetcher, transport = _alpaca_fetcher(ALPACA_BARS_JSON)

    with pytest.raises(EquitiesCandleFeedError, match="plain tickers"):
        await _alpaca_fetch(fetcher, symbol="aapl.us")

    assert transport.urls == []


@pytest.mark.asyncio
async def test_alpaca_fetch_raises_loudly_for_empty_or_null_bars() -> None:
    for body in (ALPACA_EMPTY_BARS_JSON, ALPACA_NULL_BARS_JSON):
        fetcher, _ = _alpaca_fetcher(body)

        with pytest.raises(EquitiesCandleFeedError, match="no daily candles for 'NOPE'"):
            await _alpaca_fetch(fetcher, symbol="NOPE")


@pytest.mark.asyncio
async def test_alpaca_fetch_raises_loudly_for_error_payload() -> None:
    fetcher, _ = _alpaca_fetcher(ALPACA_ERROR_JSON)

    with pytest.raises(EquitiesCandleFeedError, match="did not return bars.*forbidden"):
        await _alpaca_fetch(fetcher)


@pytest.mark.asyncio
async def test_alpaca_fetch_raises_loudly_for_non_json_response() -> None:
    fetcher, _ = _alpaca_fetcher("<!DOCTYPE html><html>challenge</html>")

    with pytest.raises(EquitiesCandleFeedError, match="non-JSON response"):
        await _alpaca_fetch(fetcher)


@pytest.mark.asyncio
async def test_alpaca_fetch_refuses_truncated_pagination() -> None:
    fetcher, _ = _alpaca_fetcher(ALPACA_TRUNCATED_JSON)

    with pytest.raises(EquitiesCandleFeedError, match="truncated"):
        await _alpaca_fetch(fetcher)


@pytest.mark.asyncio
async def test_alpaca_fetch_raises_loudly_for_malformed_bar() -> None:
    fetcher, _ = _alpaca_fetcher(ALPACA_MALFORMED_BAR_JSON)

    with pytest.raises(EquitiesCandleFeedError, match="Malformed Alpaca candle bar"):
        await _alpaca_fetch(fetcher)


def test_alpaca_default_transport_requires_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

    with pytest.raises(EquitiesCandleFeedError, match="Alpaca credentials not found"):
        AlpacaDailyCandleFetcher()


def test_alpaca_credentials_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")

    credentials = AlpacaDataCredentials.from_env()

    assert credentials == AlpacaDataCredentials(key_id="key-id", secret_key="secret")
