"""Raw-response capture over the shared fetcher, with an injected client."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from gpt_trader.features.recorder.candle_capture import capture_public_hourly_candles


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_candles(self, symbol, granularity, limit, *, start, end):
        self.calls.append((symbol, granularity, start, end))
        return {
            "candles": [
                {
                    "start": str(int(start.timestamp())),
                    "low": "1",
                    "high": "3",
                    "open": "2",
                    "close": "2",
                    "volume": "9",
                }
            ]
        }


@pytest.mark.asyncio
async def test_capture_retains_each_request_and_raw_response_verbatim():
    client = FakeClient()
    start = datetime(2026, 9, 3, tzinfo=UTC)
    data = await capture_public_hourly_candles(
        "BTC-USD",
        start,
        start + timedelta(hours=400),
        client=client,
        clock=lambda: datetime(2026, 10, 12, tzinfo=UTC),
    )
    capture = json.loads(data)
    assert len(client.calls) == 2  # 300-candle chunks
    assert capture["fetched_at"] == "2026-10-12T00:00:00+00:00"
    assert [request["start"] for request in capture["requests"]] == [
        start.isoformat(),
        (start + timedelta(hours=300)).isoformat(),
    ]
    assert capture["requests"][0]["response"]["candles"][0]["volume"] == "9"
    assert data == json.dumps(capture, sort_keys=True, indent=1).encode() + b"\n"
