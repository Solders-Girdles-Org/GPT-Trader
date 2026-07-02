"""
Liquidation buffer guard - monitors position proximity to liquidation price.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from gpt_trader.features.live_trade.execution.guards.protocol import RuntimeGuardState
from gpt_trader.features.live_trade.guard_errors import (
    RiskGuardDataCorrupt,
    RiskGuardDataUnavailable,
)
from gpt_trader.utilities.quantities import quantity_from

if TYPE_CHECKING:
    from gpt_trader.core.protocols import BrokerProtocol
    from gpt_trader.features.live_trade.risk.protocols import RiskManagerProtocol


class LiquidationBufferGuard:
    """
    Guard that monitors liquidation price buffers for perpetual positions.

    In full mode, fetches detailed position risk data from the broker.
    In incremental mode, uses cached position data.
    """

    def __init__(
        self,
        broker: BrokerProtocol,
        risk_manager: RiskManagerProtocol,
    ) -> None:
        """
        Initialize liquidation buffer guard.

        Args:
            broker: Broker for position risk queries
            risk_manager: Risk manager for buffer calculations
        """
        self._broker = broker
        self._risk_manager = risk_manager

    @property
    def name(self) -> str:
        return "liquidation_buffer"

    def check(self, state: RuntimeGuardState, incremental: bool = False) -> None:
        """Check liquidation price buffers for all positions."""
        for pos in state.positions:
            try:
                position_quantity = quantity_from(pos) or Decimal("0")
                mark = Decimal(str(getattr(pos, "mark_price", "0")))
            except Exception as exc:
                raise RiskGuardDataCorrupt(
                    guard_name=self.name,
                    message="Position payload missing numeric fields",
                    details={"symbol": getattr(pos, "symbol", "unknown")},
                ) from exc

            pos_data: dict[str, Any] = {
                "quantity": position_quantity,
                "mark": mark,
            }

            if not incremental and hasattr(self._broker, "get_position_risk"):
                try:
                    risk_info = self._broker.get_position_risk(pos.symbol)
                except Exception as exc:
                    raise RiskGuardDataUnavailable(
                        guard_name=self.name,
                        message="Failed to fetch position risk from broker",
                        details={"symbol": pos.symbol},
                    ) from exc
                if isinstance(risk_info, dict) and "liquidation_price" in risk_info:
                    pos_data["liquidation_price"] = risk_info["liquidation_price"]

            self._risk_manager.check_liquidation_buffer(pos.symbol, pos_data, state.equity)


__all__ = ["LiquidationBufferGuard"]
