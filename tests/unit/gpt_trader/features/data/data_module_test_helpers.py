"""Shared helpers for DataService tests."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from gpt_trader.features.data.types import DataQuery, DataSource, DataType
from gpt_trader.utilities.datetime_helpers import utc_now


class StorageStub:
    def __init__(self) -> None:
        self.store_calls: list[dict[str, object]] = []
        self.fetch_return: pd.DataFrame | None = None
        self.delete_before_return = 0
        self.last_fetch_query: DataQuery | None = None
        self.delete_cutoff: datetime | None = None

    def store(
        self,
        *,
        symbol: str,
        data: pd.DataFrame,
        data_type: DataType,
        source: DataSource,
        metadata: dict | None = None,
    ) -> bool:
        self.store_calls.append(
            {
                "symbol": symbol,
                "data": data,
                "data_type": data_type,
                "source": source,
                "metadata": metadata,
            }
        )
        return True

    def fetch(self, query: DataQuery) -> pd.DataFrame | None:
        self.last_fetch_query = query
        return self.fetch_return

    def delete_before(self, cutoff: datetime) -> int:
        self.delete_cutoff = cutoff
        return self.delete_before_return

    def get_stats(self) -> dict[str, object]:
        now = utc_now()
        return {
            "total_records": 10,
            "total_size_mb": 1.5,
            "oldest_record": now - timedelta(days=10),
            "newest_record": now,
            "symbols_count": 3,
        }


class CacheStub:
    def __init__(self) -> None:
        self.data: dict[str, pd.DataFrame] = {}
        self.put_calls: list[tuple[str, int | None]] = []
        self.total_hits = 0
        self.total_misses = 0
        self._clear_expired_called = False

    def put(self, key: str, data: pd.DataFrame, ttl_seconds: int = 3600) -> bool:
        self.data[key] = data.copy()
        self.put_calls.append((key, ttl_seconds))
        return True

    def get(self, key: str) -> pd.DataFrame | None:
        return self.data.get(key)

    def clear_expired(self) -> int:
        self._clear_expired_called = True
        cleared = len(self.data)
        self.data.clear()
        return cleared

    def get_stats(self) -> dict[str, object]:
        return {
            "entries": len(self.data),
            "size_mb": 0.1,
            "hit_rate": 0.5,
            "total_hits": 5,
            "total_misses": 5,
            "expired_entries": 0,
        }


class QualityStub:
    def __init__(self) -> None:
        self.acceptable = True
        self.calls: list[pd.DataFrame] = []

    class _Result:
        def __init__(self, acceptable: bool) -> None:
            self._acceptable = acceptable

        def overall_score(self) -> float:
            return 0.42

        def is_acceptable(self, threshold: float = 0.8) -> bool:
            return self._acceptable

    def check_quality(self, data: pd.DataFrame) -> _Result:
        self.calls.append(data)
        return self._Result(self.acceptable)


def _make_frame(days: int = 3) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=days, freq="D")
    return pd.DataFrame(
        {
            "open": [100 + i for i in range(days)],
            "high": [101 + i for i in range(days)],
            "low": [99 + i for i in range(days)],
            "close": [100.5 + i for i in range(days)],
            "volume": [1000 + 10 * i for i in range(days)],
        },
        index=index,
    )
