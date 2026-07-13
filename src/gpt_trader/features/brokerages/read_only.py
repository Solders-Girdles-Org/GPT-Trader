"""Read-only broker wrapper for dry-run safety."""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, cast

from gpt_trader.app.protocols import EventStoreProtocol
from gpt_trader.core import (
    Balance,
    Candle,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Product,
    Quote,
    TimeInForce,
)
from gpt_trader.core.protocols import BrokerProtocol
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="read_only_broker")


class ReadOnlyViolation(AttributeError):
    """Raised when dry-run code requests an undeclared broker capability."""


class ReadOnlyBroker(BrokerProtocol):
    """Prevent broker writes while allowing an explicit read/preview surface."""

    _blocked_methods = {
        "place_order",
        "cancel_order",
        "close_position",
        "edit_order",
    }
    _read_methods = frozenset(
        {
            "_mark_cache",
            "close",
            "get_cfm_balance_summary",
            "get_cfm_margin_window",
            "get_cfm_sweeps_schedule",
            "get_mark_price",
            "get_market_snapshot",
            "get_order",
            "get_portfolio_balances",
            "get_position",
            "get_position_pnl",
            "get_position_risk",
            "get_resilience_status",
            "get_ticker_freshness_provider",
            "get_tickers",
            "get_time",
            "get_ws_health",
            "list_cfm_sweeps",
            "list_fills",
            "list_orders",
            "list_products",
            "preview_order",
            "edit_order_preview",
            "stream_orderbook",
            "stream_trades",
        }
    )

    def __init__(
        self,
        broker: Any,
        event_store: EventStoreProtocol | None,
        *,
        bot_id: str | None,
        reason: str = "dry_run",
    ) -> None:
        self._broker = broker
        self._event_store = event_store
        self._bot_id = bot_id
        self._reason = reason

    def __getattr__(self, name: str) -> Any:
        if name in self._blocked_methods:
            return self._build_blocked_call(name)
        if name in self._read_methods:
            return getattr(self._broker, name)
        if name == "set_mark" and isinstance(self._broker, DeterministicBroker):
            return self._broker.set_mark

        raise ReadOnlyViolation(f"Read-only broker blocked undeclared capability: {name}")

    # --- BrokerProtocol read methods ---------------------------------------
    def get_product(self, symbol: str) -> Product | None:
        return cast(Product | None, self._broker.get_product(symbol))

    def get_quote(self, symbol: str) -> Quote | None:
        return cast(Quote | None, self._broker.get_quote(symbol))

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], self._broker.get_ticker(product_id))

    def list_positions(self) -> list[Position]:
        return cast(list[Position], self._broker.list_positions())

    def list_balances(self) -> list[Balance]:
        return cast(list[Balance], self._broker.list_balances())

    def get_candles(self, symbol: str, **kwargs: Any) -> list[Candle]:
        return cast(list[Candle], self._broker.get_candles(symbol, **kwargs))

    def _build_blocked_call(self, action: str) -> Any:
        def _blocked(*args: Any, **kwargs: Any) -> Any:
            payload = {
                "args": self._serialize_event_value(args),
                "kwargs": self._serialize_event_value(kwargs),
            }
            self._record_suppressed_event(action, payload)
            return None

        return _blocked

    def _serialize_event_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {str(key): self._serialize_event_value(val) for key, val in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._serialize_event_value(val) for val in value]
        return str(value)

    def _record_suppressed_event(self, action: str, payload: dict[str, Any]) -> None:
        if self._event_store is None:
            return
        data = {
            "action": action,
            "reason": self._reason,
            "timestamp": time.time(),
            "dry_run": True,
            **payload,
        }
        if self._bot_id and "bot_id" not in data:
            data["bot_id"] = self._bot_id
        try:
            self._event_store.append("order_suppressed", data)
        except Exception as exc:
            logger.warning(
                "Failed to record order_suppressed event",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="order_suppressed",
            )

    def place_order(
        self,
        symbol: str | dict[str, Any] | None = None,
        side: Any = None,
        order_type: Any = None,
        quantity: Decimal | None = None,
        **kwargs: Any,
    ) -> Order:
        symbol_value = None
        side_value = side
        order_type_value = order_type
        quantity_value = quantity
        if isinstance(symbol, dict):
            payload = symbol
            symbol_value = payload.get("product_id") or payload.get("symbol")
            side_value = payload.get("side", side)
            order_type_value = payload.get("order_type", order_type)
            quantity_value = payload.get("quantity", quantity)
        else:
            payload = None
            symbol_value = symbol

        client_id = kwargs.get("client_id") or kwargs.get("client_order_id")
        payload_data = {
            "symbol": symbol_value,
            "side": side_value,
            "order_type": order_type_value,
            "quantity": quantity_value,
            "price": kwargs.get("price"),
            "stop_price": kwargs.get("stop_price"),
            "tif": kwargs.get("tif"),
            "reduce_only": kwargs.get("reduce_only"),
            "leverage": kwargs.get("leverage"),
            "client_order_id": client_id,
        }
        if payload is not None:
            payload_data["request"] = payload
        payload_data = {
            key: self._serialize_event_value(val)
            for key, val in payload_data.items()
            if val is not None
        }
        self._record_suppressed_event("place_order", payload_data)

        side_enum = self._coerce_side(side_value)
        order_type_enum = self._coerce_order_type(order_type_value)
        quantity_decimal = self._coerce_decimal(quantity_value) or Decimal("0")
        order_id = f"DRYRUN_{client_id}" if client_id else f"DRYRUN_{time.time_ns()}"
        order = Order(
            id=order_id,
            client_id=client_id or order_id,
            symbol=str(symbol_value or ""),
            side=side_enum,
            type=order_type_enum,
            quantity=quantity_decimal,
            status=OrderStatus.REJECTED,
            filled_quantity=Decimal("0"),
            price=self._coerce_decimal(kwargs.get("price")),
            tif=self._coerce_tif(kwargs.get("tif")),
        )
        order.dry_run_suppressed = True  # type: ignore[attr-defined]
        order.suppressed_reason = self._reason  # type: ignore[attr-defined]
        return order

    def cancel_order(self, order_id: str) -> bool:
        self._record_suppressed_event("cancel_order", {"order_id": order_id})
        return False

    def close_position(
        self,
        symbol: str,
        client_order_id: str | None = None,
        fallback: Any | None = None,
    ) -> Any:
        self._record_suppressed_event(
            "close_position",
            {"symbol": symbol, "client_order_id": client_order_id},
        )
        return None

    def edit_order(self, order_id: str, preview_id: str, **kwargs: Any) -> Any:
        self._record_suppressed_event(
            "edit_order",
            {"order_id": order_id, "preview_id": preview_id, "kwargs": kwargs},
        )
        return None

    def _coerce_side(self, side: Any) -> OrderSide:
        if isinstance(side, OrderSide):
            return side
        if isinstance(side, str) and side.lower() == "sell":
            return OrderSide.SELL
        return OrderSide.BUY

    def _coerce_order_type(self, order_type: Any) -> OrderType:
        if isinstance(order_type, OrderType):
            return order_type
        if isinstance(order_type, str) and order_type.lower() == "limit":
            return OrderType.LIMIT
        return OrderType.MARKET

    def _coerce_decimal(self, value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _coerce_tif(self, value: Any) -> TimeInForce:
        if isinstance(value, TimeInForce):
            return value
        return TimeInForce.GTC


__all__ = ["ReadOnlyBroker", "ReadOnlyViolation"]
