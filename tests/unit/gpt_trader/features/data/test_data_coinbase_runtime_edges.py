from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.core import Candle
from gpt_trader.features.data.data import DataService


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


@pytest.mark.asyncio
async def test_download_from_coinbase_running_loop() -> None:
    manager = StubManager()
    service = DataService(coinbase_manager=manager)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 2, tzinfo=UTC)

    result = service.download_from_coinbase(["BTC-USD"], start, end, interval="1h")

    assert manager.calls == [("BTC-USD", "ONE_HOUR", start, end)]
    frame = result["BTC-USD"]
    assert list(frame.index) == [
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 1, tzinfo=UTC),
    ]
    assert frame.loc[frame.index[0], "open"] == 100.0
    assert frame.loc[frame.index[1], "close"] == 108.0


def test_download_from_coinbase_uses_asyncio_run(monkeypatch) -> None:
    manager = StubManager()
    service = DataService(coinbase_manager=manager)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 2, tzinfo=UTC)

    def _no_loop() -> None:
        raise RuntimeError("no loop")

    monkeypatch.setattr("gpt_trader.features.data.data.asyncio.get_event_loop", _no_loop)

    result = service.download_from_coinbase(["BTC-USD"], start, end, interval="1h")

    assert manager.calls == [("BTC-USD", "ONE_HOUR", start, end)]
    frame = result["BTC-USD"]
    assert frame.loc[frame.index[0], "open"] == 100.0
