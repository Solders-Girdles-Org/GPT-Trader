"""
Volatility circuit breaker guard - detects excessive volatility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gpt_trader.config.constants import (
    DEFAULT_VOLATILITY_WINDOW_PERIODS,
    MIN_VOLATILITY_WINDOW_THRESHOLD,
)
from gpt_trader.features.live_trade.execution.guards.protocol import RuntimeGuardState
from gpt_trader.features.live_trade.guard_errors import RiskGuardDataUnavailable

if TYPE_CHECKING:
    from gpt_trader.core.protocols import BrokerProtocol
    from gpt_trader.features.live_trade.risk.protocols import RiskManagerProtocol


class VolatilityGuard:
    """
    Guard that monitors volatility and triggers circuit breakers.

    Fetches historical candle data and evaluates volatility against
    configured thresholds. Records triggered events in state.
    """

    def __init__(
        self,
        broker: BrokerProtocol,
        risk_manager: RiskManagerProtocol,
    ) -> None:
        """
        Initialize volatility guard.

        Args:
            broker: Broker for candle data
            risk_manager: Risk manager for volatility calculations
        """
        self._broker = broker
        self._risk_manager = risk_manager

    @property
    def name(self) -> str:
        return "volatility_circuit_breaker"

    def check(self, state: RuntimeGuardState, incremental: bool = False) -> None:
        """Check volatility circuit breakers."""
        symbols: list[str] = list(self._risk_manager.last_mark_update.keys())
        symbols.extend([str(p) for p in state.positions_dict.keys() if p not in symbols])
        window = getattr(
            self._risk_manager.config,
            "volatility_window_periods",
            DEFAULT_VOLATILITY_WINDOW_PERIODS,
        )
        if not symbols or not window or window <= MIN_VOLATILITY_WINDOW_THRESHOLD:
            return

        failures: list[dict[str, Any]] = []
        for sym in symbols:
            if not hasattr(self._broker, "get_candles"):
                continue
            try:
                candles = self._broker.get_candles(sym, granularity="1m", limit=int(window))
            except Exception as exc:
                failures.append({"symbol": sym, "error": repr(exc)})
                continue
            closes = [c.close for c in candles if hasattr(c, "close")]
            if len(closes) >= window:
                outcome = self._risk_manager.check_volatility_circuit_breaker(sym, closes[-window:])
                if outcome.triggered:
                    state.guard_events.append(outcome.to_payload())

        if failures:
            raise RiskGuardDataUnavailable(
                guard_name=self.name,
                message="Failed to fetch candles for volatility guard",
                details={"failures": failures},
            )


__all__ = ["VolatilityGuard"]
