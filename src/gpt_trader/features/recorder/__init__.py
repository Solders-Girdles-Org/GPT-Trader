"""Recorder feature slice — market-data recording owned outside the engine.

Extraction of the recorder role from the accepted five-role composition
(docs/decisions/adopt-five-role-composition.md): observation must outlive
execution, so recording state is constructed by the composition root and
injected into the trading engine rather than owned by it. This slice holds
price-tick persistence/recovery and MarketSnapshot production over the
read-only Coinbase candle transport; WS tick ingestion and a standalone
``record`` entrypoint migrate here in later stages (issue #1158).
"""

from gpt_trader.features.recorder.price_tick_store import (
    EVENT_PRICE_TICK,
    MAX_PRICE_HISTORY,
    PriceTickStore,
)
from gpt_trader.features.recorder.snapshot_builder import (
    HistoricalCandleSource,
    MarketSnapshotBuilder,
    MarketSnapshotBuildRequest,
    canonical_granularity,
    granularity_duration,
)
from gpt_trader.features.recorder.snapshot_source import (
    DEFAULT_COINBASE_BASE_URL,
    DEFAULT_SNAPSHOT_SOURCE_LABEL,
    build_coinbase_market_snapshot,
)

__all__ = [
    "DEFAULT_COINBASE_BASE_URL",
    "DEFAULT_SNAPSHOT_SOURCE_LABEL",
    "EVENT_PRICE_TICK",
    "HistoricalCandleSource",
    "MAX_PRICE_HISTORY",
    "MarketSnapshotBuildRequest",
    "MarketSnapshotBuilder",
    "PriceTickStore",
    "build_coinbase_market_snapshot",
    "canonical_granularity",
    "granularity_duration",
]
