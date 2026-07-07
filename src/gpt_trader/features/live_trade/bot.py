"""
Simplified TradingBot.
Acts as the main entry point runner.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from gpt_trader.app.config import BotConfig
from gpt_trader.app.runtime_ui_adapter import NullUIAdapter, RuntimeUIAdapter
from gpt_trader.features.live_trade.account_snapshot import AccountSnapshotService
from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.engines.strategy import TradingEngine
from gpt_trader.features.live_trade.execution.status_codec import (
    ExecutionStatusCodecError,
    execution_status_for_store,
)
from gpt_trader.features.live_trade.lifecycle import (
    TRADING_BOT_TRANSITIONS,
    EngineState,
    LifecycleStateMachine,
    TradingBotState,
)
from gpt_trader.features.live_trade.order_value_extraction import (
    decimal_or_none,
    decimal_or_zero,
    extract_order_decimal,
    extract_order_identifier,
    stringify_order_value,
)
from gpt_trader.features.recorder import PriceTickStore
from gpt_trader.monitoring.alert_types import AlertSeverity
from gpt_trader.persistence.orders_store import OrderRecord, OrderStatus
from gpt_trader.utilities.async_tools import BoundedToThread
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.app.protocols import (
        ApplicationContainerProtocol,
        EventStoreProtocol,
        RuntimeStateProtocol,
    )
    from gpt_trader.core import Product
    from gpt_trader.core.protocols import BrokerProtocol
    from gpt_trader.features.live_trade.risk.protocols import RiskManagerProtocol
    from gpt_trader.monitoring.notifications.service import NotificationService

logger = get_logger(__name__, component="trading_bot")


class TradingBot:
    def __init__(
        self,
        config: BotConfig,
        container: ApplicationContainerProtocol,
        event_store: EventStoreProtocol | None = None,
        orders_store: Any = None,
        notification_service: NotificationService | None = None,
        ui_adapter: RuntimeUIAdapter | None = None,
    ) -> None:
        self.config = config
        self.container = container
        self._lifecycle: LifecycleStateMachine[TradingBotState] = LifecycleStateMachine(
            initial_state=TradingBotState.INIT,
            entity="trading_bot",
            transitions=TRADING_BOT_TRANSITIONS,
            logger=logger,
        )
        self._preserve_flatten_failure_state = False

        # Get services directly from container (legacy registry removed)
        self.broker: BrokerProtocol | None = container.broker
        # Read-only account snapshot provider (account-snapshot ADR Option A):
        # `gpt-trader account snapshot` and the runbooks read the same broker
        # surface the engine uses. No order authority.
        self.account_telemetry: AccountSnapshotService | None = (
            AccountSnapshotService(self.broker, config) if self.broker is not None else None
        )
        self.risk_manager: RiskManagerProtocol | None = container.risk_manager
        self.runtime_state: RuntimeStateProtocol | None = getattr(container, "runtime_state", None)

        # Get event_store from parameter or container
        self._event_store = event_store or container.event_store

        # Get orders_store from parameter or container
        self._orders_store = orders_store or container.orders_store

        # Get notification_service from parameter or container
        self._notification_service = notification_service or container.notification_service

        # Concurrency-limited entrypoint for sync broker calls from async code.
        broker_call_limit = getattr(config, "max_concurrent_broker_calls", None)
        if broker_call_limit is None:
            broker_call_limit = getattr(config, "max_concurrent_rest_calls", 5)
        try:
            raw_limit = broker_call_limit if broker_call_limit is not None else 5
            broker_call_limit = int(raw_limit)
        except (TypeError, ValueError):
            broker_call_limit = 5
        broker_call_limit = max(1, broker_call_limit)
        use_dedicated_executor = (
            getattr(config, "broker_calls_use_dedicated_executor", False) is True
        )
        self._broker_calls = BoundedToThread(
            max_concurrency=broker_call_limit,
            use_dedicated_executor=use_dedicated_executor,
        )

        # Recording state is constructed here, not inside the engine, so the
        # recorder role can outlive execution (five-role composition).
        self._price_tick_store = PriceTickStore(
            event_store=self._event_store,
            symbols=list(config.symbols),
            bot_id="",
        )

        # Setup context
        self.context = CoordinatorContext(
            config=config,
            container=container,
            broker=self.broker,
            broker_calls=self._broker_calls,
            symbols=tuple(config.symbols),
            risk_manager=self.risk_manager,
            event_store=self._event_store,
            orders_store=self._orders_store,
            notification_service=self._notification_service,
            price_tick_store=self._price_tick_store,
        )

        self.engine = TradingEngine(self.context)
        self.ui_adapter: RuntimeUIAdapter = ui_adapter or NullUIAdapter()
        self.ui_adapter.attach(self)

    @property
    def state(self) -> TradingBotState:
        return self._lifecycle.state

    @property
    def running(self) -> bool:
        return self.state in (TradingBotState.STARTING, TradingBotState.RUNNING)

    @running.setter
    def running(self, value: bool) -> None:
        target = TradingBotState.RUNNING if value else TradingBotState.STOPPED
        self._lifecycle.transition(
            target,
            reason="running_override",
            details={"via": "running_set"},
            force=True,
        )

    def _transition_state(
        self,
        target: TradingBotState,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
        force: bool = False,
    ) -> bool:
        return self._lifecycle.transition(
            target,
            reason=reason,
            details=details,
            force=force,
        )

    def _finalize_stop_state(self, stopped_reason: str) -> None:
        """Report STOPPED, or ERROR when the engine failed to shut down.

        ``TradingEngine.shutdown`` records ``EngineState.ERROR`` when the runtime
        coordinator's graceful shutdown fails (e.g. cleanup-resistant tasks
        outlive the cleanup timeout and remain owned by the runtime). The bot
        must surface that instead of reporting a clean stop.
        """
        if self.engine.state == EngineState.ERROR:
            self._transition_state(
                TradingBotState.ERROR,
                reason="engine_shutdown_failed",
                details={"engine_state": self.engine.state.value},
            )
            return
        self._transition_state(TradingBotState.STOPPED, reason=stopped_reason)

    def set_ui_adapter(self, adapter: RuntimeUIAdapter | None) -> None:
        """Attach a runtime UI adapter (no-op when None)."""
        if adapter is None:
            adapter = NullUIAdapter()

        if adapter is self.ui_adapter:
            return

        self.ui_adapter.detach()
        self.ui_adapter = adapter
        self.ui_adapter.attach(self)

    async def run(self, single_cycle: bool = False) -> None:
        self._transition_state(
            TradingBotState.STARTING,
            reason="run_called",
            details={"single_cycle": single_cycle},
        )
        logger.info("=" * 60)
        logger.info(f"TradingBot.run() called - Starting with symbols: {self.config.symbols}")
        logger.info(f"Interval: {self.config.interval}s")
        logger.info(f"Read-only mode: {getattr(self.config, 'read_only', False)}")
        logger.info("=" * 60)

        tasks = await self.engine.start_background_tasks()
        self._transition_state(
            TradingBotState.RUNNING,
            reason="background_tasks_started",
            details={"task_count": len(tasks)},
        )
        # Mark readiness once the engine has scheduled its background tasks (including health checks).
        try:
            from gpt_trader.app.health_server import mark_ready

            mark_ready(self.container.health_state, True, reason="trading_bot_running")
        except Exception:
            pass
        logger.info(f"Started {len(tasks)} background tasks")

        try:
            if single_cycle:
                # For testing/dev-fast
                await asyncio.sleep(self.config.interval + 0.1)
                await self.engine.shutdown()
            else:
                # Keep alive until tasks complete (which is never, unless error or shutdown)
                logger.info("Bot entering main loop (gathering background tasks)")
                await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot stopped (CancelledError caught).")
        except Exception as exc:
            self._transition_state(
                TradingBotState.ERROR,
                reason="run_failed",
                details={"error": str(exc)},
            )
            raise
        finally:
            try:
                from gpt_trader.app.health_server import mark_ready

                mark_ready(self.container.health_state, False, reason="trading_bot_shutdown")
            except Exception:
                pass
            logger.info("Bot shutting down...")
            preserve_flatten_failure = self._preserve_flatten_failure_state
            if preserve_flatten_failure:
                logger.warning(
                    "Bot shutdown preserving emergency flatten failure state",
                    operation="shutdown",
                    reason="flatten_and_stop_failed",
                )
            else:
                self._transition_state(TradingBotState.STOPPING, reason="shutdown_start")
            await self.engine.shutdown()
            preserve_flatten_failure = self._preserve_flatten_failure_state
            if preserve_flatten_failure:
                logger.warning(
                    "Bot shutdown left broker calls active for flatten reconciliation",
                    operation="shutdown",
                    reason="flatten_and_stop_failed",
                )
            else:
                self._shutdown_broker_calls()
                self._finalize_stop_state("shutdown_complete")
            logger.info("Bot shutdown complete.")

    async def stop(self) -> None:
        self._preserve_flatten_failure_state = False
        self._transition_state(TradingBotState.STOPPING, reason="stop_called")
        await self.engine.shutdown()
        self._shutdown_broker_calls()
        self._finalize_stop_state("stop_complete")

    async def flatten_and_stop(self) -> list[str]:
        """
        Emergency shutdown: Stop bot and close all open positions.
        Returns a list of messages describing actions taken.

        Note: This path intentionally bypasses the canonical guard stack
        (TradingEngine.submit_order) because emergency closures must succeed
        even when guards would block normal trading.
        """
        self._preserve_flatten_failure_state = False
        flatten_operation_id = f"flatten-{uuid4().hex}"
        self._transition_state(
            TradingBotState.STOPPING,
            reason="flatten_and_stop",
            details={
                "bypass_reason": "emergency_shutdown",
                "flatten_operation_id": flatten_operation_id,
            },
        )
        logger.warning(
            "EMERGENCY: Initiating Flatten & Stop",
            bypass_reason="emergency_shutdown",
            flatten_operation_id=flatten_operation_id,
            operation="flatten_and_stop",
        )
        messages = ["Bot stopping."]
        failed_closes: list[dict[str, str]] = []

        if not self.broker:
            error = "No broker connection available."
            messages.append(f"Error: {error}")
            logger.error(
                "Emergency flatten cannot close positions without broker connection",
                operation="flatten_and_stop",
                flatten_operation_id=flatten_operation_id,
                error=error,
            )
            failed_closes.append(
                {
                    "flatten_operation_id": flatten_operation_id,
                    "symbol": "unknown",
                    "quantity": "unknown",
                    "error": error,
                }
            )
            self._record_emergency_close_audit(
                flatten_operation_id=flatten_operation_id,
                symbol="unknown",
                side="unknown",
                order_type="unknown",
                quantity="unknown",
                status="failed",
                error=error,
            )
            self._preserve_flatten_failure_state = True
            self._preserve_engine_broker_calls_on_shutdown()
            await self.engine.shutdown()
            await self._handle_flatten_failure(
                failed_closes,
                messages,
                flatten_operation_id=flatten_operation_id,
            )
            return messages

        try:
            # 1. Fetch positions
            broker_calls = getattr(self, "_broker_calls", None)
            if broker_calls is not None and asyncio.iscoroutinefunction(
                getattr(broker_calls, "__call__", None)
            ):
                positions = await broker_calls(self.broker.list_positions)
            else:
                positions = await asyncio.to_thread(self.broker.list_positions)
            if not positions:
                messages.append("No open positions found.")
            else:
                # 2. Close each position
                from gpt_trader.core import OrderSide, OrderType

                for pos in positions:
                    order_type: Any = OrderType.MARKET
                    side: Any = "unknown"
                    try:
                        side = self._emergency_close_side(pos, OrderSide)
                        # Use absolute quantity for order
                        quantity = abs(pos.quantity)

                        order = await self._place_reduce_only_emergency_close(
                            symbol=pos.symbol,
                            side=side,
                            order_type=order_type,
                            quantity=quantity,
                        )
                        close_error = self._emergency_close_rejection_error(order)
                        if close_error is not None:
                            logger.error(
                                "Emergency close returned non-accepted broker status",
                                operation="flatten_and_stop",
                                flatten_operation_id=flatten_operation_id,
                                symbol=pos.symbol,
                                quantity=str(quantity),
                                broker_status=extract_order_identifier(
                                    order,
                                    ("status",),
                                ),
                                error=close_error,
                            )
                            messages.append(f"Failed to close {pos.symbol}: {close_error}")
                            failed_closes.append(
                                {
                                    "flatten_operation_id": flatten_operation_id,
                                    "symbol": str(pos.symbol),
                                    "quantity": str(quantity),
                                    "error": close_error,
                                }
                            )
                            self._record_emergency_close_audit(
                                flatten_operation_id=flatten_operation_id,
                                symbol=pos.symbol,
                                side=side,
                                order_type=order_type,
                                quantity=quantity,
                                status="failed",
                                order=order,
                                error=close_error,
                            )
                            continue
                        self._record_emergency_close_audit(
                            flatten_operation_id=flatten_operation_id,
                            symbol=pos.symbol,
                            side=side,
                            order_type=order_type,
                            quantity=quantity,
                            status="submitted",
                            order=order,
                        )
                        logger.info(
                            "Emergency close submitted",
                            symbol=pos.symbol,
                            quantity=str(quantity),
                            reduce_only=True,
                            bypass_reason="emergency_shutdown",
                            operation="flatten_and_stop",
                            flatten_operation_id=flatten_operation_id,
                        )
                        messages.append(
                            f"Submitted CLOSE for {pos.symbol} ({quantity}) reduce-only"
                        )
                    except Exception as e:
                        symbol = str(getattr(pos, "symbol", "unknown"))
                        raw_quantity: Any = getattr(pos, "quantity", None)
                        if raw_quantity is None:
                            quantity = "unknown"
                        else:
                            try:
                                quantity = str(abs(raw_quantity))
                            except TypeError:
                                quantity = str(raw_quantity)
                        logger.error(
                            "Failed to close position during emergency flatten",
                            operation="flatten_and_stop",
                            flatten_operation_id=flatten_operation_id,
                            symbol=symbol,
                            quantity=quantity,
                            error=str(e),
                        )
                        messages.append(f"Failed to close {symbol}: {e}")
                        failed_closes.append(
                            {
                                "flatten_operation_id": flatten_operation_id,
                                "symbol": symbol,
                                "quantity": quantity,
                                "error": str(e),
                            }
                        )
                        self._record_emergency_close_audit(
                            flatten_operation_id=flatten_operation_id,
                            symbol=symbol,
                            side=side,
                            order_type=order_type,
                            quantity=quantity,
                            status="failed",
                            error=str(e),
                        )

        except Exception as e:
            logger.error(
                "Flatten failed",
                operation="flatten_and_stop",
                flatten_operation_id=flatten_operation_id,
                error=str(e),
            )
            messages.append(f"Critical Error during flatten: {e}")
            failed_closes.append(
                {
                    "flatten_operation_id": flatten_operation_id,
                    "symbol": "unknown",
                    "quantity": "unknown",
                    "error": str(e),
                }
            )
            self._record_emergency_close_audit(
                flatten_operation_id=flatten_operation_id,
                symbol="unknown",
                side="unknown",
                order_type="unknown",
                quantity="unknown",
                status="failed",
                error=str(e),
            )

        if failed_closes:
            self._preserve_flatten_failure_state = True
            self._preserve_engine_broker_calls_on_shutdown()
        await self.engine.shutdown()
        if failed_closes:
            await self._handle_flatten_failure(
                failed_closes,
                messages,
                flatten_operation_id=flatten_operation_id,
            )
            return messages

        self._shutdown_broker_calls()
        self._preserve_flatten_failure_state = False
        self._finalize_stop_state("flatten_and_stop_complete")
        messages.append("Bot stopped.")
        return messages

    async def _handle_flatten_failure(
        self,
        failed_closes: list[dict[str, str]],
        messages: list[str],
        *,
        flatten_operation_id: str,
    ) -> None:
        self._preserve_flatten_failure_state = True
        failed_symbols = [
            failure["symbol"]
            for failure in failed_closes
            if failure.get("symbol") and failure["symbol"] != "unknown"
        ]
        details = {
            "flatten_operation_id": flatten_operation_id,
            "failed_close_count": len(failed_closes),
            "failed_symbols": failed_symbols,
            "failed_closes": failed_closes,
            "operator_action": (
                "Verify remaining exposure and reconcile positions before treating "
                "emergency flatten as complete."
            ),
            "monitoring_state": "alerting_active_until_reconciliation",
        }
        messages.append(
            "Emergency flatten incomplete; monitoring/alerting remain active until "
            "positions are reconciled."
        )
        logger.critical(
            "Emergency flatten incomplete",
            operation="flatten_and_stop",
            flatten_operation_id=flatten_operation_id,
            failed_close_count=len(failed_closes),
            failed_symbols=failed_symbols,
            monitoring_state=details["monitoring_state"],
        )
        self._record_flatten_failure_event(details)
        await self._notify_flatten_failure(details)
        self._transition_state(
            TradingBotState.ERROR,
            reason="flatten_and_stop_failed",
            details=details,
        )

    def _record_flatten_failure_event(self, details: dict[str, Any]) -> None:
        event_store = self._event_store
        append = getattr(event_store, "append", None)
        if not callable(append):
            return
        try:
            append("emergency_flatten_failed", details)
        except Exception as exc:
            logger.error(
                "Failed to record emergency flatten failure event",
                operation="flatten_and_stop",
                flatten_operation_id=details.get("flatten_operation_id"),
                error=str(exc),
            )

    def _record_emergency_close_audit(
        self,
        *,
        flatten_operation_id: str,
        symbol: Any,
        side: Any,
        order_type: Any,
        quantity: Any,
        status: str,
        order: Any | None = None,
        error: str | None = None,
    ) -> None:
        order_id = extract_order_identifier(order, ("id", "order_id"))
        client_order_id = extract_order_identifier(
            order,
            ("client_order_id", "client_id", "client_oid"),
        )
        broker_status = extract_order_identifier(order, ("status",)) or status
        filled_quantity = extract_order_decimal(
            order,
            ("filled_quantity", "filled_size", "fill_size", "filled_qty"),
        )
        average_fill_price = extract_order_decimal(
            order,
            ("average_filled_price", "average_fill_price", "avg_fill_price", "fill_price"),
        )
        payload: dict[str, Any] = {
            "flatten_operation_id": flatten_operation_id,
            "symbol": str(symbol),
            "side": stringify_order_value(side),
            "order_type": stringify_order_value(order_type),
            "quantity": str(quantity),
            "reduce_only": True,
            "status": status,
            "broker_status": broker_status,
        }
        if filled_quantity is not None:
            payload["filled_quantity"] = str(filled_quantity)
        if average_fill_price is not None:
            payload["average_fill_price"] = str(average_fill_price)
        if order_id:
            payload["order_id"] = order_id
        if client_order_id:
            payload["client_order_id"] = client_order_id
        if error:
            payload["error"] = error

        self._append_emergency_close_audit_event(payload)
        if error is None:
            self._persist_emergency_close_order(payload)

    def _append_emergency_close_audit_event(self, payload: dict[str, Any]) -> None:
        event_store = self._event_store
        append = getattr(event_store, "append", None)
        if not callable(append):
            return
        try:
            append("emergency_flatten_close_order", payload)
        except Exception as exc:
            logger.error(
                "Failed to record emergency flatten close-order audit event",
                operation="flatten_and_stop",
                error=str(exc),
                flatten_operation_id=payload.get("flatten_operation_id"),
                symbol=payload.get("symbol"),
            )

    def _persist_emergency_close_order(self, payload: dict[str, Any]) -> None:
        order_id = payload.get("order_id") or payload.get("client_order_id")
        if not order_id:
            return
        client_order_id = payload.get("client_order_id") or order_id
        upsert = getattr(self._orders_store, "upsert_by_client_id", None)
        if not callable(upsert):
            return
        filled_quantity = decimal_or_none(payload.get("filled_quantity"))
        status = self._emergency_close_store_status(
            payload.get("broker_status"),
            filled_quantity_known=filled_quantity is not None and filled_quantity > 0,
        )
        now = datetime.now(timezone.utc)
        metadata = {
            "source": "emergency_flatten",
            "flatten_operation_id": payload["flatten_operation_id"],
            "reduce_only": True,
            "audit_status": payload["status"],
            "broker_status": payload.get("broker_status"),
        }
        record = OrderRecord(
            order_id=str(order_id),
            client_order_id=str(client_order_id),
            symbol=str(payload["symbol"]),
            side=str(payload["side"]).lower(),
            order_type=str(payload["order_type"]).lower(),
            quantity=decimal_or_zero(payload.get("quantity")),
            price=None,
            status=status,
            filled_quantity=filled_quantity or Decimal("0"),
            average_fill_price=decimal_or_none(payload.get("average_fill_price")),
            created_at=now,
            updated_at=now,
            bot_id=self._emergency_close_bot_id(),
            metadata=metadata,
        )
        try:
            upsert(record)
        except Exception as exc:
            logger.error(
                "Failed to persist emergency flatten close-order record",
                operation="flatten_and_stop",
                error=str(exc),
                order_id=str(order_id),
                client_order_id=str(client_order_id),
            )

    def _emergency_close_bot_id(self) -> str:
        return str(self.context.bot_id or self.context.config.profile or "live")

    @staticmethod
    def _emergency_close_store_status(
        status: Any,
        *,
        filled_quantity_known: bool,
    ) -> OrderStatus:
        try:
            store_status = execution_status_for_store(
                status,
                context="emergency_flatten_close_order",
            )
        except ExecutionStatusCodecError:
            return OrderStatus.OPEN
        if not filled_quantity_known and store_status in {
            OrderStatus.FILLED,
            OrderStatus.PARTIALLY_FILLED,
        }:
            return OrderStatus.OPEN
        return store_status

    def _emergency_close_rejection_error(self, order: Any | None) -> str | None:
        broker_status = extract_order_identifier(order, ("status",))
        if broker_status is None:
            return None
        try:
            store_status = execution_status_for_store(
                broker_status,
                context="emergency_flatten_close_order",
            )
        except ExecutionStatusCodecError:
            return f"Broker returned unsupported emergency close status: {broker_status}"
        if store_status in {
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        }:
            return f"Broker returned non-accepted emergency close status: {store_status.value}"
        return None

    async def _notify_flatten_failure(self, details: dict[str, Any]) -> None:
        if self._notification_service is None:
            return
        failed_symbols = details.get("failed_symbols") or ["unknown"]
        try:
            await self._notification_service.notify(
                title="Emergency flatten incomplete",
                message=(
                    "Emergency flatten did not close all positions. Failed symbols: "
                    f"{', '.join(failed_symbols)}. Verify remaining exposure before "
                    "treating flatten as complete."
                ),
                severity=AlertSeverity.CRITICAL,
                source="TradingBot",
                category="emergency_flatten",
                context=details,
                force=True,
            )
        except Exception as exc:
            logger.error(
                "Failed to send emergency flatten failure notification",
                operation="flatten_and_stop",
                error=str(exc),
            )

    async def _place_reduce_only_emergency_close(
        self,
        *,
        symbol: str,
        side: Any,
        order_type: Any,
        quantity: Any,
    ) -> Any:
        if self.broker is None:
            raise RuntimeError("No broker connection available.")

        order_kwargs = {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "reduce_only": True,
        }
        broker_calls = getattr(self, "_broker_calls", None)

        try:
            if broker_calls is not None and asyncio.iscoroutinefunction(
                getattr(broker_calls, "__call__", None)
            ):
                return await broker_calls(self.broker.place_order, **order_kwargs)
            return await asyncio.to_thread(self.broker.place_order, **order_kwargs)
        except TypeError as exc:
            if not self._is_unsupported_reduce_only_type_error(exc):
                raise
            raise RuntimeError(
                "Broker rejected reduce-only emergency close order; "
                "refusing non-reduce-only fallback."
            ) from exc

    @staticmethod
    def _is_unsupported_reduce_only_type_error(exc: TypeError) -> bool:
        message = str(exc).lower()
        return "reduce_only" in message and (
            "unexpected keyword" in message or "unexpected argument" in message
        )

    @staticmethod
    def _emergency_close_side(position: Any, order_side: Any) -> Any:
        position_side = getattr(position, "side", None)
        side_value = getattr(position_side, "value", position_side)
        if isinstance(side_value, str):
            normalized_side = side_value.strip().lower()
            if normalized_side in {"long", "buy"}:
                return order_side.SELL
            if normalized_side in {"short", "sell"}:
                return order_side.BUY
        return order_side.SELL if position.quantity > 0 else order_side.BUY

    async def shutdown(self) -> None:
        """Alias for stop() to match CLI interface."""
        await self.stop()

    def _shutdown_broker_calls(self) -> None:
        broker_calls = getattr(self, "_broker_calls", None)
        if broker_calls is not None:
            shutdown = getattr(broker_calls, "shutdown", None)
            if callable(shutdown):
                shutdown()

    def _preserve_engine_broker_calls_on_shutdown(self) -> None:
        preserve = getattr(self.engine, "preserve_broker_calls_on_shutdown", None)
        if callable(preserve):
            result = preserve()
            if inspect.isawaitable(result):
                close = getattr(result, "close", None)
                if callable(close):
                    close()

    def execute_decision(
        self,
        symbol: str,
        decision: Any,
        mark: Any = None,
        product: Any = None,
        position_state: Any = None,
    ) -> Any:
        """Execute a trading decision. Stub for interface compatibility."""
        if hasattr(self.engine, "execute_decision"):
            return self.engine.execute_decision(symbol, decision, mark, product, position_state)
        return None

    def get_product(self, symbol: str) -> Product | None:
        """Get product metadata for a symbol."""
        if self.broker and hasattr(self.broker, "get_product"):
            return self.broker.get_product(symbol)
        return None


__all__ = ["TradingBot"]
