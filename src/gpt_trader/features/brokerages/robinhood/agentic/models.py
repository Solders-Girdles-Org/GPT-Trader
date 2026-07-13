"""Immutable provider-specific Agentic review records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from gpt_trader.core import OrderSide
from gpt_trader.features.brokerages.accounts import PreviewResult


@dataclass(frozen=True, slots=True)
class RobinhoodAgenticEquityReviewEvidence:
    preview: PreviewResult
    order_checks_json: str
    quote_json: str
    market_data_disclosure: str


@dataclass(frozen=True, slots=True)
class RobinhoodAgenticOptionReviewRequest:
    option_id: str
    side: OrderSide
    position_effect: str
    quantity: int
    order_type: str = "limit"
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "gfd"
    market_hours: str = "regular_hours"
    chain_symbol: str | None = None
    underlying_type: str | None = None

    def __post_init__(self) -> None:
        if not self.option_id.strip():
            raise ValueError("option_id is required")
        side = self.side if isinstance(self.side, OrderSide) else OrderSide(str(self.side).upper())
        if self.position_effect not in {"open", "close"}:
            raise ValueError("position_effect must be open or close")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type not in {"limit", "market", "stop_limit", "stop_market"}:
            raise ValueError("unsupported option review order type")
        if self.time_in_force not in {"gfd", "gtc"}:
            raise ValueError("unsupported option review time in force")
        if self.market_hours not in {
            "regular_hours",
            "regular_curb_hours",
            "regular_curb_overnight_hours",
        }:
            raise ValueError("unsupported option review market hours")
        for name, value in (("price", self.price), ("stop_price", self.stop_price)):
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError(f"{name} must be positive and finite")
        if self.order_type in {"limit", "stop_limit"} and self.price is None:
            raise ValueError("price is required for limit option reviews")
        if self.order_type in {"market", "stop_market"} and self.price is not None:
            raise ValueError("price is not accepted for market option reviews")
        if self.order_type in {"stop_limit", "stop_market"} and self.stop_price is None:
            raise ValueError("stop_price is required for stop option reviews")
        if self.order_type in {"limit", "market"} and self.stop_price is not None:
            raise ValueError("stop_price is not accepted for non-stop option reviews")
        if (self.chain_symbol is None) is not (self.underlying_type is None):
            raise ValueError("chain_symbol and underlying_type must be supplied together")
        if self.underlying_type not in {None, "equity", "index"}:
            raise ValueError("underlying_type must be equity or index")
        object.__setattr__(self, "option_id", self.option_id.strip())
        object.__setattr__(self, "side", side)
        if self.chain_symbol is not None:
            object.__setattr__(self, "chain_symbol", self.chain_symbol.strip().upper())


@dataclass(frozen=True, slots=True)
class RobinhoodAgenticOptionReviewEvidence:
    request: RobinhoodAgenticOptionReviewRequest
    generated_at: datetime
    identity_fingerprint: str
    order_checks_json: str
    quotes_json: str
    total_fee: Decimal | None
    collateral_json: str
    errors: tuple[str, ...] = ()

    @property
    def non_binding(self) -> bool:
        return True


__all__ = [
    "RobinhoodAgenticEquityReviewEvidence",
    "RobinhoodAgenticOptionReviewEvidence",
    "RobinhoodAgenticOptionReviewRequest",
]
