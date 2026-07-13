"""Recorder-owned snapshot sources: transport wiring and cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gpt_trader.core import Candle
from gpt_trader.features.recorder import MarketSnapshotBuildRequest
from gpt_trader.features.recorder.equities_candles import EquitiesCandleFeedError
from gpt_trader.features.recorder.snapshot_source import (
    build_alpaca_equities_market_snapshot,
    build_coinbase_market_snapshot,
    build_stooq_equities_market_snapshot,
)

AS_OF = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _request() -> MarketSnapshotBuildRequest:
    return MarketSnapshotBuildRequest(
        symbols=("BTC-USD",),
        granularity="ONE_HOUR",
        lookback=2,
        as_of=AS_OF,
    )


def _hourly_candles(count: int) -> list[Candle]:
    return [
        Candle(
            ts=AS_OF - timedelta(hours=count - index),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("10"),
        )
        for index in range(count)
    ]


class FakeClient:
    def __init__(self, *, base_url: str, auth: object, api_mode: str) -> None:
        self.base_url = base_url
        self.auth = auth
        self.api_mode = api_mode
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeFetcher:
    def __init__(self, *, client: FakeClient) -> None:
        self.client = client

    async def fetch_candles(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        return _hourly_candles(3)


class FailingFetcher(FakeFetcher):
    async def fetch_candles(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        raise RuntimeError("candle fetch failed")


def _patch_transport(monkeypatch: pytest.MonkeyPatch, fetcher_cls: type) -> list[FakeClient]:
    clients: list[FakeClient] = []

    def _make_client(**kwargs: object) -> FakeClient:
        client = FakeClient(**kwargs)  # type: ignore[arg-type]
        clients.append(client)
        return client

    monkeypatch.setattr(
        "gpt_trader.features.brokerages.coinbase.client.CoinbaseClient",
        _make_client,
    )
    monkeypatch.setattr(
        "gpt_trader.features.brokerages.coinbase.historical_candles.CoinbaseHistoricalFetcher",
        fetcher_cls,
    )
    return clients


@pytest.mark.asyncio
async def test_build_wires_read_only_client_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = _patch_transport(monkeypatch, FakeFetcher)

    snapshot = await build_coinbase_market_snapshot(
        _request(),
        base_url="https://example.test",
        source_label="test:candles",
    )

    assert [client.base_url for client in clients] == ["https://example.test"]
    assert clients[0].auth is None
    assert clients[0].api_mode == "advanced"
    assert clients[0].closed is True
    assert snapshot.source.startswith("test:candles:granularity=ONE_HOUR:lookback=2")
    assert snapshot.symbols() == ("BTC-USD",)


@pytest.mark.asyncio
async def test_build_closes_client_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = _patch_transport(monkeypatch, FailingFetcher)

    with pytest.raises(RuntimeError, match="candle fetch failed"):
        await build_coinbase_market_snapshot(_request())

    assert len(clients) == 1
    assert clients[0].closed is True


class FakeEquitiesFetcher:
    base_urls: list[str] = []

    def __init__(self, *, base_url: str) -> None:
        FakeEquitiesFetcher.base_urls.append(base_url)

    async def fetch_candles(
        self,
        *,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        # Daily bars timestamped at US session close (20:00 UTC in June).
        return [
            Candle(
                ts=datetime(2026, 6, day, 20, 0, tzinfo=UTC),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("1000000"),
            )
            for day in (9, 10, 11)
        ]


@pytest.mark.asyncio
async def test_build_stooq_equities_snapshot_labels_source_stooq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeEquitiesFetcher.base_urls = []
    monkeypatch.setattr(
        "gpt_trader.features.recorder.equities_candles.StooqDailyCandleFetcher",
        FakeEquitiesFetcher,
    )

    snapshot = await build_stooq_equities_market_snapshot(
        MarketSnapshotBuildRequest(
            symbols=("AAPL",),
            granularity="ONE_DAY",
            lookback=2,
            as_of=AS_OF,
        )
    )

    assert FakeEquitiesFetcher.base_urls == ["https://stooq.com"]
    assert snapshot.source.startswith("stooq:market-candles:granularity=ONE_DAY:lookback=2")
    assert snapshot.symbols() == ("AAPL",)
    # Only the 2026-06-10 session bar is both inside the lookback window and
    # completed (ts + ONE_DAY <= as_of) before the 2026-06-12 12:00 as_of.
    series = snapshot.series_for("AAPL")
    assert series is not None
    assert [candle.ts for candle in series.candles] == [
        datetime(2026, 6, 10, 20, 0, tzinfo=UTC),
    ]


@pytest.mark.asyncio
async def test_build_stooq_equities_snapshot_rejects_intraday_granularity() -> None:
    # Rejection happens before any transport call, so no network is touched.
    with pytest.raises(EquitiesCandleFeedError, match="ONE_DAY candles only"):
        await build_stooq_equities_market_snapshot(
            MarketSnapshotBuildRequest(
                symbols=("AAPL",),
                granularity="ONE_HOUR",
                lookback=2,
                as_of=AS_OF,
            )
        )


@pytest.mark.asyncio
async def test_build_alpaca_equities_snapshot_labels_source_alpaca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeEquitiesFetcher.base_urls = []
    monkeypatch.setattr(
        "gpt_trader.features.recorder.equities_candles.AlpacaDailyCandleFetcher",
        FakeEquitiesFetcher,
    )

    snapshot = await build_alpaca_equities_market_snapshot(
        MarketSnapshotBuildRequest(
            symbols=("AAPL",),
            granularity="ONE_DAY",
            lookback=2,
            as_of=AS_OF,
        )
    )

    assert FakeEquitiesFetcher.base_urls == ["https://data.alpaca.markets"]
    assert snapshot.source.startswith("alpaca:market-candles:granularity=ONE_DAY:lookback=2")
    assert snapshot.symbols() == ("AAPL",)


@pytest.mark.asyncio
async def test_build_alpaca_equities_snapshot_rejects_intraday_granularity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Credentials satisfy the default transport; rejection happens before
    # any network call is attempted.
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")

    with pytest.raises(EquitiesCandleFeedError, match="ONE_DAY candles only"):
        await build_alpaca_equities_market_snapshot(
            MarketSnapshotBuildRequest(
                symbols=("AAPL",),
                granularity="ONE_HOUR",
                lookback=2,
                as_of=AS_OF,
            )
        )
