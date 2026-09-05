"""Stateful open-order reconciliation for the live trading engine."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from gpt_trader.features.live_trade.degradation import DegradationState
from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.engines.order_record_mapping import (
    build_record_from_broker_order,
    get_order_field,
    merge_metadata,
)
from gpt_trader.features.live_trade.execution.broker_executor import BrokerExecutor
from gpt_trader.features.live_trade.execution.order_submission import OrderSubmitter
from gpt_trader.monitoring.alert_types import AlertSeverity
from gpt_trader.persistence.durability import WriteError
from gpt_trader.persistence.orders_store import OrderRecord
from gpt_trader.persistence.orders_store import OrderStatus as PersistedOrderStatus
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="order_reconciliation")

BrokerCallProvider = Callable[[], Callable[..., Awaitable[Any]]]
OrdersStoreProvider = Callable[[], Any | None]
AppendEvent = Callable[[str, dict[str, Any]], None]
Notify = Callable[..., Awaitable[None]]


class OrderReconciliationService:
    """Reconcile broker open orders with in-memory and persisted order state."""

    def __init__(
        self,
        *,
        context: CoordinatorContext,
        open_orders: list[str],
        orders_store_provider: OrdersStoreProvider,
        broker_calls_provider: BrokerCallProvider,
        degradation: DegradationState,
        order_submitter: OrderSubmitter,
        append_event: AppendEvent,
        notify: Notify,
        drift_max_failures: int,
    ) -> None:
        self._context = context
        self._open_orders = open_orders
        self._orders_store_provider = orders_store_provider
        self._broker_calls_provider = broker_calls_provider
        self._degradation = degradation
        self._order_submitter = order_submitter
        self._append_event = append_event
        self._notify = notify
        self._drift_max_failures = drift_max_failures
        self._drift_failures = 0
        self._drift_escalated = False

    @property
    def drift_failures(self) -> int:
        return self._drift_failures

    @property
    def drift_escalated(self) -> bool:
        return self._drift_escalated

    async def recover_unknown_bot_orders(
        self,
        orders: list[Any],
        *,
        bot_id: str,
    ) -> list[OrderRecord]:
        orders_store = self._orders_store_provider()
        if not orders or orders_store is None:
            return []
        now = datetime.now(timezone.utc)
        recovered: list[OrderRecord] = []
        for order in orders:
            record = build_record_from_broker_order(order, bot_id=bot_id, now=now)
            if record is None:
                continue
            metadata = dict(record.metadata or {})
            metadata["note"] = "backfilled_from_broker"
            record = replace(record, metadata=metadata)
            try:
                result = await self._broker_call(
                    orders_store.upsert_by_client_id, record, raise_on_error=True
                )
                if not result.success:
                    raise WriteError(result.error or "Order recovery could not be persisted")
            except Exception as exc:
                logger.warning(
                    "Failed to persist recovered order",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="order_reconciliation",
                    stage="persist_recovery",
                    order_id=record.order_id,
                    client_order_id=record.client_order_id,
                )
                continue
            if record.order_id not in self._open_orders:
                self._open_orders.append(record.order_id)
            recovered.append(record)

        if recovered:
            payload = {
                "timestamp": time.time(),
                "bot_id": bot_id,
                "recovered_order_ids": [record.order_id for record in recovered][:10],
                "recovered_order_count": len(recovered),
                "open_order_count": len(self._open_orders),
            }
            self._append_event("order_reconciliation_recovered", payload)
            logger.info(
                "Recovered unknown bot orders",
                recovered_order_count=len(recovered),
                operation="order_reconciliation",
                stage="recover",
            )

        return recovered

    async def refresh_missing_persisted_orders(
        self,
        records: list[OrderRecord],
        *,
        bot_id: str,
    ) -> list[Any]:
        orders_store = self._orders_store_provider()
        if not records or orders_store is None:
            return []
        broker = self._context.broker
        if broker is None:
            return []
        get_order = getattr(broker, "get_order", None)
        if not callable(get_order):
            return []
        now = datetime.now(timezone.utc)
        refreshed_orders: list[Any] = []
        for record in records:
            try:
                order = await self._broker_call(get_order, record.order_id)
                if order is None and record.client_order_id != record.order_id:
                    order = await self._broker_call(get_order, record.client_order_id)
            except Exception as exc:
                logger.warning(
                    "Failed to load order during reconciliation",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="order_reconciliation",
                    stage="load_missing",
                    order_id=record.order_id,
                    client_order_id=record.client_order_id,
                )
                continue
            if order is None:
                continue
            update_record = build_record_from_broker_order(order, bot_id=bot_id, now=now)
            if update_record is None:
                continue
            update_metadata = dict(update_record.metadata or {})
            update_metadata["note"] = "refresh_missing"
            metadata = merge_metadata(record.metadata, update_metadata)
            updated = replace(
                record,
                order_id=update_record.order_id,
                client_order_id=update_record.client_order_id,
                symbol=update_record.symbol or record.symbol,
                side=update_record.side or record.side,
                order_type=update_record.order_type or record.order_type,
                quantity=(
                    update_record.quantity
                    if update_record.quantity != Decimal("0")
                    else record.quantity
                ),
                price=update_record.price if update_record.price is not None else record.price,
                status=update_record.status,
                filled_quantity=(
                    update_record.filled_quantity
                    if update_record.filled_quantity != Decimal("0")
                    else record.filled_quantity
                ),
                average_fill_price=(update_record.average_fill_price or record.average_fill_price),
                updated_at=now,
                metadata=metadata,
                bot_id=record.bot_id or bot_id,
                time_in_force=update_record.time_in_force or record.time_in_force,
            )
            try:
                result = await self._broker_call(
                    orders_store.upsert_by_client_id, updated, raise_on_error=True
                )
                if not result.success:
                    raise WriteError(result.error or "Order refresh could not be persisted")
            except Exception as exc:
                logger.warning(
                    "Failed to persist order refresh",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="order_reconciliation",
                    stage="persist_refresh",
                    order_id=updated.order_id,
                    client_order_id=updated.client_order_id,
                )
                continue

            if updated.status in {
                PersistedOrderStatus.PENDING,
                PersistedOrderStatus.OPEN,
                PersistedOrderStatus.PARTIALLY_FILLED,
            }:
                if updated.order_id not in self._open_orders:
                    self._open_orders.append(updated.order_id)
                refreshed_orders.append(order)
            else:
                self._remove_open_order_ids(
                    record.order_id,
                    record.client_order_id,
                    updated.order_id,
                    updated.client_order_id,
                )

        return refreshed_orders

    async def reconcile_open_orders(self, orders: list[Any]) -> list[Any]:
        """Reconcile internal open-order tracking with broker + persistence state."""
        bot_id = str(self._context.bot_id or self._context.config.profile or "live")
        prefix = f"{bot_id}_"

        orders_store = self._orders_store_provider()
        pending: list[OrderRecord] = []
        pending_by_client_id: dict[str, OrderRecord] = {}
        tracked_ids: set[str] = {str(order_id) for order_id in self._open_orders}

        if orders_store is not None:
            try:
                pending = await self._broker_call(
                    orders_store.get_pending_orders,
                    bot_id=bot_id or None,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to load pending orders during audit",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="order_reconciliation",
                    stage="load_pending",
                )
                pending = []

        for store_record in pending:
            pending_by_client_id[str(store_record.client_order_id)] = store_record
            tracked_ids.add(str(store_record.client_order_id))
            tracked_ids.add(str(store_record.order_id))

        if not orders:
            refreshed_orders: list[Any] = []
            if pending:
                refreshed_orders = await self.refresh_missing_persisted_orders(
                    pending,
                    bot_id=bot_id,
                )
            if refreshed_orders:
                orders = refreshed_orders
            else:
                if not pending and self._open_orders:
                    self._open_orders.clear()
                self._reset_drift()
                return []

        order_index: dict[str, Any] = {}

        def _index_order(order: Any) -> None:
            if order is None:
                return
            order_id = get_order_field(order, "order_id", "id")
            client_order_id = get_order_field(order, "client_order_id", "client_id")
            if order_id is not None:
                order_index[str(order_id)] = order
            if client_order_id is not None:
                order_index[str(client_order_id)] = order

        for order in orders:
            _index_order(order)

        if orders_store is not None and pending_by_client_id:
            now = datetime.now(timezone.utc)
            for order in orders:
                order_id = get_order_field(order, "order_id", "id")
                client_order_id = get_order_field(order, "client_order_id", "client_id")
                if order_id is None or client_order_id is None:
                    continue
                client_id_str = str(client_order_id)
                pending_record = pending_by_client_id.get(client_id_str)
                if pending_record is None:
                    continue
                if pending_record.order_id != pending_record.client_order_id:
                    continue
                order_id_str = str(order_id)
                if order_id_str == pending_record.order_id:
                    continue
                try:
                    updated = replace(
                        pending_record,
                        order_id=order_id_str,
                        status=PersistedOrderStatus.OPEN,
                        updated_at=now,
                        metadata={
                            **(pending_record.metadata or {}),
                            "source": "order_audit",
                            "note": "normalized_submit_id_to_order_id",
                        },
                    )
                    result = await self._broker_call(
                        orders_store.upsert_by_client_id, updated, raise_on_error=True
                    )
                    if not result.success:
                        raise WriteError(
                            result.error or "Order normalization could not be persisted"
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to persist order_id normalization",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        operation="order_reconciliation",
                        stage="persist_normalization",
                        client_order_id=client_id_str,
                        order_id=order_id_str,
                    )

        if pending:
            open_ids = set(order_index.keys())
            missing_records = [
                record
                for record in pending
                if str(record.order_id) not in open_ids
                and str(record.client_order_id) not in open_ids
            ]
            refreshed_orders = await self.refresh_missing_persisted_orders(
                missing_records,
                bot_id=bot_id,
            )
            for refreshed_order in refreshed_orders:
                _index_order(refreshed_order)
            if refreshed_orders:
                orders = [*orders, *refreshed_orders]

        snapshot = list(self._open_orders)
        snapshot_set = set(snapshot)
        reconciled: list[str] = []
        seen: set[str] = set()

        def _canonical_order_id(tracked_id: str) -> str | None:
            order = order_index.get(tracked_id)
            if order is None:
                return None
            order_id = get_order_field(order, "order_id", "id") or tracked_id
            return str(order_id)

        for tracked_id in snapshot:
            canonical = _canonical_order_id(tracked_id)
            if canonical is None or canonical in seen:
                continue
            seen.add(canonical)
            reconciled.append(canonical)

        for tracked_id in list(self._open_orders):
            if tracked_id in snapshot_set:
                continue
            canonical = _canonical_order_id(tracked_id) or str(tracked_id)
            if canonical in seen:
                continue
            seen.add(canonical)
            reconciled.append(canonical)

        if reconciled != snapshot:
            self._open_orders[:] = reconciled

        unknown_bot_orders: list[str] = []
        unknown_orders: list[Any] = []
        for order in orders:
            client_order_id = get_order_field(order, "client_order_id", "client_id")
            if client_order_id is None:
                continue
            client_id_str = str(client_order_id)
            if not client_id_str.startswith(prefix):
                continue
            order_id = get_order_field(order, "order_id", "id")
            order_id_str = str(order_id) if order_id is not None else client_id_str
            if order_id_str in tracked_ids or client_id_str in tracked_ids:
                continue
            unknown_bot_orders.append(order_id_str)
            unknown_orders.append(order)

        recovered_records = await self.recover_unknown_bot_orders(
            unknown_orders,
            bot_id=bot_id,
        )
        if recovered_records:
            recovered_ids = {record.order_id for record in recovered_records}
            recovered_ids.update(record.client_order_id for record in recovered_records)
            tracked_ids.update(recovered_ids)
            unknown_bot_orders = [
                order_id for order_id in unknown_bot_orders if order_id not in recovered_ids
            ]

        if not unknown_bot_orders:
            self._reset_drift()
            return orders

        payload = {
            "timestamp": time.time(),
            "bot_id": bot_id,
            "unknown_bot_order_ids": unknown_bot_orders[:10],
            "unknown_bot_order_count": len(unknown_bot_orders),
            "open_order_count": len(orders),
        }
        self._append_event("order_reconciliation_drift", payload)
        logger.warning(
            "Order reconciliation drift detected",
            unknown_bot_order_count=len(unknown_bot_orders),
            open_order_count=len(orders),
            operation="order_reconciliation",
            stage="drift",
        )

        if self._drift_escalated:
            return orders

        self._drift_failures += 1
        if self._drift_failures < self._drift_max_failures:
            return orders

        self._drift_escalated = True
        await self.handle_order_reconciliation_drift(unknown_bot_orders)
        return orders

    async def handle_order_reconciliation_drift(self, order_ids: list[str]) -> None:
        """Escalate persistent order-reconciliation drift to graceful degradation."""
        risk_manager = self._context.risk_manager
        config = risk_manager.config if risk_manager else None

        cooldown_seconds = getattr(config, "api_health_cooldown_seconds", 300) if config else 300
        if risk_manager is not None:
            risk_manager.set_reduce_only_mode(True, reason="order_reconciliation_drift")

        broker = self._context.broker
        if broker is not None:
            cancel_executor = BrokerExecutor(broker=broker)
            retry_enabled = getattr(
                self._context.config,
                "order_submission_retries_enabled",
                False,
            )
            for order_id in order_ids:
                try:
                    await self._broker_call(
                        cancel_executor.cancel_order,
                        order_id,
                        use_retry=retry_enabled,
                        allow_idempotent=True,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to cancel drifted order",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        operation="order_reconciliation",
                        stage="cancel",
                        order_id=order_id,
                    )

        self._degradation.pause_all(
            seconds=cooldown_seconds,
            reason="order_reconciliation_drift",
            allow_reduce_only=True,
        )

        try:
            self._order_submitter.record_rejection(
                symbol="*",
                side="*",
                quantity=Decimal("0"),
                price=None,
                reason="order_reconciliation_drift",
            )
        except Exception:
            pass

        await self._notify(
            title="Order Reconciliation Drift - Trading Paused",
            message=(
                "Detected open broker orders that appear to belong to this bot but are not tracked. "
                f"Paused trading for {cooldown_seconds}s and enabled reduce-only mode. "
                f"Attempted cancellation for {len(order_ids)} order(s)."
            ),
            severity=AlertSeverity.ERROR,
            context={
                "unknown_order_ids": order_ids[:10],
                "unknown_order_count": len(order_ids),
                "cooldown_seconds": cooldown_seconds,
            },
        )

    async def _broker_call(self, function: Any, *args: Any, **kwargs: Any) -> Any:
        return await self._broker_calls_provider()(function, *args, **kwargs)

    def _reset_drift(self) -> None:
        self._drift_failures = 0
        self._drift_escalated = False

    def _remove_open_order_ids(self, *order_ids: str | None) -> None:
        identifiers = {str(order_id) for order_id in order_ids if order_id is not None}
        if not identifiers:
            return
        self._open_orders[:] = [
            order_id for order_id in self._open_orders if str(order_id) not in identifiers
        ]
