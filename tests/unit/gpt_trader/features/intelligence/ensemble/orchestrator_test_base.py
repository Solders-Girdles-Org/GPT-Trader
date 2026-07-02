"""Shared helpers for ensemble orchestrator tests."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from gpt_trader.core import Product
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision


class MockStrategy:
    """Mock strategy for testing."""

    def __init__(self, name: str, action: Action = Action.HOLD, confidence: float = 0.5):
        self.name = name
        self.action = action
        self.confidence = confidence
        self.call_count = 0

    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Product | None,
    ) -> Decision:
        """Return configured decision."""
        self.call_count += 1
        return Decision(
            action=self.action,
            reason=f"{self.name} signal",
            confidence=self.confidence,
            indicators={"strategy": self.name},
        )
