from __future__ import annotations

from tests.unit.gpt_trader.features.data.data_module_test_helpers import (
    CacheStub,
    StorageStub,
    _make_frame,
)


def test_cache_data_delegates_to_cache(data_service) -> None:
    cache: CacheStub = data_service["cache"]
    frame = _make_frame()
    assert data_service["service"].cache_data("key", frame, ttl_seconds=30)
    assert cache.put_calls[0] == ("key", 30)


def test_clean_old_data_clears_cache(data_service) -> None:
    storage: StorageStub = data_service["storage"]
    storage.delete_before_return = 7

    deleted = data_service["service"].clean_old_data(days_to_keep=30)
    assert deleted == 7
    assert data_service["cache"]._clear_expired_called


def test_get_storage_stats_combines_sources(data_service) -> None:
    stats = data_service["service"].get_storage_stats()
    assert stats.total_records == 10
    assert stats.cache_entries == 0
    assert stats.symbols_count == 3
