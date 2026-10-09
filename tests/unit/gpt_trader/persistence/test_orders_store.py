"""Tests for OrdersStore persistence."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from gpt_trader.persistence import orders_store as orders_store_module
from gpt_trader.persistence.orders_store import OrdersStore, OrderStatus
from tests.unit.gpt_trader.persistence.orders_store_test_helpers import create_test_order


class TestOrdersStore:
    """Tests for OrdersStore persistence."""

    def test_initialize_creates_database(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = OrdersStore(tmpdir)
            store.initialize()

            db_path = Path(tmpdir) / "orders.db"
            assert db_path.exists()
            store.close()

    def test_explicit_close_allows_reinitialize_and_reuse(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = OrdersStore(tmpdir)
            store.initialize()
            store.close()

            order = create_test_order()
            result = store.save_order(order)

            assert result.success is True
            assert store.get_order(order.order_id) is not None
            store.close()

    def test_close_releases_thread_local_connections(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = OrdersStore(tmpdir)
            store.initialize()
            main_connection = store._get_connection()
            worker_connections: list[sqlite3.Connection] = []

            def save_in_worker() -> None:
                store.save_order(create_test_order(order_id="worker-order"))
                worker_connections.append(store._get_connection())

            worker = threading.Thread(target=save_in_worker)
            worker.start()
            worker.join()

            assert len(worker_connections) == 1

            store.close()

            for connection in [main_connection, *worker_connections]:
                with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                    connection.execute("SELECT 1")

    def test_worker_thread_connection_closes_when_thread_exits(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = OrdersStore(tmpdir)
            store.initialize()
            worker_connections: list[sqlite3.Connection] = []

            def save_in_worker() -> None:
                store.save_order(create_test_order(order_id="worker-exit-order"))
                worker_connections.append(store._get_connection())

            worker = threading.Thread(target=save_in_worker)
            worker.start()
            worker.join()

            assert len(worker_connections) == 1
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                worker_connections[0].execute("SELECT 1")

            store.close()

    def test_reconnect_in_same_generation_keeps_previous_handle_registered(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = OrdersStore(tmpdir)
            store.initialize()
            first_connection = store._get_connection()
            first_holder = store._local.connection_holder
            del store._local.connection_holder
            del store._local.connection_generation

            second_connection = store._get_connection()
            second_holder = store._local.connection_holder

            assert first_connection is not second_connection
            assert first_holder is not second_holder
            assert len(store._connections) == 2

            store.close()

            for connection in [first_connection, second_connection]:
                with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                    connection.execute("SELECT 1")

    def test_connection_generation_retry_releases_lock_before_reconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            store = OrdersStore(tmpdir)
            real_connect = sqlite3.connect
            connect_count = 0

            def connect_with_generation_change(
                *args: object, **kwargs: object
            ) -> sqlite3.Connection:
                nonlocal connect_count
                connect_count += 1
                connection = real_connect(*args, **kwargs)
                if connect_count == 1:
                    store.close()
                return connection

            monkeypatch.setattr(sqlite3, "connect", connect_with_generation_change)

            errors: list[BaseException] = []

            def save_with_generation_retry() -> None:
                try:
                    store.initialize()
                    store.save_order(create_test_order(order_id="retry-order"))
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=save_with_generation_retry, daemon=True)
            worker.start()
            worker.join(timeout=2)

            assert not worker.is_alive()
            if errors:
                raise errors[0]
            assert connect_count == 2
            assert store.get_order("retry-order") is not None
            store.close()

    def test_closed_holder_after_publish_retries_before_return(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_holder = orders_store_module._ConnectionHolder

        class ClosingOnceHolder(real_holder):
            closed_once = False

            @property
            def closed(self) -> bool:
                if not ClosingOnceHolder.closed_once:
                    ClosingOnceHolder.closed_once = True
                    self.close()
                return super().closed

        monkeypatch.setattr(orders_store_module, "_ConnectionHolder", ClosingOnceHolder)

        with TemporaryDirectory() as tmpdir:
            store = OrdersStore(tmpdir)
            store.initialize()
            result = store.save_order(create_test_order(order_id="publish-race-order"))

            assert ClosingOnceHolder.closed_once is True
            assert result.success is True
            assert store.get_order("publish-race-order") is not None
            store.close()

    def test_save_and_get_order(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                order = create_test_order()
                result = store.save_order(order)

                assert result.success is True
                assert result.checksum is not None

                retrieved = store.get_order(order.order_id)
                assert retrieved is not None
                assert retrieved.order_id == order.order_id
                assert retrieved.symbol == order.symbol
                assert retrieved.quantity == order.quantity

    def test_get_order_by_client_order_id(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                order = create_test_order(order_id="order-123", client_order_id="client-abc")
                store.save_order(order)

                retrieved = store.get_order_by_client_order_id("client-abc")
                assert retrieved is not None
                assert retrieved.order_id == "order-123"

                missing = store.get_order_by_client_order_id("missing-client")
                assert missing is None

    def test_get_order_not_found(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                result = store.get_order("nonexistent")
                assert result is None

    def test_save_order_upsert(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                order = create_test_order(status=OrderStatus.PENDING)
                store.save_order(order)

                order.status = OrderStatus.FILLED
                order.filled_quantity = order.quantity
                store.save_order(order)

                retrieved = store.get_order(order.order_id)
                assert retrieved.status == OrderStatus.FILLED
                assert retrieved.filled_quantity == order.quantity

    def test_upsert_by_client_id_updates_order_id(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                pending = create_test_order(
                    order_id="client-1",
                    client_order_id="client-1",
                    status=OrderStatus.PENDING,
                )
                store.upsert_by_client_id(pending)

                final = create_test_order(
                    order_id="order-123",
                    client_order_id="client-1",
                    status=OrderStatus.OPEN,
                )
                store.upsert_by_client_id(final)

                retrieved = store.get_order("order-123")
                assert retrieved is not None
                assert retrieved.client_order_id == "client-1"
                assert store.get_order("client-1") is None

    def test_get_pending_orders(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                pending = create_test_order(order_id="pending-1", status=OrderStatus.PENDING)
                open_order = create_test_order(order_id="open-1", status=OrderStatus.OPEN)
                filled = create_test_order(order_id="filled-1", status=OrderStatus.FILLED)
                cancelled = create_test_order(order_id="cancelled-1", status=OrderStatus.CANCELLED)

                for order in [pending, open_order, filled, cancelled]:
                    store.save_order(order)

                pending_orders = store.get_pending_orders()

                order_ids = [o.order_id for o in pending_orders]
                assert "pending-1" in order_ids
                assert "open-1" in order_ids
                assert "filled-1" not in order_ids
                assert "cancelled-1" not in order_ids

    def test_get_pending_orders_by_bot(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                order1 = create_test_order(order_id="order-1", bot_id="bot-a")
                order2 = create_test_order(order_id="order-2", bot_id="bot-b")

                store.save_order(order1)
                store.save_order(order2)

                bot_a_orders = store.get_pending_orders(bot_id="bot-a")
                assert len(bot_a_orders) == 1
                assert bot_a_orders[0].order_id == "order-1"

    def test_get_orders_by_symbol(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                btc_order = create_test_order(order_id="btc-1", symbol="BTC-USD")
                eth_order = create_test_order(order_id="eth-1", symbol="ETH-USD")

                store.save_order(btc_order)
                store.save_order(eth_order)

                btc_orders = store.get_orders_by_symbol("BTC-USD")
                assert len(btc_orders) == 1
                assert btc_orders[0].symbol == "BTC-USD"

    def test_list_orders_filters_and_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                base = datetime(2025, 1, 1, tzinfo=UTC)
                older = create_test_order(
                    order_id="old",
                    status=OrderStatus.OPEN,
                    symbol="BTC-USD",
                    created_at=base,
                    updated_at=base,
                )
                newer = create_test_order(
                    order_id="new",
                    status=OrderStatus.FILLED,
                    symbol="BTC-USD",
                    created_at=base + timedelta(days=1),
                    updated_at=base + timedelta(days=1),
                )
                other = create_test_order(
                    order_id="other",
                    status=OrderStatus.CANCELLED,
                    symbol="ETH-USD",
                    created_at=base + timedelta(days=2),
                    updated_at=base + timedelta(days=2),
                )

                for order in (older, newer, other):
                    store.save_order(order)

                recent = store.list_orders(limit=2)
                assert [record.order_id for record in recent] == ["other", "new"]

                filtered = store.list_orders(
                    limit=5,
                    symbol="BTC-USD",
                    status=OrderStatus.FILLED,
                )
                assert len(filtered) == 1
                assert filtered[0].order_id == "new"

    def test_list_orders_requires_positive_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                with pytest.raises(ValueError):
                    store.list_orders(limit=0)

    def test_update_status(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                order = create_test_order()
                store.save_order(order)

                result = store.update_status(
                    order.order_id,
                    OrderStatus.FILLED,
                    filled_quantity=order.quantity,
                    average_fill_price=Decimal("50000"),
                )

                assert result.success is True

                retrieved = store.get_order(order.order_id)
                assert retrieved.status == OrderStatus.FILLED
                assert retrieved.filled_quantity == order.quantity
                assert retrieved.average_fill_price == Decimal("50000")

    def test_verify_integrity_valid(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                order = create_test_order()
                store.save_order(order)

                valid_count, invalid_orders = store.verify_integrity()

                assert valid_count == 1
                assert invalid_orders == []

    def test_cleanup_old_orders(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                order = create_test_order(status=OrderStatus.FILLED)
                store.save_order(order)

                deleted = store.cleanup_old_orders(days=0)

                assert deleted >= 0

    def test_context_manager(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with OrdersStore(tmpdir) as store:
                order = create_test_order()
                store.save_order(order)

            store2 = OrdersStore(tmpdir)
            store2.initialize()
            retrieved = store2.get_order(order.order_id)
            assert retrieved is not None
            store2.close()
