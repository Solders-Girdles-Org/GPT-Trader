"""Recorder-owned snapshot production entry points.

The recorder owns market-data acquisition: this module constructs the
read-only candle transports (public data, no auth) and produces
``MarketSnapshot`` artifacts through the builder. Consumers (the ``ideas``
CLI, the Stage-1 paper cycle) receive snapshots from here instead of wiring
clients ad hoc. Two venues are served: Coinbase market candles and Stooq
daily equity candles (``equities_candles.py``, which documents the
session-close-in-UTC timestamp convention).
"""

from __future__ import annotations

from gpt_trader.features.recorder.equities_candles import DEFAULT_STOOQ_BASE_URL
from gpt_trader.features.recorder.snapshot_builder import (
    MarketSnapshotBuilder,
    MarketSnapshotBuildRequest,
)
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot

DEFAULT_COINBASE_BASE_URL = "https://api.coinbase.com"
DEFAULT_SNAPSHOT_SOURCE_LABEL = "coinbase:market-candles"
DEFAULT_EQUITIES_SNAPSHOT_SOURCE_LABEL = "stooq:market-candles"


async def build_coinbase_market_snapshot(
    request: MarketSnapshotBuildRequest,
    *,
    base_url: str = DEFAULT_COINBASE_BASE_URL,
    source_label: str = DEFAULT_SNAPSHOT_SOURCE_LABEL,
) -> MarketSnapshot:
    """Fetch read-only public Coinbase candles and build one snapshot.

    Constructs an unauthenticated client per call and closes it afterwards;
    the request is point-in-time, so no connection state is worth keeping.
    """
    from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient
    from gpt_trader.features.brokerages.coinbase.historical_candles import (
        CoinbaseHistoricalFetcher,
    )

    client = CoinbaseClient(
        base_url=base_url,
        auth=None,
        api_mode="advanced",
    )
    try:
        builder = MarketSnapshotBuilder(
            CoinbaseHistoricalFetcher(client=client),
            source_label=source_label,
        )
        return await builder.build(request)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


async def build_equities_market_snapshot(
    request: MarketSnapshotBuildRequest,
    *,
    base_url: str = DEFAULT_STOOQ_BASE_URL,
    source_label: str = DEFAULT_EQUITIES_SNAPSHOT_SOURCE_LABEL,
) -> MarketSnapshot:
    """Fetch keyless daily Stooq equity candles and build one snapshot.

    ONE_DAY granularity only: the underlying source rejects intraday
    requests loudly instead of fabricating bars. Symbols are plain tickers
    (``AAPL``, ``SPY``); the Stooq symbol form stays inside the source. The
    transport is one unauthenticated GET per symbol, nothing to close.
    """
    from gpt_trader.features.recorder.equities_candles import StooqDailyCandleFetcher

    builder = MarketSnapshotBuilder(
        StooqDailyCandleFetcher(base_url=base_url),
        source_label=source_label,
    )
    return await builder.build(request)
