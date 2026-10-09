from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.persistence.durability import WriteError
from gpt_trader.persistence.orders_store import OrderRecord, OrdersStore, OrderStatus
from tests.unit.gpt_trader.persistence.orders_store_test_helpers import create_test_order


def _make_order(
    *,
    order_id: str = "order-1",
    symbol: str = "BTC-USD",
    status: OrderStatus = OrderStatus.PENDING,
    filled_quantity: Decimal | None = None,
    average_fill_price: Decimal | None = None,
) -> OrderRecord:
    now = datetime.now(UTC)
    return OrderRecord(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        symbol=symbol,
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        price=None,
        status=status,
        filled_quantity=filled_quantity if filled_quantity is not None else Decimal("0"),
        average_fill_price=average_fill_price,
        created_at=now,
        updated_at=now,
        bot_id="bot-1",
        time_in_force="GTC",
        metadata=None,
    )


def test_get_pending_orders_includes_partially_filled(tmp_path: Path) -> None:
    with OrdersStore(tmp_path) as store:
        store.save_order(_make_order(order_id="partial", status=OrderStatus.PARTIALLY_FILLED))

        order_ids = [order.order_id for order in store.get_pending_orders()]

    assert "partial" in order_ids


def test_get_orders_by_symbol_include_terminal(tmp_path: Path) -> None:
    with OrdersStore(tmp_path) as store:
        store.save_order(_make_order(order_id="open-1", status=OrderStatus.OPEN))
        store.save_order(_make_order(order_id="filled-1", status=OrderStatus.FILLED))

        default_ids = [order.order_id for order in store.get_orders_by_symbol("BTC-USD")]
        all_ids = [
            order.order_id for order in store.get_orders_by_symbol("BTC-USD", include_terminal=True)
        ]

    assert "filled-1" not in default_ids
    assert "filled-1" in all_ids


def test_update_status_status_only_preserves_fill_fields(tmp_path: Path) -> None:
    with OrdersStore(tmp_path) as store:
        order = _make_order(
            order_id="order-2",
            status=OrderStatus.PENDING,
            filled_quantity=Decimal("0.5"),
            average_fill_price=Decimal("99"),
        )
        store.save_order(order)

        result = store.update_status(order.order_id, OrderStatus.OPEN)
        updated = store.get_order(order.order_id)

    assert result.success is True
    assert updated is not None
    assert updated.status == OrderStatus.OPEN
    assert updated.filled_quantity == Decimal("0.5")
    assert updated.average_fill_price == Decimal("99")


def test_update_status_filled_quantity_only(tmp_path: Path) -> None:
    with OrdersStore(tmp_path) as store:
        order = _make_order(order_id="order-3", status=OrderStatus.OPEN)
        store.save_order(order)

        result = store.update_status(
            order.order_id,
            OrderStatus.PARTIALLY_FILLED,
            filled_quantity=Decimal("0.25"),
        )
        updated = store.get_order(order.order_id)

    assert result.success is True
    assert updated is not None
    assert updated.status == OrderStatus.PARTIALLY_FILLED
    assert updated.filled_quantity == Decimal("0.25")
    assert updated.average_fill_price is None


def test_verify_integrity_detects_checksum_mismatch(tmp_path: Path) -> None:
    with OrdersStore(tmp_path) as store:
        order = _make_order(order_id="order-4")
        store.save_order(order)

        connection = store._get_connection()
        connection.execute(
            "UPDATE orders SET checksum = ? WHERE order_id = ?",
            ("bad", order.order_id),
        )

        valid_count, invalid_orders = store.verify_integrity()

    assert valid_count == 0
    assert order.order_id in invalid_orders


def test_cleanup_old_orders_deletes_terminal(tmp_path: Path) -> None:
    with OrdersStore(tmp_path) as store:
        order = _make_order(order_id="order-5", status=OrderStatus.FILLED)
        store.save_order(order)

        connection = store._get_connection()
        connection.execute(
            "UPDATE orders SET updated_at = ? WHERE order_id = ?",
            ("2000-01-01 00:00:00", order.order_id),
        )

        deleted = store.cleanup_old_orders(days=1)

    assert deleted == 1


def test_save_order_sqlite_error_returns_fail(tmp_path: Path, monkeypatch) -> None:
    store = OrdersStore(tmp_path)
    store.initialize()

    class StubConnection:
        def execute(self, *args, **kwargs):
            raise sqlite3.Error("boom")

    monkeypatch.setattr(store, "_get_connection", lambda: StubConnection())

    result = store.save_order(_make_order(order_id="order-6"))
    assert result.success is False
    assert result.error and "Failed to save order" in result.error


def test_save_order_sqlite_error_raises(tmp_path: Path, monkeypatch) -> None:
    store = OrdersStore(tmp_path)
    store.initialize()

    class StubConnection:
        def execute(self, *args, **kwargs):
            raise sqlite3.Error("boom")

    monkeypatch.setattr(store, "_get_connection", lambda: StubConnection())

    with pytest.raises(WriteError):
        store.save_order(_make_order(order_id="order-7"), raise_on_error=True)


class TestOrderRecord:
    """Tests for OrderRecord dataclass."""

    def test_to_dict_and_from_dict(self) -> None:
        order = create_test_order(
            price=Decimal("50000"),
            metadata={"source": "test"},
        )

        order_dict = order.to_dict()
        restored = OrderRecord.from_dict(order_dict)

        assert restored.order_id == order.order_id
        assert restored.symbol == order.symbol
        assert restored.quantity == order.quantity
        assert restored.price == order.price
        assert restored.metadata == order.metadata

    def test_compute_checksum_deterministic(self) -> None:
        order = create_test_order()
        checksum1 = order.compute_checksum()
        checksum2 = order.compute_checksum()
        assert checksum1 == checksum2

    def test_compute_checksum_changes_with_critical_fields(self) -> None:
        order1 = create_test_order(quantity=Decimal("1.0"))
        order2 = create_test_order(quantity=Decimal("2.0"))
        assert order1.compute_checksum() != order2.compute_checksum()

    def test_is_terminal_pending(self) -> None:
        order = create_test_order(status=OrderStatus.PENDING)
        assert order.is_terminal() is False

    def test_is_terminal_open(self) -> None:
        order = create_test_order(status=OrderStatus.OPEN)
        assert order.is_terminal() is False

    def test_is_terminal_filled(self) -> None:
        order = create_test_order(status=OrderStatus.FILLED)
        assert order.is_terminal() is True

    def test_is_terminal_cancelled(self) -> None:
        order = create_test_order(status=OrderStatus.CANCELLED)
        assert order.is_terminal() is True


class TestOrderStatus:
    """Tests for OrderStatus enum."""

    def test_status_values(self) -> None:
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.CANCELLED.value == "cancelled"

    def test_status_from_string(self) -> None:
        status = OrderStatus("pending")
        assert status == OrderStatus.PENDING
