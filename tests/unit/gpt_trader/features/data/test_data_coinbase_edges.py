from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pandas as pd

from gpt_trader.core import Candle
from gpt_trader.features.data.data import (
    DataService,
    _candles_to_dataframe,
    _interval_to_granularity,
)
from gpt_trader.features.data.quality import DataQualityChecker


def test_interval_to_granularity_maps_known_and_default() -> None:
    assert _interval_to_granularity("1m") == "ONE_MINUTE"
    assert _interval_to_granularity("1h") == "ONE_HOUR"
    assert _interval_to_granularity("unknown") == "ONE_HOUR"


def test_candles_to_dataframe_empty_returns_empty_frame() -> None:
    frame = _candles_to_dataframe([])

    assert isinstance(frame, pd.DataFrame)
    assert frame.empty


def test_download_from_coinbase_returns_none_without_manager() -> None:
    service = DataService(coinbase_manager=None)

    result = service.download_from_coinbase(
        ["BTC-USD"], datetime.now(UTC), datetime.now(UTC), interval="1h"
    )

    assert result is None


def test_download_from_coinbase_uses_manager_and_returns_dataframe() -> None:
    class StubManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, datetime, datetime]] = []

        async def get_candles(
            self, *, symbol: str, granularity: str, start: datetime, end: datetime
        ) -> list[Candle]:
            self.calls.append((symbol, granularity, start, end))
            ts1 = datetime(2024, 1, 1, tzinfo=UTC)
            ts2 = datetime(2024, 1, 1, 1, tzinfo=UTC)
            return [
                Candle(
                    ts=ts1,
                    open=Decimal("100"),
                    high=Decimal("110"),
                    low=Decimal("90"),
                    close=Decimal("105"),
                    volume=Decimal("10"),
                ),
                Candle(
                    ts=ts2,
                    open=Decimal("105"),
                    high=Decimal("115"),
                    low=Decimal("95"),
                    close=Decimal("108"),
                    volume=Decimal("12"),
                ),
            ]

    manager = StubManager()
    service = DataService(coinbase_manager=manager)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 2, tzinfo=UTC)

    result = service.download_from_coinbase(["BTC-USD"], start, end, interval="1h")

    assert result is not None
    assert manager.calls == [("BTC-USD", "ONE_HOUR", start, end)]

    frame = result["BTC-USD"]
    assert list(frame.index) == [
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 1, tzinfo=UTC),
    ]
    assert frame.loc[frame.index[0], "open"] == 100.0
    assert frame.loc[frame.index[1], "close"] == 108.0


def test_from_coinbase_client_wires_manager(monkeypatch) -> None:
    sentinel_manager = Mock()
    calls: dict[str, object] = {}

    def _fake_provider(*, client: object, cache_dir: object, **kwargs: object) -> object:
        calls["client"] = client
        calls["cache_dir"] = cache_dir
        calls["kwargs"] = kwargs
        return sentinel_manager

    monkeypatch.setattr(
        "gpt_trader.backtesting.data.create_coinbase_data_provider",
        _fake_provider,
    )

    client = Mock()
    service = DataService.from_coinbase_client(coinbase_client=client, cache_dir="tmp")

    assert calls["client"] is client
    assert calls["cache_dir"] == "tmp"
    assert calls["kwargs"]["validate_quality"] is False
    assert service._coinbase_manager is sentinel_manager


def test_download_from_coinbase_records_quality_report_for_empty_candles() -> None:
    class EmptyManager:
        async def get_candles(
            self, *, symbol: str, granularity: str, start: datetime, end: datetime
        ) -> list[Candle]:
            return []

    service = DataService(
        coinbase_manager=EmptyManager(),
        quality_checker=DataQualityChecker(),
        quality_checks_enabled=True,
    )
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 2, tzinfo=UTC)

    result = service.download_from_coinbase(["BTC-USD"], start, end, interval="1h")

    assert result is not None
    assert result["BTC-USD"].empty

    report = service.get_quality_report("BTC-USD", "1h")
    assert report is not None
    assert report.total_candles == 0
    assert report.overall_score == 0.0
    assert report.is_acceptable is False
