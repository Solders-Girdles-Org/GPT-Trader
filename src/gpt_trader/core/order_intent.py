"""Immutable admitted order contract shared by paper and direct broker dispatch.

An intent describes an already-admitted operation; constructing one grants no
trading authority. Reductions preserve side, quantity and reduce-only exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from gpt_trader.core.trading import OrderSide, OrderType


@dataclass(frozen=True, slots=True)
class OrderIntent:
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    tif: str | None = None
    reduce_only: bool = False
    leverage: int | None = None

    def __post_init__(self) -> None:
        if not self.client_order_id.strip() or not self.symbol.strip():
            raise ValueError("Order intent requires stable client identity and symbol")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("Order intent quantity must be finite and positive")
        if not isinstance(self.side, OrderSide) or not isinstance(self.order_type, OrderType):
            raise ValueError("Order intent requires explicit side and order type")
        if not isinstance(self.reduce_only, bool):
            raise ValueError("Order intent reduce_only must be boolean")
        for value in (self.price, self.stop_price):
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError("Order intent prices must be finite and positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side.value.lower(),
            "order_type": self.order_type.value.lower(),
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price is not None else None,
            "stop_price": str(self.stop_price) if self.stop_price is not None else None,
            "tif": self.tif,
            "reduce_only": self.reduce_only,
            "leverage": self.leverage,
        }

    def broker_kwargs(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "price": self.price,
            "stop_price": self.stop_price,
            "tif": self.tif,
            "reduce_only": self.reduce_only,
            "leverage": self.leverage,
            "client_id": self.client_order_id,
        }

    def validate_receipt(self, order: Any) -> None:
        """Validate explicit observed fields without inventing absent broker facts."""
        if order is None:
            return
        expected = {
            "client_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "type": self.order_type,
            "quantity": self.quantity,
            "reduce_only": self.reduce_only,
        }
        for field, value in expected.items():
            aliases = {"client_id": "client_order_id", "type": "order_type"}
            observed = getattr(order, field, getattr(order, aliases.get(field, field), None))
            if observed is None or (field == "client_id" and not observed):
                continue  # Older adapters may omit fields; never invent observations.
            if field in {"side", "type"}:
                observed = str(getattr(observed, "value", observed)).upper()
                value = getattr(value, "value", value)
            if observed != value:
                raise ValueError(f"Broker receipt {field} conflicts with intent")
        try:
            filled_raw = getattr(order, "filled_quantity", None)
            filled = Decimal(str(filled_raw)) if filled_raw is not None else None
            status = getattr(order, "status", None)
            is_filled = str(getattr(status, "value", status)).upper() == "FILLED"
            if filled is not None and (
                not filled.is_finite()
                or filled < 0
                or filled > self.quantity
                or (is_filled and filled != self.quantity)
            ):
                raise ValueError("Broker receipt fill quantity conflicts with intent")
            fill_price_raw = getattr(
                order, "avg_fill_price", getattr(order, "average_fill_price", None)
            )
            if fill_price_raw is not None:
                fill_price = Decimal(str(fill_price_raw))
                if not fill_price.is_finite() or fill_price <= 0:
                    raise ValueError("Broker receipt fill price must be finite and positive")
        except (TypeError, ValueError, ArithmeticError) as error:
            raise ValueError("Broker receipt contains malformed fill values") from error

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OrderIntent:
        return cls(
            client_order_id=payload["client_order_id"],
            symbol=payload["symbol"],
            side=OrderSide(payload["side"].upper()),
            order_type=OrderType(payload["order_type"].upper()),
            quantity=Decimal(payload["quantity"]),
            price=Decimal(payload["price"]) if payload.get("price") is not None else None,
            stop_price=(
                Decimal(payload["stop_price"]) if payload.get("stop_price") is not None else None
            ),
            tif=payload.get("tif"),
            reduce_only=payload.get("reduce_only", False),
            leverage=payload.get("leverage"),
        )
