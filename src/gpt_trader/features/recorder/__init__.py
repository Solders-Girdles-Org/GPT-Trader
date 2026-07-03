"""Recorder feature slice — market-data recording owned outside the engine.

First extraction stage of the recorder role from the accepted five-role
composition (docs/decisions/adopt-five-role-composition.md): observation must
outlive execution, so recording state is constructed by the composition root
and injected into the trading engine rather than owned by it. This slice
currently holds price-tick persistence/recovery; WS/REST ingestion and
MarketSnapshot production migrate here in later stages (issue #1158).
"""

from gpt_trader.features.recorder.price_tick_store import (
    EVENT_PRICE_TICK,
    MAX_PRICE_HISTORY,
    PriceTickStore,
)

__all__ = [
    "EVENT_PRICE_TICK",
    "MAX_PRICE_HISTORY",
    "PriceTickStore",
]
