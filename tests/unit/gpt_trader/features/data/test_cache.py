from __future__ import annotations

import pandas as pd
from freezegun import freeze_time

from gpt_trader.features.data.cache import DataCache


def _heavy_frame(size: int = 500) -> pd.DataFrame:
    return pd.DataFrame({"value": ["x" * 64 for _ in range(size)]})


def test_cache_entry_expiration_and_hits() -> None:
    cache = DataCache(max_size_mb=1)
    frame = _heavy_frame(10)

    with freeze_time("2024-01-01 00:00:00"):
        assert cache.put("recent", frame, ttl_seconds=60)

    with freeze_time("2024-01-01 00:00:30"):
        cached = cache.get("recent")
        assert cached is not None

    with freeze_time("2024-01-01 00:01:05"):
        assert cache.get("recent") is None


def test_cache_evicts_least_recent_when_exceeding_limit() -> None:
    cache = DataCache(max_size_mb=0.0005)  # ~500 bytes
    first = _heavy_frame(50)
    second = _heavy_frame(50)

    cache.put("k1", first)
    assert "k1" in cache.cache

    cache.put("k2", second)
    assert "k2" in cache.cache
    assert "k1" not in cache.cache, "first entry should be evicted to honor size limit"


def test_cache_stats_reported_correctly() -> None:
    cache = DataCache()
    frame = _heavy_frame(5)

    cache.put("k1", frame)
    cache.get("k1")
    cache.get("missing")

    stats = cache.get_stats()
    assert stats["entries"] == 1
    assert stats["total_hits"] == 1
    assert stats["total_misses"] == 1


def test_clear_expired_removes_stale_entries() -> None:
    cache = DataCache()
    frame = _heavy_frame(5)

    with freeze_time("2024-01-01 00:00:00"):
        cache.put("fresh", frame, ttl_seconds=30)
        cache.put("stale", frame, ttl_seconds=5)

    with freeze_time("2024-01-01 00:00:10"):
        expired = cache.clear_expired()

    assert expired == 1
    assert "fresh" in cache.cache
    assert "stale" not in cache.cache
