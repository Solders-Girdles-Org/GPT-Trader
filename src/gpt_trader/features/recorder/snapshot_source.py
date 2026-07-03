"""Recorder-owned Coinbase snapshot production.

The recorder owns market-data acquisition: this module constructs the
read-only Coinbase REST transport (public market candles, no auth) and
produces ``MarketSnapshot`` artifacts through the builder. Consumers (the
``ideas`` CLI, the Stage-1 paper cycle) receive snapshots from here instead
of wiring clients ad hoc.
"""

from __future__ import annotations

from gpt_trader.features.recorder.snapshot_builder import (
    MarketSnapshotBuilder,
    MarketSnapshotBuildRequest,
)
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot

DEFAULT_COINBASE_BASE_URL = "https://api.coinbase.com"
DEFAULT_SNAPSHOT_SOURCE_LABEL = "coinbase:market-candles"


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
