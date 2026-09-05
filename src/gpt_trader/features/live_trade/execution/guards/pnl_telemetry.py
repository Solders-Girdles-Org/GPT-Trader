"""
P&L telemetry guard - emits P&L data to monitoring system.
"""

from __future__ import annotations

from typing import Any

from gpt_trader.features.live_trade.execution.guards.protocol import Guard, RuntimeGuardState
from gpt_trader.features.live_trade.guard_errors import RiskGuardTelemetryError
from gpt_trader.monitoring.system import get_logger as _get_plog
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="pnl_telemetry")


class PnLTelemetryGuard:
    """
    Guard that logs P&L telemetry for all positions.

    Emits realized and unrealized P&L data to the system monitoring logger
    for observability and alerting.
    """

    @property
    def name(self) -> str:
        return "pnl_telemetry"

    def check(self, state: RuntimeGuardState, incremental: bool = False) -> None:
        """Log P&L telemetry for all positions."""
        plog = _get_plog()
        failures: list[dict[str, Any]] = []

        for symbol, availability in state.pnl_availability.items():
            logger.info("Local trade PnL evidence", symbol=symbol, **availability)

        for sym, pnl in state.positions_pnl.items():
            rp = pnl.get("realized_pnl")
            up = pnl.get("unrealized_pnl")
            rp_f = float(rp) if rp is not None else None
            up_f = float(up) if up is not None else None
            try:
                plog.log_pnl(symbol=sym, realized_pnl=rp_f, unrealized_pnl=up_f)
            except Exception as exc:
                failures.append({"symbol": sym, "error": repr(exc)})

        if failures:
            raise RiskGuardTelemetryError(
                guard_name=self.name,
                message="Failed to emit PnL telemetry for one or more symbols",
                details={"failures": failures},
            )


# Verify protocol compliance
_: Guard = PnLTelemetryGuard()

__all__ = ["PnLTelemetryGuard"]
