"""Shared helpers for OrdersStore unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from gpt_trader.persistence.orders_store import OrderRecord, OrderStatus


def create_test_order(
    order_id: str = "test-order-1",
    symbol: str = "BTC-USD",
    side: str = "buy",
    quantity: Decimal = Decimal("1.5"),
    status: OrderStatus = OrderStatus.PENDING,
    **kwargs,
) -> OrderRecord:
    """Create a test order with defaults."""
    now = datetime.now(UTC)
    return OrderRecord(
        order_id=order_id,
        client_order_id=kwargs.get("client_order_id", f"client-{order_id}"),
        symbol=symbol,
        side=side,
        order_type=kwargs.get("order_type", "market"),
        quantity=quantity,
        price=kwargs.get("price"),
        status=status,
        filled_quantity=kwargs.get("filled_quantity", Decimal("0")),
        average_fill_price=kwargs.get("average_fill_price"),
        created_at=kwargs.get("created_at", now),
        updated_at=kwargs.get("updated_at", now),
        bot_id=kwargs.get("bot_id", "test-bot"),
        time_in_force=kwargs.get("time_in_force", "GTC"),
        metadata=kwargs.get("metadata"),
    )
