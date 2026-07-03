"""Edge coverage for HistoricalDataManager quality gating behavior."""

from __future__ import annotations

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
from gpt_trader.features.data.quality import CandleQualityReport


def _make_manager(
    tmp_path: Path,
    *,
    validate_quality: bool,
    reject_on_quality_failure: bool,
) -> tuple[HistoricalDataManager, Mock]:
    fetcher = Mock(spec=CoinbaseHistoricalFetcher)
    fetcher.fetch_candles = AsyncMock()
    manager = HistoricalDataManager(
        fetcher=fetcher,
        cache_dir=tmp_path,
        validate_quality=validate_quality,
        reject_on_quality_failure=reject_on_quality_failure,
    )
    return manager, fetcher


def _make_report(is_acceptable: bool) -> CandleQualityReport:
    return CandleQualityReport(
        total_candles=2,
        gaps_detected=[],
        spikes_detected=[],
        volume_anomalies=[],
        overall_score=0.1,
        is_acceptable=is_acceptable,
    )


def _make_candles(start: datetime) -> list[Candle]:
    return [
        Candle(
            ts=start,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("10"),
        ),
        Candle(
            ts=start + timedelta(hours=1),
            open=Decimal("105"),
            high=Decimal("115"),
            low=Decimal("95"),
            close=Decimal("108"),
            volume=Decimal("12"),
        ),
    ]


@pytest.mark.asyncio
async def test_reject_on_quality_failure_returns_empty(tmp_path: Path) -> None:
    manager, fetcher = _make_manager(
        tmp_path, validate_quality=True, reject_on_quality_failure=True
    )
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(hours=2)
    fetcher.fetch_candles.return_value = _make_candles(start)
    report = _make_report(is_acceptable=False)
    manager._validate_candles = Mock(return_value=report)

    result = await manager.get_candles("BTC-USD", "ONE_HOUR", start, end)

    assert result == []
    assert manager._recent_quality_reports["BTC-USD:ONE_HOUR"] is report


@pytest.mark.asyncio
async def test_quality_failure_not_rejected_returns_candles(tmp_path: Path) -> None:
    manager, fetcher = _make_manager(
        tmp_path, validate_quality=True, reject_on_quality_failure=False
    )
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(hours=2)
    candles = _make_candles(start)
    fetcher.fetch_candles.return_value = candles
    report = _make_report(is_acceptable=False)
    manager._validate_candles = Mock(return_value=report)

    result = await manager.get_candles("BTC-USD", "ONE_HOUR", start, end)

    assert [c.ts for c in result] == [c.ts for c in candles]
    assert manager._recent_quality_reports["BTC-USD:ONE_HOUR"] is report


@pytest.mark.asyncio
async def test_no_candles_skips_quality(tmp_path: Path) -> None:
    manager, fetcher = _make_manager(
        tmp_path, validate_quality=True, reject_on_quality_failure=False
    )
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(hours=2)
    fetcher.fetch_candles.return_value = []
    manager._validate_candles = Mock()

    result = await manager.get_candles("BTC-USD", "ONE_HOUR", start, end)

    assert result == []
    manager._validate_candles.assert_not_called()
    assert manager._recent_quality_reports == {}
