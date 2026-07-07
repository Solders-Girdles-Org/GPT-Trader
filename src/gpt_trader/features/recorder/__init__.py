"""Recorder feature slice — market-data recording owned outside the engine.

Extraction of the recorder role from the accepted five-role composition
(docs/decisions/adopt-five-role-composition.md): observation must outlive
execution, so recording state is constructed by the composition root and
injected into the trading engine rather than owned by it. This slice holds
price-tick persistence/recovery, MarketSnapshot production over the
read-only candle transports (Coinbase market candles and Stooq daily
equity candles), and the standalone recording loop
behind ``gpt-trader record`` (issue #1158). The engine's WS telemetry
stream converges here only after strategy→proposer parity, per the
decision record.
"""

from gpt_trader.features.recorder.market_data_recorder import (
    MarketDataRecorder,
    MarketDataRecorderConfig,
    derive_recorder_bot_id,
)
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
    DEFAULT_EQUITIES_SNAPSHOT_SOURCE_LABEL,
    DEFAULT_SNAPSHOT_SOURCE_LABEL,
    DEFAULT_STOOQ_BASE_URL,
    build_coinbase_market_snapshot,
    build_equities_market_snapshot,
)

__all__ = [
    "DEFAULT_COINBASE_BASE_URL",
    "DEFAULT_EQUITIES_SNAPSHOT_SOURCE_LABEL",
    "DEFAULT_SNAPSHOT_SOURCE_LABEL",
    "DEFAULT_STOOQ_BASE_URL",
    "EVENT_PRICE_TICK",
    "HistoricalCandleSource",
    "MAX_PRICE_HISTORY",
    "MarketDataRecorder",
    "MarketDataRecorderConfig",
    "MarketSnapshotBuildRequest",
    "MarketSnapshotBuilder",
    "PriceTickStore",
    "build_coinbase_market_snapshot",
    "build_equities_market_snapshot",
    "canonical_granularity",
    "derive_recorder_bot_id",
    "granularity_duration",
]
