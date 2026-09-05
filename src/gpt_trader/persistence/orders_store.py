"""
Order persistence store for durable order state tracking.

Provides crash-safe order state persistence with:
- Atomic writes via SQLite WAL mode
- Order lifecycle tracking (pending → filled/cancelled)
- Recovery of incomplete orders on restart
- Checksum validation for critical order data
"""

from __future__ import annotations

import json
import sqlite3
import threading
import weakref
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from gpt_trader.core.order_intent import OrderIntent
from gpt_trader.persistence.durability import (
    WriteError,
    WriteResult,
    check_sqlite_integrity,
)
from gpt_trader.persistence.orders_models import OrderRecord, OrderStatus
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="orders_store")


class _ConnectionHolder:
    """Thread-local SQLite connection wrapper that unregisters itself on teardown."""

    __slots__ = ("_closed", "_store_ref", "connection", "__weakref__")

    def __init__(self, store: OrdersStore, connection: sqlite3.Connection) -> None:
        self._closed = False
        self._store_ref = weakref.ref(store)
        self.connection = connection

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, *, checkpoint: bool = False, unregister: bool = True) -> None:
        if self._closed:
            return

        self._closed = True
        if unregister:
            store = self._store_ref()
            if store is not None:
                store._unregister_connection(self)

        if checkpoint:
            with suppress(sqlite3.Error):
                self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with suppress(sqlite3.Error):
            self.connection.close()

    def __del__(self) -> None:
        self.close()


_ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT,
    status TEXT NOT NULL,
    filled_quantity TEXT NOT NULL,
    average_fill_price TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    bot_id TEXT,
    time_in_force TEXT DEFAULT 'GTC',
    metadata TEXT,
    checksum TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_bot_id ON orders(bot_id);
CREATE INDEX IF NOT EXISTS idx_orders_client_order_id ON orders(client_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
"""


class OrdersStore:
    """
    Persists order state to SQLite for crash recovery.

    Features:
    - Atomic writes with WAL mode
    - Order lifecycle tracking
    - Recovery of pending/open orders on restart
    - Checksum validation for data integrity
    """

    def __init__(self, storage_path: str | Path) -> None:
        """
        Initialize orders store.

        Args:
            storage_path: Directory for database file
        """
        self.storage_path = Path(storage_path)
        self._database_path = self.storage_path / "orders.db"
        self._lock = threading.Lock()
        self._connection_lock = threading.Lock()
        self._connections: weakref.WeakValueDictionary[int, _ConnectionHolder] = (
            weakref.WeakValueDictionary()
        )
        self._connection_generation = 0
        self._initialized = False
        self._local = threading.local()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        holder: _ConnectionHolder | None = getattr(self._local, "connection_holder", None)
        connection_generation: int | None = getattr(self._local, "connection_generation", None)
        if (
            holder is not None
            and not holder.closed
            and connection_generation == self._connection_generation
        ):
            return holder.connection

        if holder is not None:
            holder.close()
            del self._local.connection_holder
            if hasattr(self._local, "connection_generation"):
                del self._local.connection_generation

        with self._connection_lock:
            generation = self._connection_generation

        connection = sqlite3.connect(
            str(self._database_path),
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode
        )
        cleanup_holder: _ConnectionHolder | None = None
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.row_factory = sqlite3.Row

            retry_connection = False
            connection_holder = _ConnectionHolder(self, connection)
            cleanup_holder = connection_holder
            with self._connection_lock:
                if generation != self._connection_generation:
                    retry_connection = True
                else:
                    self._connections[id(connection_holder)] = connection_holder
                    self._local.connection_holder = connection_holder
                    self._local.connection_generation = generation

            if retry_connection:
                connection_holder.close()
                return self._get_connection()

            if connection_holder.closed or generation != self._connection_generation:
                if getattr(self._local, "connection_holder", None) is connection_holder:
                    del self._local.connection_holder
                if hasattr(self._local, "connection_generation"):
                    del self._local.connection_generation
                connection_holder.close()
                return self._get_connection()

            return connection
        except Exception:
            if cleanup_holder is not None:
                cleanup_holder.close()
            else:
                with suppress(sqlite3.Error):
                    connection.close()
            raise

    def _unregister_connection(self, holder: _ConnectionHolder) -> None:
        with self._connection_lock:
            self._connections.pop(id(holder), None)

    def initialize(self, *, check_integrity: bool = True) -> None:
        """
        Initialize database schema.

        Args:
            check_integrity: Run integrity check on existing database
        """
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            self.storage_path.mkdir(parents=True, exist_ok=True)

            # Check integrity of existing database
            if check_integrity and self._database_path.exists():
                is_ok, issues = check_sqlite_integrity(self._database_path)
                if not is_ok:
                    logger.error(
                        "Orders database corruption detected",
                        operation="orders_init",
                        path=str(self._database_path),
                        issues=issues[:5],
                    )

            connection = self._get_connection()
            connection.executescript(_ORDERS_SCHEMA)
            self._initialized = True

            logger.info(
                "Orders store initialized",
                operation="orders_init",
                path=str(self._database_path),
            )

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    def save_order(self, order: OrderRecord, *, raise_on_error: bool = False) -> WriteResult:
        """
        Save or update an order record.

        Uses INSERT OR REPLACE for atomic upsert.

        Args:
            order: Order record to save
            raise_on_error: If True, raise WriteError on failure

        Returns:
            WriteResult with success status
        """
        self._ensure_initialized()
        try:
            connection = self._get_connection()
            checksum = order.compute_checksum()

            connection.execute(
                """
                INSERT OR REPLACE INTO orders (
                    order_id, client_order_id, symbol, side, order_type,
                    quantity, price, status, filled_quantity, average_fill_price,
                    created_at, updated_at, bot_id, time_in_force, metadata, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_id,
                    order.client_order_id,
                    order.symbol,
                    order.side,
                    order.order_type,
                    str(order.quantity),
                    str(order.price) if order.price else None,
                    order.status.value,
                    str(order.filled_quantity),
                    str(order.average_fill_price) if order.average_fill_price else None,
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                    order.bot_id,
                    order.time_in_force,
                    json.dumps(order.metadata) if order.metadata else None,
                    checksum,
                ),
            )

            logger.debug(
                "Order saved",
                operation="save_order",
                order_id=order.order_id,
                status=order.status.value,
            )

            return WriteResult.ok(checksum=checksum)

        except sqlite3.Error as e:
            error_msg = f"Failed to save order {order.order_id}: {e}"
            logger.error(error_msg, operation="save_order", error=str(e))
            if raise_on_error:
                raise WriteError(error_msg) from e
            return WriteResult.fail(error_msg)

    def reserve_submission(self, order: OrderRecord) -> OrderRecord | None:
        """Atomically reserve a new client ID or return its existing request.

        No network call belongs in this transaction. A committed pending record
        survives uncertainty and must never be interpreted as permission to resend.
        """
        self._ensure_initialized()
        connection = self._get_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?",
                (order.client_order_id,),
            ).fetchall()
            if len(rows) > 1:
                raise WriteError("Multiple orders share one client order ID")
            existing = self._row_to_record(rows[0]) if rows else None
            if existing is None:
                if self.get_order(order.order_id) is not None:
                    raise WriteError("Order ID already belongs to another client identity")
                result = self.save_order(order, raise_on_error=True)
                if not result.success:
                    raise WriteError(result.error or "Order reservation failed")
            elif not existing.checksum_is_valid():
                raise WriteError("Existing order checksum mismatch")
            connection.commit()
            return existing
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _preserve_intent(existing: OrderRecord, order: OrderRecord) -> OrderRecord:
        original_metadata = existing.metadata or {}
        metadata = order.metadata or {}
        if "intent" in original_metadata:
            for key in ("intent", "record_checksum_version", "reduce_only", "leverage"):
                if key in metadata and metadata[key] != original_metadata.get(key):
                    raise WriteError(f"Order update conflicts with immutable {key}")
            order = replace(
                order,
                metadata={**original_metadata, **metadata},
                created_at=existing.created_at,
            )
            intent = OrderIntent.from_dict(original_metadata["intent"])
            intent.validate_receipt(order)
            if order.filled_quantity < existing.filled_quantity:
                raise WriteError("Order update decreases observed fill quantity")
            if existing.status is OrderStatus.FILLED and (
                order.status is not OrderStatus.FILLED
                or order.average_fill_price != existing.average_fill_price
            ):
                raise WriteError("Order update conflicts with terminal fill receipt")
        elif (order.metadata or {}).get("record_checksum_version") == 2:
            order = replace(order, created_at=existing.created_at)
        return order

    def upsert_by_client_id(
        self, order: OrderRecord, *, raise_on_error: bool = False
    ) -> WriteResult:
        """
        Save or update an order record using client_order_id as the match key.

        This allows order_id to transition from client_order_id to broker order_id
        while keeping a single persistent record for crash recovery.
        """
        self._ensure_initialized()
        try:
            connection = self._get_connection()
            connection.execute("BEGIN IMMEDIATE")
            existing = self.get_order_by_client_order_id(order.client_order_id)
            if existing is not None:
                order = self._preserve_intent(existing, order)
            checksum = order.compute_checksum()
            metadata = json.dumps(order.metadata) if order.metadata else None

            cursor = connection.execute(
                """
                UPDATE orders
                SET order_id = ?, symbol = ?, side = ?, order_type = ?,
                    quantity = ?, price = ?, status = ?, filled_quantity = ?,
                    average_fill_price = ?, updated_at = ?, bot_id = ?,
                    time_in_force = ?, metadata = ?, checksum = ?
                WHERE client_order_id = ?
                """,
                (
                    order.order_id,
                    order.symbol,
                    order.side,
                    order.order_type,
                    str(order.quantity),
                    str(order.price) if order.price else None,
                    order.status.value,
                    str(order.filled_quantity),
                    str(order.average_fill_price) if order.average_fill_price else None,
                    order.updated_at.isoformat(),
                    order.bot_id,
                    order.time_in_force,
                    metadata,
                    checksum,
                    order.client_order_id,
                ),
            )

            if cursor.rowcount == 0:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO orders (
                        order_id, client_order_id, symbol, side, order_type,
                        quantity, price, status, filled_quantity, average_fill_price,
                        created_at, updated_at, bot_id, time_in_force, metadata, checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.order_id,
                        order.client_order_id,
                        order.symbol,
                        order.side,
                        order.order_type,
                        str(order.quantity),
                        str(order.price) if order.price else None,
                        order.status.value,
                        str(order.filled_quantity),
                        str(order.average_fill_price) if order.average_fill_price else None,
                        order.created_at.isoformat(),
                        order.updated_at.isoformat(),
                        order.bot_id,
                        order.time_in_force,
                        metadata,
                        checksum,
                    ),
                )

            connection.commit()

            logger.debug(
                "Order upserted",
                operation="upsert_order",
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                status=order.status.value,
            )

            return WriteResult.ok(checksum=checksum)

        except (sqlite3.Error, WriteError, ValueError, ArithmeticError) as e:
            if "connection" in locals():
                connection.rollback()
            error_msg = f"Failed to upsert order {order.order_id}: {e}"
            logger.error(error_msg, operation="upsert_order", error=str(e))
            if raise_on_error:
                raise WriteError(error_msg) from e
            return WriteResult.fail(error_msg)
        except BaseException:
            if "connection" in locals():
                connection.rollback()
            raise

    def get_order(self, order_id: str) -> OrderRecord | None:
        """
        Get order by ID.

        Args:
            order_id: Order identifier

        Returns:
            OrderRecord if found, None otherwise
        """
        self._ensure_initialized()
        connection = self._get_connection()
        cursor = connection.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return self._row_to_record(row)

    def get_order_by_client_order_id(self, client_order_id: str) -> OrderRecord | None:
        """
        Get order by client order ID.

        Args:
            client_order_id: Client order identifier

        Returns:
            OrderRecord if found, None otherwise
        """
        self._ensure_initialized()
        connection = self._get_connection()
        cursor = connection.execute(
            "SELECT * FROM orders WHERE client_order_id = ?",
            (client_order_id,),
        )
        rows = cursor.fetchall()
        if len(rows) > 1:
            raise WriteError("Multiple orders share one client order ID")
        if not rows:
            return None
        record = self._row_to_record(rows[0])
        if not record.checksum_is_valid():
            raise WriteError("Existing order checksum mismatch")
        return record

    def get_pending_orders(self, bot_id: str | None = None) -> list[OrderRecord]:
        """
        Get all non-terminal orders.

        These are orders that may need recovery after restart.

        Args:
            bot_id: Optional filter by bot

        Returns:
            List of pending/open orders
        """
        self._ensure_initialized()
        connection = self._get_connection()
        non_terminal = (
            OrderStatus.PENDING.value,
            OrderStatus.OPEN.value,
            OrderStatus.PARTIALLY_FILLED.value,
        )

        if bot_id:
            cursor = connection.execute(
                """
                SELECT * FROM orders
                WHERE status IN (?, ?, ?) AND bot_id = ?
                ORDER BY created_at ASC
                """,
                (*non_terminal, bot_id),
            )
        else:
            cursor = connection.execute(
                """
                SELECT * FROM orders
                WHERE status IN (?, ?, ?)
                ORDER BY created_at ASC
                """,
                non_terminal,
            )

        return [self._row_to_record(row) for row in cursor]

    def get_orders_by_symbol(
        self, symbol: str, *, include_terminal: bool = False
    ) -> list[OrderRecord]:
        """
        Get orders for a symbol.

        Args:
            symbol: Trading symbol
            include_terminal: Include filled/cancelled orders

        Returns:
            List of orders
        """
        self._ensure_initialized()
        connection = self._get_connection()

        if include_terminal:
            cursor = connection.execute(
                "SELECT * FROM orders WHERE symbol = ? ORDER BY created_at DESC",
                (symbol,),
            )
        else:
            non_terminal = (
                OrderStatus.PENDING.value,
                OrderStatus.OPEN.value,
                OrderStatus.PARTIALLY_FILLED.value,
            )
            cursor = connection.execute(
                """
                SELECT * FROM orders
                WHERE symbol = ? AND status IN (?, ?, ?)
                ORDER BY created_at DESC
                """,
                (symbol, *non_terminal),
            )

        return [self._row_to_record(row) for row in cursor]

    def list_orders(
        self,
        *,
        limit: int = 100,
        symbol: str | None = None,
        status: OrderStatus | None = None,
    ) -> list[OrderRecord]:
        """
        Retrieve recent order records with optional filtering.

        Returns rows ordered by updated_at DESC and order_id ASC to keep the output
        deterministic.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")

        self._ensure_initialized()
        connection = self._get_connection()

        filters: list[str] = []
        params: list[object] = []

        if symbol:
            filters.append("symbol = ?")
            params.append(symbol)

        if status:
            filters.append("status = ?")
            params.append(status.value)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        sql_parts = ["SELECT * FROM orders"]
        if where_clause:
            sql_parts.append(where_clause)
        sql_parts.extend(
            [
                "ORDER BY updated_at DESC, order_id ASC",
                "LIMIT ?",
            ]
        )
        sql = " ".join(sql_parts)
        params.append(limit)

        cursor = connection.execute(sql, tuple(params))
        return [self._row_to_record(row) for row in cursor]

    def update_status(
        self,
        order_id: str,
        status: OrderStatus,
        *,
        filled_quantity: Decimal | None = None,
        average_fill_price: Decimal | None = None,
    ) -> WriteResult:
        """
        Update order status.

        Args:
            order_id: Order identifier
            status: New status
            filled_quantity: Updated fill quantity
            average_fill_price: Updated average price

        Returns:
            WriteResult with success status
        """
        self._ensure_initialized()
        try:
            connection = self._get_connection()
            connection.execute("BEGIN IMMEDIATE")
            original = self.get_order(order_id)
            if original is not None and not original.checksum_is_valid():
                raise WriteError("Existing order checksum mismatch")
            if original is not None:
                updated = replace(
                    original,
                    status=status,
                    updated_at=datetime.now(timezone.utc),
                    filled_quantity=(
                        filled_quantity if filled_quantity is not None else original.filled_quantity
                    ),
                    average_fill_price=(
                        average_fill_price
                        if average_fill_price is not None
                        else original.average_fill_price
                    ),
                )
                updated = self._preserve_intent(original, updated)
                self.save_order(updated, raise_on_error=True)
            connection.commit()

            logger.debug(
                "Order status updated",
                operation="update_status",
                order_id=order_id,
                status=status.value,
            )

            return WriteResult.ok()

        except (sqlite3.Error, WriteError) as e:
            if "connection" in locals():
                connection.rollback()
            error_msg = f"Failed to update order {order_id}: {e}"
            logger.error(error_msg, operation="update_status", error=str(e))
            return WriteResult.fail(error_msg)
        except BaseException:
            if "connection" in locals():
                connection.rollback()
            raise

    def verify_integrity(self, chunk_size: int = 1000) -> tuple[int, list[str]]:
        """
        Verify integrity of all stored orders.

        Args:
            chunk_size: Number of orders to fetch and process in each batch.

        Returns:
            Tuple of (valid_count, list of invalid order_ids)
        """
        self._ensure_initialized()
        connection = self._get_connection()
        cursor = connection.execute("SELECT * FROM orders")

        valid_count = 0
        invalid_orders: list[str] = []

        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break

            for row in rows:
                record = self._row_to_record(row)
                if not record.checksum_is_valid():
                    invalid_orders.append(record.order_id)
                    logger.warning(
                        "Order checksum mismatch",
                        operation="verify_integrity",
                        order_id=record.order_id,
                        stored=row["checksum"][:16] if row["checksum"] else None,
                    )
                else:
                    valid_count += 1

        return valid_count, invalid_orders

    def cleanup_old_orders(self, days: int = 30) -> int:
        """
        Remove terminal orders older than specified days.

        Args:
            days: Age threshold in days

        Returns:
            Number of orders deleted
        """
        self._ensure_initialized()
        connection = self._get_connection()
        terminal_statuses = (
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REJECTED.value,
            OrderStatus.EXPIRED.value,
            OrderStatus.FAILED.value,
        )

        cursor = connection.execute(
            """
            DELETE FROM orders
            WHERE status IN (?, ?, ?, ?, ?)
            AND datetime(updated_at) < datetime('now', '-' || ? || ' days')
            """,
            (*terminal_statuses, days),
        )

        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(
                "Cleaned up old orders",
                operation="cleanup_orders",
                deleted=deleted,
                days_threshold=days,
            )

        return deleted

    def _row_to_record(self, row: sqlite3.Row) -> OrderRecord:
        """Convert database row to OrderRecord."""
        return OrderRecord(
            order_id=row["order_id"],
            client_order_id=row["client_order_id"],
            symbol=row["symbol"],
            side=row["side"],
            order_type=row["order_type"],
            quantity=Decimal(row["quantity"]),
            price=Decimal(row["price"]) if row["price"] else None,
            status=OrderStatus(row["status"]),
            filled_quantity=Decimal(row["filled_quantity"]),
            average_fill_price=(
                Decimal(row["average_fill_price"]) if row["average_fill_price"] else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            bot_id=row["bot_id"],
            time_in_force=row["time_in_force"] or "GTC",
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            checksum=row["checksum"],
        )

    def close(self) -> None:
        """
        Close all SQLite connections created by this store.

        `OrdersStore` keeps one connection per thread. Explicit close is required
        for initialize-only or short-lived usage so Windows can release
        `orders.db` and its WAL side files before cleanup or rotation.
        """
        with self._connection_lock:
            holders = list(self._connections.values())
            self._connections.clear()
            self._connection_generation += 1

        for holder in holders:
            holder.close(checkpoint=True, unregister=False)

        if hasattr(self._local, "connection_holder"):
            del self._local.connection_holder
        if hasattr(self._local, "connection_generation"):
            del self._local.connection_generation
        self._initialized = False

    def __enter__(self) -> OrdersStore:
        """Context manager entry."""
        self.initialize()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any
    ) -> None:
        """Context manager exit."""
        self.close()


__all__ = [
    "OrderRecord",
    "OrderStatus",
    "OrdersStore",
]
