"""Capture public Coinbase hourly candles with their raw responses retained.

Read-only and unauthenticated, like the snapshot source. The capture keeps
every request and raw response verbatim with the fetch time, so a recorded
experiment input can be rebuilt from it and checked against its SHA-256.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from gpt_trader.features.recorder.snapshot_source import DEFAULT_COINBASE_BASE_URL

CAPTURE_SCHEMA = "coinbase-public-candles-capture/1"


async def capture_public_hourly_candles(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    client: Any = None,
    base_url: str = DEFAULT_COINBASE_BASE_URL,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bytes:
    """Return the canonical capture bytes; callers write them verbatim."""
    from gpt_trader.features.brokerages.coinbase.historical_candles import (
        CoinbaseHistoricalFetcher,
    )

    owned = client is None
    if owned:
        from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient

        client = CoinbaseClient(base_url=base_url, auth=None, api_mode="advanced")
    requests: list[dict[str, Any]] = []
    try:
        fetcher = CoinbaseHistoricalFetcher(client=client, response_sink=requests.append)
        await fetcher.fetch_candles(symbol, "ONE_HOUR", start, end)
        fetched_at = clock()
    finally:
        if owned:
            client.close()
    capture = {
        "schema": CAPTURE_SCHEMA,
        "symbol": symbol,
        "granularity": "ONE_HOUR",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "requests": requests,
    }
    return json.dumps(capture, sort_keys=True, indent=1).encode() + b"\n"
