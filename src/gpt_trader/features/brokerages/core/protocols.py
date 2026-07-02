"""Transitional re-export shim; the canonical home is ``gpt_trader.core.protocols``.

Moved 2026-07-02 so lower layers (monitoring, execution) can depend on broker
protocols without importing a feature slice. New code should import from
``gpt_trader.core.protocols`` (or ``gpt_trader.core``); this shim exists only
for not-yet-migrated callers and will be removed once none remain.
"""

from __future__ import annotations

from gpt_trader.core.protocols import (
    BrokerProtocol,
    ExtendedBrokerProtocol,
    MarketDataProtocol,
    TickerFreshnessProvider,
    TickerFreshnessProviderSource,
)

__all__ = [
    "BrokerProtocol",
    "ExtendedBrokerProtocol",
    "MarketDataProtocol",
    "TickerFreshnessProvider",
    "TickerFreshnessProviderSource",
]
