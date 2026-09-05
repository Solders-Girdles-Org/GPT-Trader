"""
Protocol definitions for runtime guards.

Guards implement a common interface for evaluating account safety conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from gpt_trader.core import Balance


@dataclass
class RuntimeGuardState:
    """Cached snapshot of account risk state used by runtime guards."""

    timestamp: float
    balances: list[Balance]
    equity: Decimal
    positions: list[Any]
    positions_pnl: dict[str, dict[str, Decimal]]
    positions_dict: dict[str, dict[str, Decimal]]
    guard_events: list[dict[str, Any]] = field(default_factory=list)
    pnl_availability: dict[str, dict[str, Any]] = field(default_factory=dict)


@runtime_checkable
class Guard(Protocol):
    """Protocol for runtime safety guards."""

    @property
    def name(self) -> str:
        """Unique identifier for this guard."""
        ...

    def check(self, state: RuntimeGuardState, incremental: bool = False) -> None:
        """
        Execute guard logic.

        Args:
            state: Current account state snapshot
            incremental: Whether this is an incremental check (may skip expensive operations)

        Raises:
            GuardError: On guard failure (recoverable or non-recoverable)
        """
        ...


__all__ = ["Guard", "RuntimeGuardState"]
