from __future__ import annotations

import pandas as pd
import pytest
from tests.unit.gpt_trader.features.data.data_module_test_helpers import (
    CacheStub,
    QualityStub,
    StorageStub,
    _make_frame,
)

from gpt_trader.features.data.types import DataQuery, DataSource, DataType


def test_store_data_updates_cache_with_warning(data_service) -> None:
    quality: QualityStub = data_service["quality"]
    quality.acceptable = False

    frame = _make_frame()
    assert data_service["service"].store_data("BTC-USD", frame)

    storage: StorageStub = data_service["storage"]
    cache: CacheStub = data_service["cache"]

    assert storage.store_calls[0]["symbol"] == "BTC-USD"
    assert cache.put_calls, "cache should be updated"


def test_fetch_data_returns_cache_hit(data_service) -> None:
    cache: CacheStub = data_service["cache"]
    frame = _make_frame()
    query = DataQuery(
        symbols=["ETH-USD"],
        start_date=frame.index.min(),
        end_date=frame.index.max(),
        data_type=DataType.OHLCV,
        source=DataSource.COINBASE,
    )
    cache.put(query.get_cache_key(), frame)

    result = data_service["service"].fetch_data(query)
    assert result is not None
    pd.testing.assert_frame_equal(result, frame)


def test_fetch_data_loads_from_storage_and_updates_cache(data_service) -> None:
    storage: StorageStub = data_service["storage"]
    cache: CacheStub = data_service["cache"]

    frame = _make_frame()
    storage.fetch_return = frame

    query = DataQuery(
        symbols=["BTC-USD"],
        start_date=frame.index.min(),
        end_date=frame.index.max(),
        data_type=DataType.OHLCV,
    )
    result = data_service["service"].fetch_data(query)

    assert result is not None
    pd.testing.assert_frame_equal(result, frame)
    assert cache.put_calls, "storage results should populate cache"
    assert storage.last_fetch_query is query


def test_fetch_data_downloads_when_storage_misses(
    data_service, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage: StorageStub = data_service["storage"]
    storage.fetch_return = None

    downloaded_frame = _make_frame()

    def _download_stub(symbols, start, end, interval):
        return {symbol: downloaded_frame for symbol in symbols}

    monkeypatch.setattr(data_service["service"], "download_from_coinbase", _download_stub)

    query = DataQuery(
        symbols=["SOL-USD"],
        start_date=downloaded_frame.index.min(),
        end_date=downloaded_frame.index.max(),
        data_type=DataType.OHLCV,
        source=DataSource.COINBASE,
    )

    result = data_service["service"].fetch_data(query)

    assert result is not None
    pd.testing.assert_frame_equal(result, downloaded_frame)
