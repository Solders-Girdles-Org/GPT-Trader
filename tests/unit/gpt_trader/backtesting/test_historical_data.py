"""Edge coverage for HistoricalDataManager cache and coverage behavior."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from gpt_trader.backtesting.data.manager import HistoricalDataManager
from gpt_trader.core import Candle
from gpt_trader.features.brokerages.coinbase.historical_candles import (
    CoinbaseHistoricalFetcher,
)


def _make_manager(tmp_path: Path, validate_quality: bool = False) -> HistoricalDataManager:
    fetcher = Mock(spec=CoinbaseHistoricalFetcher)
    return HistoricalDataManager(
        fetcher=fetcher,
        cache_dir=tmp_path,
        validate_quality=validate_quality,
    )


def test_read_from_cache_invalid_json_returns_empty(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    cache_path = manager._get_cache_path("BTC-USD", "ONE_MINUTE")
    cache_path.write_text("not-json")

    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(minutes=5)

    assert manager._read_from_cache("BTC-USD", "ONE_MINUTE", start, end) == []


def test_write_to_cache_deduplicates_and_sorts(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    ts1 = datetime(2024, 1, 1, 0, 0, 0)
    ts2 = datetime(2024, 1, 1, 0, 1, 0)
    ts3 = datetime(2024, 1, 1, 0, 2, 0)

    candles_first = [
        Candle(
            ts=ts2,
            open=Decimal("2"),
            high=Decimal("2"),
            low=Decimal("2"),
            close=Decimal("2"),
            volume=Decimal("2"),
        ),
        Candle(
            ts=ts1,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        ),
    ]
    candles_second = [
        Candle(
            ts=ts1,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        ),
        Candle(
            ts=ts3,
            open=Decimal("3"),
            high=Decimal("3"),
            low=Decimal("3"),
            close=Decimal("3"),
            volume=Decimal("3"),
        ),
    ]

    manager._write_to_cache("BTC-USD", "ONE_MINUTE", candles_first)
    manager._write_to_cache("BTC-USD", "ONE_MINUTE", candles_second)

    cache_path = manager._get_cache_path("BTC-USD", "ONE_MINUTE")
    data = json.loads(cache_path.read_text())
    timestamps = [item["ts"] for item in data.get("candles", [])]

    assert timestamps == [ts1.isoformat(), ts2.isoformat(), ts3.isoformat()]


@pytest.mark.asyncio
async def test_get_candles_returns_cached_without_fetch(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path, validate_quality=False)
    manager.fetcher.fetch_candles = AsyncMock(return_value=[])

    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(minutes=3)

    cached = [
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
    manager._write_to_cache("BTC-USD", "ONE_MINUTE", cached)
    manager._coverage_index = {
        "BTC-USD": {
            "ONE_MINUTE": [(start, end)],
        }
    }

    result = await manager.get_candles("BTC-USD", "ONE_MINUTE", start, end)

    assert [c.ts for c in result] == [c.ts for c in cached]
    manager.fetcher.fetch_candles.assert_not_called()


def test_identify_gaps_returns_full_range_when_no_coverage(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(hours=1)

    assert manager._identify_gaps("BTC-USD", "ONE_HOUR", start, end) == [(start, end)]


def test_identify_gaps_returns_empty_when_range_covered(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(hours=1)
    manager._coverage_index = {
        "BTC-USD": {"ONE_HOUR": [(start - timedelta(minutes=5), end + timedelta(minutes=5))]}
    }

    assert manager._identify_gaps("BTC-USD", "ONE_HOUR", start, end) == []


def test_identify_gaps_returns_missing_segments_for_partial_overlap(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(hours=3)

    # Only the last hour is covered.
    manager._coverage_index = {
        "BTC-USD": {"ONE_HOUR": [(start + timedelta(hours=2), end)]},
    }

    assert manager._identify_gaps("BTC-USD", "ONE_HOUR", start, end) == [
        (start, start + timedelta(hours=2))
    ]


def test_load_coverage_index_invalid_json(tmp_path: Path) -> None:
    index_path = tmp_path / "_coverage_index.json"
    index_path.write_text("not-json")

    manager = _make_manager(tmp_path)

    assert manager._coverage_index == {}


def test_update_coverage_writes_index_file(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(minutes=5)

    manager._update_coverage("BTC-USD", "ONE_MINUTE", start, end)

    data = json.loads((tmp_path / "_coverage_index.json").read_text())
    assert data["BTC-USD"]["ONE_MINUTE"] == [[start.isoformat(), end.isoformat()]]
