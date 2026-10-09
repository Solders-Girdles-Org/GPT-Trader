"""Pure mapping helpers: broker order payloads -> persisted ``OrderRecord``.

Extracted from ``TradingEngine`` (``engines/strategy.py``) as a focused,
independently testable collaborator. Every function here is pure — it depends
only on its arguments, holds no engine state, and performs no IO. The engine
keeps thin ``_`` -prefixed delegators so existing call sites are unchanged.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from gpt_trader.persistence.orders_store import OrderRecord
from gpt_trader.persistence.orders_store import OrderStatus as PersistedOrderStatus
from gpt_trader.utilities.datetime_helpers import parse_iso_to_epoch


def get_order_field(order: Any, *keys: str) -> Any:
    """Return the first non-None value among ``keys`` from a dict or object."""
    if isinstance(order, dict):
        for key in keys:
            if key in order and order[key] is not None:
                return order[key]
        return None
    for key in keys:
        if hasattr(order, key):
            value = getattr(order, key)
            if value is not None:
                return value
    return None


def normalize_persisted_status(status: Any) -> PersistedOrderStatus:
    """Map a broker/enum status onto the persisted ``OrderStatus`` vocabulary."""
    value = status.value if hasattr(status, "value") else status
    normalized = str(value).lower()
    mapping = {
        "pending": PersistedOrderStatus.PENDING,
        "submitted": PersistedOrderStatus.OPEN,
        "open": PersistedOrderStatus.OPEN,
        "partially_filled": PersistedOrderStatus.PARTIALLY_FILLED,
        "filled": PersistedOrderStatus.FILLED,
        "cancelled": PersistedOrderStatus.CANCELLED,
        "canceled": PersistedOrderStatus.CANCELLED,
        "rejected": PersistedOrderStatus.REJECTED,
        "expired": PersistedOrderStatus.EXPIRED,
        "failed": PersistedOrderStatus.FAILED,
    }
    return mapping.get(normalized, PersistedOrderStatus.OPEN)


def parse_decimal(value: Any, default: Decimal) -> Decimal:
    """Coerce ``value`` to a finite ``Decimal``, falling back to ``default``.

    Non-finite values (``NaN``/``Infinity``) are rejected — they must never be
    persisted as an order quantity/price.
    """
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default
    return result if result.is_finite() else default


def parse_decimal_optional(value: Any) -> Decimal | None:
    """Coerce ``value`` to a finite ``Decimal``, returning ``None`` otherwise."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def merge_metadata(
    base: dict[str, Any] | None,
    update: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Shallow-merge two optional metadata dicts (``update`` wins)."""
    if base is None and update is None:
        return None
    merged = dict(base or {})
    if update:
        merged.update(update)
    return merged


def parse_timestamp(value: Any) -> float:
    """Coerce a timestamp value (epoch/datetime/ISO string) to epoch seconds."""
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else time.time()
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            return parse_iso_to_epoch(value)
        except (ValueError, TypeError):
            return time.time()
    return time.time()


def build_record_from_broker_order(
    order: Any,
    *,
    bot_id: str,
    now: datetime,
) -> OrderRecord | None:
    """Build a persisted ``OrderRecord`` from a broker order payload.

    Returns ``None`` when the payload lacks an identifiable order id.
    """
    order_id = get_order_field(order, "order_id", "id")
    client_order_id = get_order_field(order, "client_order_id", "client_id") or order_id
    if order_id is None or client_order_id is None:
        return None
    symbol = get_order_field(order, "product_id", "symbol") or ""
    side_value = get_order_field(order, "side")
    side = str(side_value).lower() if side_value is not None else "unknown"
    order_type_value = get_order_field(order, "order_type", "type")
    order_type = str(order_type_value).lower() if order_type_value is not None else "unknown"
    quantity_value = get_order_field(order, "size", "quantity", "base_size")
    quantity = parse_decimal(quantity_value, Decimal("0"))
    price_value = get_order_field(order, "price")
    price = parse_decimal_optional(price_value)
    filled_value = get_order_field(order, "filled_size", "filled_quantity")
    filled_quantity = parse_decimal(filled_value, Decimal("0"))
    average_value = get_order_field(order, "average_filled_price", "avg_fill_price")
    average_fill_price = parse_decimal_optional(average_value)
    status_value = get_order_field(order, "status")
    status = normalize_persisted_status(status_value)
    created_value = get_order_field(order, "created_time", "created_at", "submitted_at", "created")
    created_ts = parse_timestamp(created_value)
    created_at = datetime.fromtimestamp(created_ts, tz=UTC)
    tif_value = get_order_field(order, "tif", "time_in_force")
    time_in_force = str(tif_value) if tif_value is not None else "GTC"
    metadata = {
        "source": "order_reconciliation",
        "raw_status": str(status_value or ""),
    }
    return OrderRecord(
        order_id=str(order_id),
        client_order_id=str(client_order_id),
        symbol=str(symbol),
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        status=status,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        created_at=created_at,
        updated_at=now,
        bot_id=bot_id,
        time_in_force=time_in_force,
        metadata=metadata,
    )
