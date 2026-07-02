"""
Simplified Strategy Engine.
Replaces the 608-line enterprise coordinator with a simple loop.

State Recovery:
On startup, reads `price_tick` events from EventStore to restore price history.
During operation, persists price ticks to EventStore for crash recovery.

Streaming Lifecycle:
When enabled, starts WebSocket streaming for real-time market data.
Includes WS health watchdog that monitors staleness and triggers degradation.
"""

import asyncio
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
from decimal import Decimal
from importlib import metadata
from typing import Any

from gpt_trader.app.health_server import HealthState
from gpt_trader.app.runtime.fingerprint import (
    compare_startup_config_fingerprints,
    load_startup_config_fingerprint,
)
from gpt_trader.config.constants import HEALTH_CHECK_INTERVAL_SECONDS
from gpt_trader.core import OrderSide, OrderType, Position, Product
from gpt_trader.features.live_trade.degradation import DegradationState
from gpt_trader.features.live_trade.engines.base import (
    BaseEngine,
    CoordinatorContext,
    HealthStatus,
)
from gpt_trader.features.live_trade.engines.cycle_runner import run_cycle
from gpt_trader.features.live_trade.engines.decision_flow import (
    handle_decision,
    process_symbol,
)
from gpt_trader.features.live_trade.engines.equity_calculator import (
    EquityCalculator,
)
from gpt_trader.features.live_trade.engines.order_audit import OrderAuditService
from gpt_trader.features.live_trade.engines.order_reconciliation import (
    OrderReconciliationService,
)
from gpt_trader.features.live_trade.engines.order_record_mapping import (
    build_record_from_broker_order,
    get_order_field,
    merge_metadata,
    normalize_persisted_status,
    parse_decimal,
    parse_decimal_optional,
    parse_timestamp,
)
from gpt_trader.features.live_trade.engines.position_formatting import (
    build_position_state,
    positions_to_risk_format,
    positions_to_status_format,
    resolve_close_order,
)
from gpt_trader.features.live_trade.engines.price_tick_store import (
    EVENT_PRICE_TICK,
    PriceTickStore,
)
from gpt_trader.features.live_trade.engines.runtime.coordinator import RuntimeEngine
from gpt_trader.features.live_trade.engines.runtime.models import (
    RuntimeDependency,
    RuntimeLifecycleError,
    RuntimeLifecyclePlan,
    RuntimeLifecycleStep,
    RuntimeStepKind,
    RuntimeStopCondition,
)
from gpt_trader.features.live_trade.engines.system_maintenance import (
    SystemMaintenanceService,
)
from gpt_trader.features.live_trade.engines.telemetry_health import (
    extract_mark_from_message,
    update_mark_and_metrics,
)
from gpt_trader.features.live_trade.engines.telemetry_streaming import (
    _handle_stream_task_completion,
    _run_stream_loop,
    _run_stream_loop_async,
    _schedule_coroutine,
    _should_enable_streaming,
    _start_streaming,
    _stop_streaming,
)
from gpt_trader.features.live_trade.engines.ws_health_monitor import monitor_ws_health
from gpt_trader.features.live_trade.execution.decision_trace import OrderDecisionTrace
from gpt_trader.features.live_trade.execution.guard_manager import GuardManager
from gpt_trader.features.live_trade.execution.order_submission import OrderSubmitter
from gpt_trader.features.live_trade.execution.state_collection import StateCollector
from gpt_trader.features.live_trade.execution.submission_result import (
    OrderSubmissionResult,
    OrderSubmissionStatus,
)
from gpt_trader.features.live_trade.execution.validation import OrderValidator
from gpt_trader.features.live_trade.factory import create_strategy
from gpt_trader.features.live_trade.guard_errors import GuardError
from gpt_trader.features.live_trade.lifecycle import (
    ENGINE_TRANSITIONS,
    EngineState,
    LifecycleStateMachine,
)
from gpt_trader.features.live_trade.risk.manager import ValidationError
from gpt_trader.features.live_trade.strategies.perps_baseline import (
    Action,
    Decision,
)
from gpt_trader.features.strategy_tools import (
    StrategySignalToTradeIdeaAdapter,
    StrategySignalToTradeIdeaAdapterConfig,
)
from gpt_trader.features.trade_ideas import TradeIdeaService, create_trade_idea_service
from gpt_trader.monitoring.alert_types import AlertSeverity
from gpt_trader.monitoring.feature_seeds import build_feature_seed, summarize_seed_reason
from gpt_trader.monitoring.health_checks import HealthCheckRunner
from gpt_trader.monitoring.heartbeat import HeartbeatService
from gpt_trader.monitoring.metrics_collector import record_histogram, record_trade_blocked
from gpt_trader.monitoring.profiling import profile_span
from gpt_trader.monitoring.status_reporter import StatusReporter
from gpt_trader.persistence.event_store import EventStore
from gpt_trader.persistence.orders_store import (
    OrderRecord,
)
from gpt_trader.persistence.orders_store import (
    OrderStatus as PersistedOrderStatus,
)
from gpt_trader.utilities.async_tools import BoundedToThread
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="trading_engine")

# Consecutive order reconciliation drift detections required before triggering
# an escalation (pause + reduce-only). Keep this conservative to avoid
# flapping on transient list_orders inconsistencies.
ORDER_RECONCILIATION_DRIFT_MAX_FAILURES = 3

# Re-export for backward compatibility
__all__ = ["TradingEngine", "EVENT_PRICE_TICK"]


class TradingEngine(BaseEngine):
    """
    Simple trading loop that fetches data and executes strategy.

    Supports state recovery via EventStore persistence.
    """

    def __init__(self, context: CoordinatorContext) -> None:
        super().__init__(context)
        self._lifecycle: LifecycleStateMachine[EngineState] = LifecycleStateMachine(
            initial_state=EngineState.INIT,
            entity="trading_engine",
            transitions=ENGINE_TRANSITIONS,
            logger=logger,
        )
        # Create strategy via factory (supports baseline and mean_reversion)
        self.strategy = create_strategy(self.context.config)
        self._current_positions: dict[str, Position] = {}
        self._rehydrated = False
        self._cycle_count = 0

        # Initialize price tick store for state recovery
        self._price_tick_store = PriceTickStore(
            event_store=context.event_store,
            symbols=list(context.config.symbols),
            bot_id=context.bot_id,
        )

        # Initialize heartbeat service
        self._heartbeat = HeartbeatService(
            event_store=context.event_store,
            ping_url=getattr(context.config, "heartbeat_url", None),
            interval_seconds=getattr(context.config, "heartbeat_interval", 60),
            bot_id=context.bot_id,
            enabled=getattr(context.config, "heartbeat_enabled", True),
        )

        # Initialize status reporter
        self._status_reporter = StatusReporter(
            status_file=getattr(context.config, "status_file", "status.json"),
            file_write_interval=getattr(context.config, "status_interval", 60),
            bot_id=context.bot_id,
            enabled=getattr(context.config, "status_enabled", True),
        )
        self._status_reporter.set_heartbeat_service(self._heartbeat)

        # Initialize system maintenance service (health reporting + pruning)
        self._system_maintenance = SystemMaintenanceService(
            status_reporter=self._status_reporter,
            event_store=context.event_store,
        )

        # System health tracking
        self._last_latency = 0.0
        self._connection_status = "UNKNOWN"

        # Initialize graceful degradation state
        self._degradation = DegradationState()

        broker_calls = getattr(context, "broker_calls", None)
        if broker_calls is not None and not asyncio.iscoroutinefunction(
            getattr(broker_calls, "__call__", None)
        ):
            broker_calls = None
        if broker_calls is None:
            broker_call_limit = getattr(context.config, "max_concurrent_broker_calls", None)
            if broker_call_limit is None:
                broker_call_limit = getattr(context.config, "max_concurrent_rest_calls", 5)
            try:
                raw_limit = broker_call_limit if broker_call_limit is not None else 5
                broker_call_limit = int(raw_limit)
            except (TypeError, ValueError):
                broker_call_limit = 5
            broker_call_limit = max(1, broker_call_limit)
            use_dedicated_executor = (
                getattr(context.config, "broker_calls_use_dedicated_executor", False) is True
            )
            broker_calls = BoundedToThread(
                max_concurrency=broker_call_limit,
                use_dedicated_executor=use_dedicated_executor,
            )

        self._broker_calls = broker_calls
        self._preserve_broker_calls_on_shutdown = False

        # Initialize equity calculator (extracted for reusability)
        self._equity_calculator = EquityCalculator(
            config=context.config,
            degradation=self._degradation,
            risk_manager=context.risk_manager,
            price_history=self._price_tick_store.price_history,
            broker_calls=self._broker_calls,
        )

        # Initialize health check runner for active /health probes
        health_state = context.container.health_state if context.container else HealthState()
        market_data_service = None
        if context.container is not None:
            market_data_service = getattr(context.container, "market_data_service", None)
        if market_data_service is not None:
            self._status_reporter.set_market_data_service(market_data_service)
        self._health_check_runner = HealthCheckRunner(
            health_state=health_state,
            broker=context.broker,
            degradation_state=self._degradation,
            risk_manager=context.risk_manager,
            market_data_service=market_data_service,
            interval_seconds=HEALTH_CHECK_INTERVAL_SECONDS,
            message_stale_seconds=getattr(context.config, "ws_message_stale_seconds", 60.0),
            heartbeat_stale_seconds=getattr(context.config, "ws_heartbeat_stale_seconds", 120.0),
            broker_calls=self._broker_calls,
        )

        # Initialize streaming lifecycle attributes
        self._ws_stop: threading.Event | None = None
        self._pending_stream_config: tuple[list[str], int] | None = None
        self._stream_task: asyncio.Task[Any] | None = None
        self._loop_task_handle: asyncio.Task[Any] | None = None
        self._market_monitor: Any = None  # Market monitor for telemetry

        # WS health watchdog attributes
        self._ws_health_task: asyncio.Task[Any] | None = None
        self._ws_reconnect_attempts: int = 0
        self._ws_reconnect_delay: float = 1.0
        self._ws_last_health_check: float = 0.0

        # Initialize pre-trade guard stack (Option A: embedded guards)
        self._init_guard_stack()
        self._user_event_handler: Any | None = None
        self._init_user_event_handler()
        self._runtime_engine = RuntimeEngine(
            context,
            shutdown_timeout_seconds=getattr(
                context.config,
                "runtime_shutdown_timeout_seconds",
                5.0,
            ),
        )

        # Stage 1 human-approved loop: default-off bridge that routes live
        # strategy decisions into the approval-gated trade-idea workflow instead
        # of the broker. Built only when the gate is on so the disabled path has
        # zero overhead and no filesystem side effects.
        self._trade_idea_service: TradeIdeaService | None = None
        self._strategy_proposal_adapter: StrategySignalToTradeIdeaAdapter | None = None
        self._init_strategy_proposal_bridge()

    def _init_strategy_proposal_bridge(self) -> None:
        """Wire the default-off strategy-signal-to-trade-idea proposal bridge.

        When ``strategy_signal_proposals_enabled`` is set, live decisions are
        proposed for human review through ``TradeIdeaService.propose()`` and the
        engine never submits orders (proposal-only mode). When unset, both
        collaborators stay ``None`` and execution behaves exactly as before.
        """
        if not getattr(self.context.config, "strategy_signal_proposals_enabled", False):
            return
        self._trade_idea_service = create_trade_idea_service()
        self._strategy_proposal_adapter = StrategySignalToTradeIdeaAdapter(
            StrategySignalToTradeIdeaAdapterConfig(enabled=True)
        )
        logger.warning(
            "Strategy-signal proposal mode ENABLED: live decisions route to the "
            "approval-gated trade-idea workflow; the engine will not submit orders",
            operation="strategy_proposal",
            stage="enabled",
        )

    def preserve_broker_calls_on_shutdown(self) -> None:
        self._preserve_broker_calls_on_shutdown = True

    def _init_guard_stack(self) -> None:
        """Initialize StateCollector, OrderValidator, OrderSubmitter for pre-trade guards."""
        # Event store fallback
        event_store = self.context.event_store or EventStore()
        self._event_store = event_store
        bot_id = str(self.context.bot_id or self.context.config.profile or "live")

        # Orders store for durable restart (optional)
        orders_store = self.context.orders_store
        if orders_store is None and self.context.container is not None:
            orders_store = getattr(self.context.container, "orders_store", None)
        if orders_store is not None:
            try:
                orders_store.initialize()
            except Exception as exc:
                logger.warning(
                    "Failed to initialize orders store",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="orders_store_init",
                )
                orders_store = None
        self._orders_store = orders_store

        # Broker and risk manager must exist
        broker = self.context.broker
        risk_manager = self.context.risk_manager

        # Track open orders
        self._open_orders: list[str] = []
        self._rehydrate_open_orders()

        # StateCollector: needs broker, config
        self._state_collector = StateCollector(
            broker=broker,  # type: ignore[arg-type]
            config=self.context.config,
            integration_mode=False,
        )

        # OrderSubmitter: broker + event store + bot_id + open_orders
        self._order_submitter = OrderSubmitter(
            broker=broker,  # type: ignore[arg-type]
            event_store=event_store,
            bot_id=bot_id,
            open_orders=self._open_orders,
            enable_retries=getattr(self.context.config, "order_submission_retries_enabled", False),
            orders_store=self._orders_store,
            integration_mode=False,
        )
        self._order_reconciliation = OrderReconciliationService(
            context=self.context,
            open_orders=self._open_orders,
            orders_store_provider=lambda: self._orders_store,
            broker_calls_provider=lambda: self._broker_calls,
            degradation=self._degradation,
            order_submitter=self._order_submitter,
            append_event=self._append_event,
            notify=self._notify,
            drift_max_failures=ORDER_RECONCILIATION_DRIFT_MAX_FAILURES,
        )
        self._order_audit = OrderAuditService(
            context=self.context,
            broker_calls_provider=lambda: self._broker_calls,
            status_reporter=self._status_reporter,
            reconciliation=self._order_reconciliation,
            append_event=self._append_event,
            cycle_count_provider=lambda: self._cycle_count,
        )

        # Failure tracker from container (not global) with escalation callback
        container = self.context.container
        if container is None:
            raise RuntimeError(
                "TradingEngine requires a container in context. "
                "Pass container=ApplicationContainer(config) to CoordinatorContext."
            )
        failure_tracker = container.validation_failure_tracker

        # Wire escalation callback: on repeated validation failures, pause + reduce-only
        def _on_validation_escalation() -> None:
            """Handle validation infrastructure failures by pausing and setting reduce-only."""
            if risk_manager is None:
                return

            risk_manager.set_reduce_only_mode(True, reason="validation_failures")
            cooldown = 180
            if risk_manager.config is not None:
                cooldown = risk_manager.config.validation_failure_cooldown_seconds
            self._degradation.pause_all(
                seconds=cooldown,
                reason="validation_failures",
                allow_reduce_only=True,
            )
            logger.warning(
                "Validation escalation triggered - pausing trading",
                cooldown_seconds=cooldown,
                operation="degradation",
                stage="validation_escalation",
            )

        failure_tracker.escalation_callback = _on_validation_escalation

        # OrderValidator: broker + risk_manager + preview config + callbacks + tracker
        self._order_validator: OrderValidator | None = None
        if risk_manager is not None:
            self._order_validator = OrderValidator(
                broker=broker,  # type: ignore[arg-type]
                risk_manager=risk_manager,
                enable_order_preview=self.context.config.enable_order_preview,
                record_preview_callback=self._order_submitter.record_preview,
                record_rejection_callback=self._order_submitter.record_rejection,
                failure_tracker=failure_tracker,
                broker_calls=self._broker_calls,
            )

        # GuardManager: runtime guards (daily loss, liquidation buffer, volatility)
        self._guard_manager: GuardManager | None = None
        if broker is not None and risk_manager is not None:
            self._guard_manager = GuardManager(
                broker=broker,  # type: ignore[arg-type]
                risk_manager=risk_manager,
                equity_calculator=self._state_collector.calculate_equity_from_balances,
                open_orders=self._open_orders,
                invalidate_cache_callback=lambda: None,
                cancel_retries_enabled=getattr(
                    self.context.config, "order_submission_retries_enabled", False
                ),
            )

    def _init_user_event_handler(self) -> None:
        """Initialize Coinbase WS user-event handling for live order updates."""
        if getattr(self.context.config, "dry_run", False):
            logger.info(
                "Dry-run enabled; skipping Coinbase user-event handling",
                operation="user_events",
                stage="skip",
            )
            return

        broker = self.context.broker
        if broker is None:
            return

        module_name = getattr(broker, "__module__", "")
        if "coinbase" not in module_name:
            return

        from gpt_trader.features.brokerages.coinbase.user_event_handler import (
            CoinbaseUserEventHandler,
        )

        market_data_service = None
        product_catalog = None
        container = self.context.container
        if container is not None:
            market_data_service = getattr(container, "market_data_service", None)
            product_catalog = getattr(container, "product_catalog", None)

        self._user_event_handler = CoinbaseUserEventHandler(
            broker=broker,
            orders_store=self._orders_store,
            event_store=self.context.event_store,
            bot_id=str(self.context.bot_id or self.context.config.profile or "live"),
            market_data_service=market_data_service,
            symbols=list(self.context.config.symbols),
            product_catalog=product_catalog,
        )

    def _rehydrate_open_orders(self) -> None:
        """Restore open order IDs from the orders store after a restart."""
        if self._orders_store is None:
            return
        bot_id = str(self.context.bot_id or self.context.config.profile or "")
        try:
            pending = self._orders_store.get_pending_orders(bot_id=bot_id or None)
        except Exception as exc:
            logger.warning(
                "Failed to rehydrate open orders",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="order_recovery",
            )
            return

        if not pending:
            return

        seen: set[str] = set()
        order_ids: list[str] = []
        for order in pending:
            if order.order_id in seen:
                continue
            seen.add(order.order_id)
            order_ids.append(order.order_id)

        self._open_orders[:] = order_ids
        logger.info(
            "Rehydrated open orders from persistence",
            count=len(order_ids),
            operation="order_recovery",
        )

    async def _recover_unknown_bot_orders(
        self,
        orders: list[Any],
        *,
        bot_id: str,
    ) -> list[OrderRecord]:
        return await self._order_reconciliation.recover_unknown_bot_orders(
            orders,
            bot_id=bot_id,
        )

    async def _refresh_missing_persisted_orders(
        self,
        records: list[OrderRecord],
        *,
        bot_id: str,
    ) -> list[Any]:
        return await self._order_reconciliation.refresh_missing_persisted_orders(
            records,
            bot_id=bot_id,
        )

    async def _reconcile_open_orders(self, orders: list[Any]) -> None:
        """Reconcile internal open-order tracking with broker + persistence state.

        This addresses a crash-recovery edge case: orders are persisted as pending with
        ``order_id == client_order_id == submit_id`` before the broker responds with
        the canonical broker order ID. After restart, rehydration can restore the
        submit_id into ``self._open_orders``. On the next audit, we normalize tracked
        IDs back to broker order IDs when the broker returns both IDs.
        """
        await self._order_reconciliation.reconcile_open_orders(orders)

    async def _handle_order_reconciliation_drift(self, order_ids: list[str]) -> None:
        """Escalate persistent order-reconciliation drift to graceful degradation."""
        await self._order_reconciliation.handle_order_reconciliation_drift(order_ids)

    # =========================================================================
    # Streaming Lifecycle Methods
    # =========================================================================

    def _should_enable_streaming(self) -> bool:
        """Check if streaming should be enabled based on config."""
        return _should_enable_streaming(self)

    def _schedule_coroutine(self, coro: Any) -> None:
        """Schedule a coroutine for execution."""
        _schedule_coroutine(self, coro)

    async def _start_streaming(self) -> asyncio.Task[Any] | None:
        """Start WebSocket streaming."""
        return await _start_streaming(self)

    async def _stop_streaming(self) -> None:
        """Stop WebSocket streaming."""
        await _stop_streaming(self)

    def _handle_stream_task_completion(self, task: asyncio.Task[Any]) -> None:
        """Handle stream task completion callback."""
        _handle_stream_task_completion(self, task)

    async def _run_stream_loop_async(
        self,
        symbols: list[str],
        level: int,
        stop_signal: threading.Event | None,
    ) -> None:
        """Run streaming loop asynchronously."""
        await _run_stream_loop_async(self, symbols, level, stop_signal)

    def _run_stream_loop(
        self,
        symbols: list[str],
        level: int,
        stop_signal: threading.Event | None,
    ) -> None:
        """Run streaming loop synchronously (called from executor)."""
        _run_stream_loop(self, symbols, level, stop_signal)

    def _extract_mark_from_message(self, msg: dict[str, Any]) -> Decimal | None:
        """Extract mark price from WebSocket message."""
        return extract_mark_from_message(msg)

    def _update_mark_and_metrics(
        self,
        ctx: CoordinatorContext,
        symbol: str,
        mark: Decimal,
    ) -> None:
        """Update mark price and related metrics."""
        update_mark_and_metrics(self, ctx, symbol, mark)

    @property
    def status_reporter(self) -> StatusReporter:
        return self._status_reporter

    @property
    def price_history(self) -> dict[str, deque[Decimal]]:
        """Access price history via PriceTickStore."""
        return self._price_tick_store.price_history

    async def _notify(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Send notification if service is available."""
        if self.context.notification_service is None:
            return
        try:
            await self.context.notification_service.notify(
                title=title,
                message=message,
                severity=severity,
                source="TradingEngine",
                context=context,
            )
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    @property
    def name(self) -> str:
        return "strategy"

    @property
    def state(self) -> EngineState:
        return self._lifecycle.state

    @property
    def running(self) -> bool:
        return self.state in (EngineState.STARTING, EngineState.RUNNING)

    @running.setter
    def running(self, value: bool) -> None:
        target = EngineState.RUNNING if value else EngineState.STOPPED
        self._lifecycle.transition(
            target,
            reason="running_override",
            details={"via": "running_set"},
            force=True,
        )

    def _transition_state(
        self,
        target: EngineState,
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

    def _runtime_lifecycle_plan(self) -> RuntimeLifecyclePlan:
        shutdown_timeout_seconds = getattr(
            self.context.config,
            "runtime_shutdown_timeout_seconds",
            5.0,
        )
        return RuntimeLifecyclePlan(
            dependencies=(
                RuntimeDependency("config", self.context.config),
                RuntimeDependency("container", self.context.container),
                RuntimeDependency("broker", self.context.broker),
                RuntimeDependency("risk_manager", self.context.risk_manager, required=False),
                RuntimeDependency("event_store", self.context.event_store, required=False),
                RuntimeDependency("orders_store", self.context.orders_store, required=False),
                RuntimeDependency(
                    "notification_service",
                    self.context.notification_service,
                    required=False,
                ),
            ),
            stop_conditions=(
                RuntimeStopCondition(
                    name="engine_error_state",
                    is_met=lambda: self.state == EngineState.ERROR,
                    reason="trading_engine_error",
                ),
            ),
            startup_steps=(
                RuntimeLifecycleStep(
                    name="engine_state_starting",
                    kind=RuntimeStepKind.STARTUP_HOOK,
                    callback=self._runtime_mark_starting,
                ),
                RuntimeLifecycleStep(
                    name="price_history_rehydrate",
                    kind=RuntimeStepKind.STARTUP_HOOK,
                    callback=self._runtime_rehydrate_once,
                ),
                RuntimeLifecycleStep(
                    name="runtime_start_event",
                    kind=RuntimeStepKind.STARTUP_HOOK,
                    callback=self._record_runtime_start,
                ),
                RuntimeLifecycleStep(
                    name="engine_state_running",
                    kind=RuntimeStepKind.STARTUP_HOOK,
                    callback=self._runtime_mark_running,
                ),
                RuntimeLifecycleStep(
                    name="trading_loop",
                    kind=RuntimeStepKind.BACKGROUND_TASK,
                    callback=self._start_trading_loop_task,
                ),
                RuntimeLifecycleStep(
                    name="heartbeat_service",
                    kind=RuntimeStepKind.HEARTBEAT,
                    callback=self._heartbeat.start,
                ),
                RuntimeLifecycleStep(
                    name="status_reporter",
                    kind=RuntimeStepKind.HEALTH,
                    callback=self._status_reporter.start,
                ),
                RuntimeLifecycleStep(
                    name="health_check_runner",
                    kind=RuntimeStepKind.HEALTH,
                    callback=self._health_check_runner.start,
                ),
                RuntimeLifecycleStep(
                    name="system_maintenance_prune",
                    kind=RuntimeStepKind.HEALTH,
                    callback=self._system_maintenance.start_prune_loop,
                ),
                RuntimeLifecycleStep(
                    name="runtime_guard_checkpoint",
                    kind=RuntimeStepKind.POLICY_CHECKPOINT,
                    callback=self._runtime_guard_checkpoint,
                    register_task=False,
                ),
                RuntimeLifecycleStep(
                    name="runtime_guard_sweep",
                    kind=RuntimeStepKind.POLICY_CHECKPOINT,
                    callback=self._start_runtime_guard_task,
                ),
                RuntimeLifecycleStep(
                    name="streaming",
                    kind=RuntimeStepKind.STREAMING,
                    callback=self._runtime_start_streaming,
                ),
                RuntimeLifecycleStep(
                    name="ws_health_watchdog",
                    kind=RuntimeStepKind.HEALTH,
                    callback=self._start_ws_health_watchdog_task,
                ),
            ),
            shutdown_steps=(
                RuntimeLifecycleStep(
                    name="ws_health_watchdog",
                    kind=RuntimeStepKind.SHUTDOWN_HOOK,
                    callback=self._stop_ws_health_watchdog_task,
                    timeout_seconds=shutdown_timeout_seconds,
                    register_task=False,
                ),
                RuntimeLifecycleStep(
                    name="streaming",
                    kind=RuntimeStepKind.STREAMING,
                    callback=self._runtime_stop_streaming,
                    timeout_seconds=shutdown_timeout_seconds,
                    register_task=False,
                ),
                RuntimeLifecycleStep(
                    name="health_check_runner",
                    kind=RuntimeStepKind.HEALTH,
                    callback=self._health_check_runner.stop,
                    timeout_seconds=shutdown_timeout_seconds,
                    register_task=False,
                ),
                RuntimeLifecycleStep(
                    name="system_maintenance",
                    kind=RuntimeStepKind.SHUTDOWN_HOOK,
                    callback=self._system_maintenance.stop,
                    timeout_seconds=shutdown_timeout_seconds,
                    register_task=False,
                ),
                RuntimeLifecycleStep(
                    name="status_reporter",
                    kind=RuntimeStepKind.HEALTH,
                    callback=self._status_reporter.stop,
                    timeout_seconds=shutdown_timeout_seconds,
                    register_task=False,
                ),
                RuntimeLifecycleStep(
                    name="heartbeat_service",
                    kind=RuntimeStepKind.HEARTBEAT,
                    callback=self._heartbeat.stop,
                    timeout_seconds=shutdown_timeout_seconds,
                    register_task=False,
                ),
                RuntimeLifecycleStep(
                    name="broker_call_executor",
                    kind=RuntimeStepKind.SHUTDOWN_HOOK,
                    callback=self._runtime_shutdown_broker_calls,
                    register_task=False,
                ),
            ),
            task_cleanup_timeout_seconds=shutdown_timeout_seconds,
            shutdown_step_timeout_seconds=shutdown_timeout_seconds,
        )

    def _runtime_mark_starting(self) -> None:
        self._transition_state(EngineState.STARTING, reason="start_background_tasks")

    def _runtime_rehydrate_once(self) -> None:
        if not self._rehydrated:
            self._rehydrate_from_events()
            self._rehydrated = True

    def _runtime_mark_running(self) -> None:
        self._transition_state(EngineState.RUNNING, reason="tasks_scheduled")

    def _start_trading_loop_task(self) -> asyncio.Task[Any]:
        return asyncio.create_task(self._run_loop(), name="trading_loop")

    def _runtime_guard_checkpoint(self) -> None:
        logger.info(
            "Runtime policy checkpoint reached",
            guard_manager_available=self._guard_manager is not None,
            order_validator_available=self._order_validator is not None,
            operation="runtime_lifecycle",
            stage="policy_checkpoint",
        )

    def _start_runtime_guard_task(self) -> asyncio.Task[Any] | None:
        if self._guard_manager is not None:
            return asyncio.create_task(self._runtime_guard_sweep(), name="runtime_guard_sweep")
        return None

    async def _runtime_start_streaming(self) -> asyncio.Task[Any] | None:
        if self._should_enable_streaming():
            task = await self._start_streaming()
            logger.info(
                "Started WebSocket streaming",
                operation="streaming",
                stage="start",
            )
            return task
        return None

    def _start_ws_health_watchdog_task(self) -> asyncio.Task[Any]:
        self._ws_health_task = asyncio.create_task(
            self._monitor_ws_health(), name="ws_health_watchdog"
        )
        logger.info(
            "Started WS health watchdog",
            operation="ws_health",
            stage="start",
        )
        return self._ws_health_task

    async def start_background_tasks(self) -> list[asyncio.Task[Any]]:
        """Start runtime background tasks through the lifecycle coordinator.

        Before starting, attempts to rehydrate state from EventStore.
        """
        try:
            return await self._runtime_engine.start(
                self._runtime_lifecycle_plan(),
                register_task=self._register_background_task,
            )
        except RuntimeLifecycleError:
            self._transition_state(EngineState.ERROR, reason="runtime_start_failed")
            raise

    def _rehydrate_from_events(self) -> int:
        """Restore price history from persisted events.

        Delegates to PriceTickStore for the actual rehydration logic.

        Returns:
            Number of price ticks restored
        """
        # Prepare strategy rehydration callback if strategy supports it
        strategy_callback = None
        if hasattr(self.strategy, "rehydrate"):
            strategy_callback = self.strategy.rehydrate

        return self._price_tick_store.rehydrate(strategy_rehydrate_callback=strategy_callback)

    def _record_runtime_start(self) -> None:
        event_store = getattr(self, "_event_store", None)
        if event_store is None:
            return

        try:
            package_version = metadata.version("gpt-trader")
        except metadata.PackageNotFoundError:
            package_version = None

        container = self.context.container
        fingerprint = container.startup_config_fingerprint if container is not None else None
        payload = {
            "timestamp": time.time(),
            "profile": str(self.context.config.profile or ""),
            "bot_id": self.context.bot_id or None,
            "build_sha": os.getenv("GPT_TRADER_BUILD_SHA"),
            "package_version": package_version,
            "python_version": sys.version.split()[0],
            "pid": os.getpid(),
            "config_digest": fingerprint.digest if fingerprint is not None else None,
        }

        fingerprint_path = (
            container.runtime_paths.config_fingerprint_path if container is not None else None
        )
        if fingerprint_path is None:
            logger.debug(
                "No startup config fingerprint runtime path available",
                operation="runtime_start",
            )
        else:
            expected = load_startup_config_fingerprint(fingerprint_path)
            if expected is not None:
                match, reason = compare_startup_config_fingerprints(expected, fingerprint)
                if match:
                    logger.debug(
                        "Startup config fingerprint validated",
                        operation="runtime_start",
                        config_digest=fingerprint.digest if fingerprint is not None else None,
                    )
                else:
                    logger.warning(
                        "Startup config fingerprint mismatch",
                        operation="runtime_start",
                        config_digest=fingerprint.digest if fingerprint is not None else None,
                        reason=reason,
                    )
            elif fingerprint_path.exists():
                logger.warning(
                    "Startup config fingerprint file unreadable",
                    operation="runtime_start",
                    path=fingerprint_path,
                )
            else:
                logger.debug(
                    "No startup config fingerprint file available",
                    operation="runtime_start",
                    path=fingerprint_path,
                )
        try:
            event_store.append("runtime_start", payload)
        except Exception:
            logger.exception("Failed to record runtime_start", operation="runtime_start")

    async def _runtime_guard_sweep(self) -> None:
        """Periodically run runtime guards to check risk limits.

        Runs on a cadence to proactively detect risk breaches (daily loss,
        liquidation buffer, volatility) rather than only at order time.

        On guard failure, triggers graceful degradation (pause + reduce-only).
        """
        interval = getattr(self.context.config, "runtime_guard_interval", 60)
        while self.running:
            try:
                if self._guard_manager is not None:
                    # Use run_runtime_guards directly to catch GuardError for degradation
                    state = await self._broker_calls(self._guard_manager.run_runtime_guards)
                    self._record_guard_events(state.guard_events)

            except GuardError as err:
                # Trigger graceful degradation on guard failure
                await self._handle_guard_failure(err)

            except Exception:
                logger.exception("Runtime guard sweep failed", operation="runtime_guards")

            await asyncio.sleep(interval)

    async def _handle_guard_failure(self, err: GuardError) -> None:
        """Handle guard failure by triggering graceful degradation."""
        risk_manager = self.context.risk_manager
        config = risk_manager.config if risk_manager else None
        self._record_guard_failure_event(err)

        # Determine cooldown from config
        cooldown_seconds = 300  # Default 5 minutes
        if config is not None:
            cooldown_seconds = config.api_health_cooldown_seconds

        # Set reduce-only mode
        if risk_manager is not None:
            risk_manager.set_reduce_only_mode(True, reason=f"guard_failure:{err.guard_name}")

        # Cancel all open orders
        if self._guard_manager is not None:
            cancelled = await self._broker_calls(self._guard_manager.cancel_all_orders)
            logger.warning(
                "Guard failure triggered order cancellation",
                guard_name=err.guard_name,
                cancelled_orders=cancelled,
                operation="degradation",
                stage="cancel_orders",
            )

        # Pause all trading
        self._degradation.pause_all(
            seconds=cooldown_seconds,
            reason=f"guard_failure:{err.guard_name}",
            allow_reduce_only=True,
        )

        # Record rejection for telemetry
        self._order_submitter.record_rejection(
            symbol="*",
            side="*",
            quantity=Decimal("0"),
            price=None,
            reason=f"guard_failure:{err.guard_name}",
        )

        # Notify
        await self._notify(
            title="Guard Failure - Trading Paused",
            message=f"Runtime guard '{err.guard_name}' failed: {err.message}. "
            f"Trading paused for {cooldown_seconds}s. Reduce-only mode activated.",
            severity=AlertSeverity.ERROR,
            context={
                "guard_name": err.guard_name,
                "message": err.message,
                "cooldown_seconds": cooldown_seconds,
                "recoverable": err.recoverable,
            },
        )

    async def _monitor_ws_health(self) -> None:
        """Monitor WebSocket health and trigger degradation on staleness."""
        await monitor_ws_health(self)

    async def _run_loop(self) -> None:
        logger.info("Starting strategy loop...")
        while self.running:
            try:
                await self._cycle()
                # Record successful cycle
                self._status_reporter.record_cycle()
            except Exception as e:
                logger.error(f"Error in strategy cycle: {e}", exc_info=True)
                # Record error in status reporter
                self._status_reporter.record_error(str(e))
                await self._notify(
                    title="Strategy Cycle Error",
                    message=f"Error during trading cycle: {e}",
                    severity=AlertSeverity.ERROR,
                    context={"error": str(e)},
                )

            await asyncio.sleep(self.context.config.interval)

    async def _cycle(self) -> None:
        """One trading cycle."""
        await run_cycle(self)

    async def _process_symbol(
        self,
        *,
        symbol: str,
        broker: Any,
        ticker: dict[str, Any] | None,
        positions: dict[str, Position],
        equity: Decimal,
    ) -> None:
        await process_symbol(
            self,
            symbol=symbol,
            broker=broker,
            ticker=ticker,
            positions=positions,
            equity=equity,
        )

    async def _handle_decision(
        self,
        *,
        symbol: str,
        decision: Decision,
        price: Decimal,
        equity: Decimal,
        position_state: dict[str, Any] | None,
    ) -> None:
        await handle_decision(
            self,
            symbol=symbol,
            decision=decision,
            price=price,
            equity=equity,
            position_state=position_state,
        )

    async def _fetch_total_equity(self, positions: dict[str, Position]) -> Decimal | None:
        """Fetch total equity = collateral + unrealized PnL."""
        return await self._equity_calculator.calculate_total_equity(self.context.broker, positions)

    async def _fetch_positions(self) -> dict[str, Position]:
        """Fetch current positions as a lookup dict."""
        assert self.context.broker is not None
        start_time = time.perf_counter()
        result = "ok"
        try:
            positions_list = await self._broker_calls(self.context.broker.list_positions)
            # Success: reset broker failure counter
            self._degradation.reset_broker_failures()
            return {p.symbol: p for p in positions_list}
        except Exception as e:
            result = "error"
            logger.error(f"Failed to fetch positions: {e}")
            # Track broker failure for degradation
            config = self.context.risk_manager.config if self.context.risk_manager else None
            if config is not None:
                self._degradation.record_broker_failure(config)
            return {}
        finally:
            duration = time.perf_counter() - start_time
            record_histogram(
                "gpt_trader_positions_fetch_seconds",
                duration,
                labels={"result": result},
            )

    # Pure position-format transforms live in engines/position_formatting.py;
    # these are thin delegators preserving existing call sites and tests.
    def _build_position_state(
        self, symbol: str, positions: dict[str, Position]
    ) -> dict[str, Any] | None:
        return build_position_state(symbol, positions)

    def _resolve_close_order(
        self, position_state: dict[str, Any]
    ) -> tuple[OrderSide, Decimal] | None:
        return resolve_close_order(position_state)

    def _record_price_tick(self, symbol: str, price: Decimal) -> None:
        """Persist price tick to EventStore for crash recovery.

        Delegates to PriceTickStore which handles both in-memory
        history update and EventStore persistence.
        """
        self._price_tick_store.record_price_tick(symbol, price)

    def _positions_to_risk_format(
        self, positions: dict[str, Position]
    ) -> dict[str, dict[str, Any]]:
        return positions_to_risk_format(positions)

    def _positions_to_status_format(
        self, positions: dict[str, Position]
    ) -> dict[str, dict[str, Any]]:
        return positions_to_status_format(positions)

    def _calculate_order_quantity(
        self,
        symbol: str,
        price: Decimal,
        equity: Decimal,
        product: Product | None,
        *,
        quantity_override: Decimal | None = None,
    ) -> Decimal:
        """Calculate order size based on equity and position_fraction."""
        # External override (submit_order) bypasses dynamic sizing.
        if quantity_override is not None:
            return quantity_override

        # 1. Determine fraction
        fraction = Decimal("0.1")  # Default
        if hasattr(self.strategy, "config") and self.strategy.config.position_fraction:
            fraction = Decimal(str(self.strategy.config.position_fraction))
        elif (
            hasattr(self.context.config, "perps_position_fraction")
            and self.context.config.perps_position_fraction is not None
        ):
            fraction = Decimal(str(self.context.config.perps_position_fraction))

        # 2. Calculate raw quantity
        if price == 0:
            return Decimal("0")

        target_notional = equity * fraction
        quantity = target_notional / price

        # 3. Apply constraints
        if product and product.min_size:
            if quantity < product.min_size:
                logger.warning(f"Quantity {quantity} below min size {product.min_size}")
                return Decimal("0")

            # Round to step size if needed (simplified)
            # quantity = (quantity // product.step_size) * product.step_size

        return quantity

    def _is_reduce_only_order(self, current_pos: Position | None, side: OrderSide) -> bool:
        """Determine if an order would reduce an existing position."""
        if current_pos is None:
            return False

        # Handle Position objects
        if hasattr(current_pos, "side") and hasattr(current_pos, "quantity"):
            pos_side = current_pos.side.lower() if current_pos.side else ""
            pos_qty = current_pos.quantity
            # Reducing = LONG + SELL or SHORT + BUY
            return (pos_side == "long" and side == OrderSide.SELL and pos_qty > 0) or (
                pos_side == "short" and side == OrderSide.BUY and pos_qty > 0
            )

        # Handle dict format
        if isinstance(current_pos, dict):
            pos_side = str(current_pos.get("side", "")).lower()
            pos_qty = Decimal(str(current_pos.get("quantity", 0)))
            if pos_side in ("long", "short"):
                return (pos_side == "long" and side == OrderSide.SELL and pos_qty > 0) or (
                    pos_side == "short" and side == OrderSide.BUY and pos_qty > 0
                )
            # Legacy: quantity sign indicates direction
            return (pos_qty > 0 and side == OrderSide.SELL) or (
                pos_qty < 0 and side == OrderSide.BUY
            )

        return False

    def _record_decision_trace(self, trace: OrderDecisionTrace) -> None:
        """Persist the decision trace for auditability."""
        try:
            self._order_submitter.record_decision_trace(trace)
        except Exception as exc:
            logger.error(
                "Failed to record order decision trace",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="order_decision_trace",
                symbol=trace.symbol,
                side=trace.side,
            )

    def _append_event(self, event_type: str, data: dict[str, Any]) -> None:
        event_store = getattr(self, "_event_store", None)
        if event_store is None:
            return
        payload = dict(data)
        if "bot_id" not in payload and self.context.bot_id:
            payload["bot_id"] = self.context.bot_id
        try:
            event_store.append(event_type, payload)
        except Exception as exc:
            logger.error(
                "Failed to append event",
                event_type=event_type,
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="event_store",
            )

    def _get_strategy_names(self) -> list[str]:
        active_strategies = getattr(self.strategy, "active_strategies", None)
        if isinstance(active_strategies, (list, tuple)):
            strategy_names = [str(name) for name in active_strategies if name]
            if strategy_names:
                return strategy_names
        elif active_strategies:
            return [str(active_strategies)]
        return [self.strategy.__class__.__name__]

    def _emit_trade_gate_blocked(
        self,
        *,
        gate: str,
        symbol: str,
        side: OrderSide,
        reason: str,
        params: dict[str, Any] | None = None,
        decision_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "gate": gate,
            "symbol": symbol,
            "side": side.value if hasattr(side, "value") else str(side),
            "reason": reason,
            "timestamp": time.time(),
        }
        strategy_names = self._get_strategy_names()
        if strategy_names:
            payload["strategy"] = strategy_names[0]
            if len(strategy_names) > 1:
                payload["strategies"] = strategy_names
        if params:
            payload["params"] = params
        if decision_id is not None:
            payload["decision_id"] = str(decision_id)
        reason_label = summarize_seed_reason(reason)
        seed_title = f"Trade gate blocked: {gate}"
        if reason_label:
            seed_title = f"{seed_title} - {reason_label}"
        seed = build_feature_seed(
            title=seed_title,
            signature={
                "gate": gate,
                "reason": reason,
            },
        )
        payload["seed_key"] = seed.key
        payload["seed_title"] = seed.title
        self._append_event("trade_gate_blocked", payload)

    def _record_guard_events(self, guard_events: list[dict[str, Any]]) -> None:
        if not guard_events:
            return
        for event in guard_events:
            if not event:
                continue
            payload = dict(event)
            if payload.get("triggered") is False:
                continue
            payload.setdefault("guard", "volatility_circuit_breaker")
            payload.setdefault("timestamp", time.time())
            self._append_event("guard_triggered", payload)

    def _record_guard_failure_event(self, err: GuardError) -> None:
        payload = {
            "guard": err.guard_name,
            "reason": err.message,
            "recoverable": err.recoverable,
            "category": getattr(err, "category", "unknown"),
            "details": err.details,
            "timestamp": time.time(),
            "triggered": True,
        }
        self._append_event("guard_triggered", payload)
        if err.guard_name == "api_health":
            api_payload = {
                "reason": err.message,
                "details": err.details,
                "timestamp": payload["timestamp"],
            }
            self._append_event("api_error", api_payload)

    # Pure broker-order -> OrderRecord mapping lives in
    # engines/order_record_mapping.py; these are thin delegators that preserve
    # the existing internal call sites.
    def _get_order_field(self, order: Any, *keys: str) -> Any:
        return get_order_field(order, *keys)

    def _normalize_persisted_status(self, status: Any) -> PersistedOrderStatus:
        return normalize_persisted_status(status)

    def _parse_decimal(self, value: Any, default: Decimal) -> Decimal:
        return parse_decimal(value, default)

    def _parse_decimal_optional(self, value: Any) -> Decimal | None:
        return parse_decimal_optional(value)

    def _merge_metadata(
        self,
        base: dict[str, Any] | None,
        update: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return merge_metadata(base, update)

    def _build_record_from_broker_order(
        self,
        order: Any,
        *,
        bot_id: str,
        now: datetime,
    ) -> OrderRecord | None:
        return build_record_from_broker_order(order, bot_id=bot_id, now=now)

    def _parse_timestamp(self, value: Any) -> float:
        return parse_timestamp(value)

    def _record_unfilled_order_alerts(self, orders: list[Any]) -> None:
        self._order_audit.record_unfilled_order_alerts(orders)

    def _finalize_decision_trace(
        self,
        trace: OrderDecisionTrace,
        *,
        status: OrderSubmissionStatus,
        order_id: str | None = None,
        reason: str | None = None,
        error: str | None = None,
    ) -> OrderSubmissionResult:
        detail = reason or error
        trace.record_outcome("result", status.value, detail=detail)
        if status is OrderSubmissionStatus.BLOCKED:
            try:
                record_trade_blocked()
            except Exception:
                pass
        self._record_decision_trace(trace)
        return OrderSubmissionResult(
            status=status,
            order_id=order_id,
            reason=reason,
            error=error,
            decision_trace=trace,
        )

    async def _check_degradation_gate(
        self,
        *,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        trace: OrderDecisionTrace,
        reduce_only_flag: bool,
    ) -> OrderSubmissionResult | None:
        if self._degradation.is_paused(symbol, is_reduce_only=reduce_only_flag):
            pause_reason = self._degradation.get_pause_reason(symbol) or "unknown"
            logger.warning(
                f"Order blocked: trading paused for {symbol}",
                symbol=symbol,
                side=side.value,
                reason=pause_reason,
                operation="degradation",
                stage="order_blocked",
            )
            self._emit_trade_gate_blocked(
                gate="degradation_gate",
                symbol=symbol,
                side=side,
                reason=pause_reason,
                params={
                    "pause_reason": pause_reason,
                    "reduce_only": reduce_only_flag,
                },
                decision_id=trace.decision_id,
            )
            self._order_submitter.record_rejection(
                symbol, side.value, Decimal("0"), price, f"paused:{pause_reason}"
            )
            await self._notify(
                title="Order Blocked - Trading Paused",
                message=f"Cannot place {side.value} order for {symbol}: {pause_reason}",
                severity=AlertSeverity.WARNING,
                context={"symbol": symbol, "side": side.value, "reason": pause_reason},
            )
            trace.record_outcome("degradation_gate", "blocked", detail=pause_reason)
            return self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason=f"paused:{pause_reason}",
            )
        trace.record_outcome("degradation_gate", "passed")
        return None

    def _calculate_quantity_and_record(
        self,
        *,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        equity: Decimal,
        quantity_override: Decimal | None,
        trace: OrderDecisionTrace,
    ) -> tuple[Decimal, OrderSubmissionResult | None]:
        quantity = self._calculate_order_quantity(
            symbol,
            price,
            equity,
            product=None,
            quantity_override=quantity_override,
        )
        trace.quantity = quantity

        if quantity <= 0:
            logger.warning(f"Calculated quantity is {quantity}, skipping order")
            trace.record_outcome("sizing", "blocked", detail="quantity_zero")
            self._emit_trade_gate_blocked(
                gate="sizing",
                symbol=symbol,
                side=side,
                reason="quantity_zero",
                params={
                    "quantity": str(quantity),
                    "price": str(price),
                    "equity": str(equity),
                    "quantity_override": (
                        str(quantity_override) if quantity_override is not None else None
                    ),
                },
                decision_id=trace.decision_id,
            )
            self._order_submitter.record_rejection(
                symbol, side.value, quantity, price, "quantity_zero"
            )
            return quantity, self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason="quantity_zero",
            )
        trace.record_outcome("sizing", "passed")
        return quantity, None

    async def _check_reduce_only_request(
        self,
        *,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        reduce_only_requested: bool,
        is_reducing: bool,
        trace: OrderDecisionTrace,
    ) -> OrderSubmissionResult | None:
        if reduce_only_requested and not is_reducing:
            logger.warning(
                "Reduce-only requested without a matching position",
                symbol=symbol,
                side=side.value,
                operation="reduce_only",
                stage="requested_not_reducing",
            )
            trace.record_outcome("reduce_only", "blocked", detail="requested_not_reducing")
            self._emit_trade_gate_blocked(
                gate="reduce_only",
                symbol=symbol,
                side=side,
                reason="requested_not_reducing",
                params={
                    "reduce_only_requested": reduce_only_requested,
                    "is_reducing": is_reducing,
                },
                decision_id=trace.decision_id,
            )
            self._order_submitter.record_rejection(
                symbol, side.value, quantity, price, "reduce_only_not_reducing"
            )
            return self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason="reduce_only_not_reducing",
            )
        return None

    async def _run_security_validation(
        self,
        *,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        equity: Decimal,
        trace: OrderDecisionTrace,
    ) -> OrderSubmissionResult | None:
        from gpt_trader.security.validate import get_validator

        security_order = {
            "symbol": symbol,
            "side": side.value,
            "quantity": float(quantity),
            "price": float(price),
            "type": "MARKET",
        }

        limits = {}
        if hasattr(self.context.config, "risk"):
            risk = self.context.config.risk
            if risk:
                max_position_size = 0.05
                raw_max_position_fraction = getattr(risk, "max_position_pct", None)
                if raw_max_position_fraction is None:
                    raw_max_position_fraction = getattr(risk, "position_fraction", None)
                if raw_max_position_fraction is not None:
                    try:
                        max_position_size = float(raw_max_position_fraction)
                    except (TypeError, ValueError):
                        max_position_size = 0.05
                limits["max_position_size"] = max_position_size

                limits["max_leverage"] = float(getattr(risk, "max_leverage", 2.0))
                limits["max_daily_loss"] = float(getattr(risk, "daily_loss_limit_pct", 0.02))

        security_result = get_validator().validate_order_request(
            security_order, account_value=float(equity), limits=limits
        )

        if not security_result.is_valid:
            error_msg = f"Security validation failed: {', '.join(security_result.errors)}"
            logger.error(error_msg)
            self._order_submitter.record_rejection(
                symbol,
                side.value,
                quantity,
                price,
                "security_validation_failed",
            )
            await self._notify(
                title="Security Validation Failed",
                message=error_msg,
                severity=AlertSeverity.ERROR,
                context=security_order,
            )
            trace.record_outcome("security_validation", "blocked", detail=error_msg)
            return self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason=error_msg,
            )
        trace.record_outcome("security_validation", "passed")
        return None

    async def _apply_reduce_only_mode(
        self,
        *,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        quantity: Decimal,
        reduce_only_flag: bool,
        is_reducing: bool,
        current_pos: Position | dict[str, Any] | None,
        trace: OrderDecisionTrace,
    ) -> tuple[Decimal, OrderSubmissionResult | None]:
        risk_manager = self.context.risk_manager
        if risk_manager is None:
            error_msg = "risk_manager_unavailable"
            logger.error(
                "Order blocked - risk manager unavailable",
                symbol=symbol,
                side=side.value,
                operation="reduce_only",
                stage="missing_risk_manager",
            )
            self._emit_trade_gate_blocked(
                gate="reduce_only",
                symbol=symbol,
                side=side,
                reason=error_msg,
                params={
                    "is_reducing": is_reducing,
                    "reduce_only_flag": reduce_only_flag,
                },
                decision_id=trace.decision_id,
            )
            self._order_submitter.record_rejection(symbol, side.value, quantity, price, error_msg)
            await self._notify(
                title="Order Blocked - Risk Manager Unavailable",
                message=(
                    f"Cannot place {side.value} order for {symbol}: "
                    "risk manager is required for reduce-only enforcement"
                ),
                severity=AlertSeverity.ERROR,
                context={
                    "symbol": symbol,
                    "side": side.value,
                    "reason": error_msg,
                },
            )
            trace.record_outcome("reduce_only", "blocked", detail=error_msg)
            return quantity, self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason=error_msg,
            )

        daily_pnl_triggered = bool(getattr(risk_manager, "_daily_pnl_triggered", False))
        reduce_only_mode = risk_manager.is_reduce_only_mode()
        reduce_only_active = reduce_only_mode or daily_pnl_triggered
        reduce_only_clamped = False
        current_qty: Decimal | None = None
        if reduce_only_active and is_reducing and current_pos is not None:
            if hasattr(current_pos, "quantity"):
                current_qty = abs(current_pos.quantity)
            elif isinstance(current_pos, dict):
                current_qty = abs(Decimal(str(current_pos.get("quantity", 0))))
            else:
                current_qty = Decimal("0")

            if current_qty is not None and quantity > current_qty:
                logger.warning(
                    f"Reduce-only: clamping order from {quantity} to {current_qty} "
                    f"to prevent position flip for {symbol}"
                )
                quantity = current_qty
                trace.quantity = quantity
                reduce_only_clamped = True

            if quantity <= 0:
                logger.info(f"Reduce-only: no position to reduce for {symbol}, skipping order")
                trace.record_outcome(
                    "reduce_only",
                    "blocked",
                    detail="reduce_only_empty_position",
                )
                self._emit_trade_gate_blocked(
                    gate="reduce_only",
                    symbol=symbol,
                    side=side,
                    reason="reduce_only_empty_position",
                    params={
                        "quantity": str(quantity),
                        "current_qty": str(current_qty) if current_qty is not None else None,
                        "reduce_only_mode": reduce_only_mode,
                        "daily_pnl_triggered": daily_pnl_triggered,
                    },
                    decision_id=trace.decision_id,
                )
                self._order_submitter.record_rejection(
                    symbol, side.value, quantity, price, "reduce_only_empty_position"
                )
                return quantity, self._finalize_decision_trace(
                    trace,
                    status=OrderSubmissionStatus.BLOCKED,
                    reason="reduce_only_empty_position",
                )

        order_for_check = {
            "symbol": symbol,
            "side": side.value,
            "quantity": float(quantity),
            "reduce_only": reduce_only_flag,
        }

        if not risk_manager.check_order(order_for_check):
            error_msg = (
                f"Order blocked by risk manager: "
                f"reduce_only_mode={reduce_only_mode}, "
                f"daily_pnl_triggered={daily_pnl_triggered}"
            )
            logger.warning(error_msg)
            self._emit_trade_gate_blocked(
                gate="reduce_only",
                symbol=symbol,
                side=side,
                reason="reduce_only_mode_blocked",
                params={
                    "reduce_only_mode": reduce_only_mode,
                    "daily_pnl_triggered": daily_pnl_triggered,
                    "reduce_only_flag": reduce_only_flag,
                    "is_reducing": is_reducing,
                },
                decision_id=trace.decision_id,
            )
            await self._notify(
                title="Order Blocked - Reduce Only Mode",
                message=f"Cannot open new {side.value} position for {symbol} while in reduce-only mode",
                severity=AlertSeverity.WARNING,
                context=order_for_check,
            )
            trace.record_outcome("reduce_only", "blocked", detail=error_msg)
            self._order_submitter.record_rejection(
                symbol, side.value, quantity, price, "reduce_only_mode_blocked"
            )
            return quantity, self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason=error_msg,
            )
        trace.record_outcome(
            "reduce_only",
            "passed",
            detail="clamped" if reduce_only_clamped else None,
        )
        return quantity, None

    async def _check_mark_staleness(
        self,
        *,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        reduce_only_flag: bool,
        trace: OrderDecisionTrace,
    ) -> OrderSubmissionResult | None:
        if self.context.risk_manager is None:
            trace.record_outcome("mark_staleness", "skipped")
            return None

        if self.context.risk_manager.check_mark_staleness(symbol):
            config = self.context.risk_manager.config
            if config is not None:
                allow_reduce = config.mark_staleness_allow_reduce_only
                cooldown = config.mark_staleness_cooldown_seconds
                self._append_event(
                    "stale_mark_detected",
                    {
                        "symbol": symbol,
                        "side": side.value,
                        "allowed_reduce_only": allow_reduce and reduce_only_flag,
                        "timestamp": time.time(),
                    },
                )
                self._degradation.pause_symbol(
                    symbol=symbol,
                    seconds=cooldown,
                    reason="mark_staleness",
                    allow_reduce_only=allow_reduce,
                )
                if allow_reduce and reduce_only_flag:
                    logger.info(
                        f"Mark stale for {symbol} but allowing reduce-only order",
                        operation="degradation",
                    )
                    trace.record_outcome(
                        "mark_staleness",
                        "allowed",
                        detail="reduce_only",
                    )
                    return None

                logger.warning(f"Order blocked: mark price stale for {symbol}")
                self._emit_trade_gate_blocked(
                    gate="mark_staleness",
                    symbol=symbol,
                    side=side,
                    reason="mark_staleness",
                    params={
                        "allow_reduce_only": allow_reduce,
                        "reduce_only": reduce_only_flag,
                        "cooldown_seconds": cooldown,
                    },
                    decision_id=trace.decision_id,
                )
                self._order_submitter.record_rejection(
                    symbol, side.value, quantity, price, "mark_staleness"
                )
                await self._notify(
                    title="Order Blocked - Stale Mark Price",
                    message=f"Cannot place order for {symbol}: mark price data is stale",
                    severity=AlertSeverity.WARNING,
                    context={"symbol": symbol, "side": side.value},
                )
                trace.record_outcome("mark_staleness", "blocked", detail="stale")
                return self._finalize_decision_trace(
                    trace,
                    status=OrderSubmissionStatus.BLOCKED,
                    reason="mark_staleness",
                )

            logger.warning(f"Order blocked: mark price stale for {symbol}")
            self._emit_trade_gate_blocked(
                gate="mark_staleness",
                symbol=symbol,
                side=side,
                reason="mark_staleness",
                params={
                    "allow_reduce_only": False,
                    "reduce_only": reduce_only_flag,
                    "cooldown_seconds": None,
                },
                decision_id=trace.decision_id,
            )
            self._append_event(
                "stale_mark_detected",
                {
                    "symbol": symbol,
                    "side": side.value,
                    "allowed_reduce_only": False,
                    "timestamp": time.time(),
                },
            )
            await self._notify(
                title="Order Blocked - Stale Mark Price",
                message=f"Cannot place order for {symbol}: mark price data is stale",
                severity=AlertSeverity.WARNING,
                context={"symbol": symbol, "side": side.value},
            )
            trace.record_outcome("mark_staleness", "blocked", detail="stale")
            return self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason="mark_staleness",
            )

        trace.record_outcome("mark_staleness", "passed")
        return None

    async def _run_order_validator_guards(
        self,
        *,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        equity: Decimal,
        quantity: Decimal,
        reduce_only_flag: bool,
        trace: OrderDecisionTrace,
    ) -> tuple[Decimal, Decimal, bool, OrderSubmissionResult | None]:
        effective_price = price
        if self._order_validator is None:
            trace.record_outcome("order_validation", "skipped")
            return quantity, effective_price, reduce_only_flag, None

        try:
            with profile_span("pre_trade_validation", {"symbol": symbol}) as _val_span:
                product = self._state_collector.require_product(symbol, product=None)
                effective_price = self._state_collector.resolve_effective_price(
                    symbol, side.value.lower(), price, product
                )

                try:
                    quantity, _ = self._order_validator.validate_exchange_rules(
                        symbol=symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        order_quantity=quantity,
                        price=None,
                        effective_price=effective_price,
                        product=product,
                    )
                    trace.quantity = quantity
                    trace.record_outcome("exchange_rules", "passed")
                except ValidationError as exc:
                    trace.record_outcome("exchange_rules", "blocked", detail=str(exc))
                    raise

                current_positions_dict = self._state_collector.build_positions_dict(
                    list(self._current_positions.values())
                )
                try:
                    self._order_validator.run_pre_trade_validation(
                        symbol=symbol,
                        side=side,
                        order_quantity=quantity,
                        effective_price=effective_price,
                        product=product,
                        equity=equity,
                        current_positions=current_positions_dict,
                    )
                    trace.record_outcome("pre_trade_validation", "passed")
                except ValidationError as exc:
                    trace.record_outcome("pre_trade_validation", "blocked", detail=str(exc))
                    raise

                try:
                    self._order_validator.enforce_slippage_guard(
                        symbol, side, quantity, effective_price
                    )
                    trace.record_outcome("slippage_guard", "passed")
                    self._degradation.reset_slippage_failures(symbol)
                except ValidationError as slippage_exc:
                    trace.record_outcome(
                        "slippage_guard",
                        "blocked",
                        detail=str(slippage_exc),
                    )
                    config = self.context.risk_manager.config if self.context.risk_manager else None
                    if config is not None:
                        self._degradation.record_slippage_failure(symbol, config)
                    raise slippage_exc

                # Use container's tracker (validated at init, asserted non-None here)
                assert self.context.container is not None
                failure_tracker = self.context.container.validation_failure_tracker
                config = self.context.risk_manager.config if self.context.risk_manager else None
                preview_disable_threshold = config.preview_failure_disable_after if config else 5

                if (
                    self._order_validator.enable_order_preview
                    and failure_tracker.get_failure_count("order_preview")
                    >= preview_disable_threshold
                ):
                    logger.warning(
                        "Auto-disabling order preview due to repeated failures",
                        consecutive_failures=failure_tracker.get_failure_count("order_preview"),
                        threshold=preview_disable_threshold,
                        operation="degradation",
                        stage="preview_disable",
                    )
                    self._order_validator.enable_order_preview = False

                if self._order_validator.enable_order_preview:
                    try:
                        await self._order_validator.maybe_preview_order_async(
                            symbol=symbol,
                            side=side,
                            order_type=OrderType.MARKET,
                            order_quantity=quantity,
                            effective_price=effective_price,
                            stop_price=None,
                            tif=self.context.config.time_in_force,
                            reduce_only=reduce_only_flag,
                            leverage=None,
                        )
                        trace.record_outcome("order_preview", "passed")
                    except ValidationError as exc:
                        trace.record_outcome(
                            "order_preview",
                            "blocked",
                            detail=str(exc),
                        )
                        raise
                else:
                    trace.record_outcome("order_preview", "skipped")

                reduce_only_flag = self._order_validator.finalize_reduce_only_flag(
                    reduce_only_flag, symbol
                )
                trace.reduce_only_final = reduce_only_flag
        except ValidationError as exc:
            logger.warning(f"Pre-trade guard rejected order: {exc}")
            blocked_stage = None
            for stage, outcome in trace.outcomes.items():
                if outcome.get("status") == "blocked":
                    blocked_stage = stage
                    break
            reason_code = blocked_stage or "order_validation"
            self._emit_trade_gate_blocked(
                gate=reason_code,
                symbol=symbol,
                side=side,
                reason=str(exc),
                params={
                    "blocked_stage": reason_code,
                    "reduce_only": reduce_only_flag,
                },
                decision_id=trace.decision_id,
            )
            self._order_submitter.record_rejection(
                symbol, side.value, quantity, effective_price, reason_code
            )
            await self._notify(
                title="Order Blocked - Guard Rejection",
                message=f"Cannot place order for {symbol}: {exc}",
                severity=AlertSeverity.WARNING,
                context={"symbol": symbol, "side": side.value, "reason": str(exc)},
            )
            trace.record_outcome("order_validation", "blocked", detail=str(exc))
            return (
                quantity,
                effective_price,
                reduce_only_flag,
                self._finalize_decision_trace(
                    trace,
                    status=OrderSubmissionStatus.BLOCKED,
                    reason=str(exc),
                ),
            )
        except Exception as exc:
            logger.error(f"Guard check error: {exc}")
            self._order_submitter.record_rejection(
                symbol, side.value, quantity, price, "guard_error"
            )
            await self._notify(
                title="Order Blocked - Guard Error",
                message=f"Cannot place order for {symbol}: guard check failed",
                severity=AlertSeverity.ERROR,
                context={"symbol": symbol, "side": side.value, "error": str(exc)},
            )
            trace.record_outcome("order_validation", "error", detail=str(exc))
            return (
                quantity,
                effective_price,
                reduce_only_flag,
                self._finalize_decision_trace(
                    trace,
                    status=OrderSubmissionStatus.FAILED,
                    error=str(exc),
                ),
            )

        return quantity, effective_price, reduce_only_flag, None

    async def _validate_and_place_order(
        self,
        symbol: str,
        decision: Decision,
        price: Decimal,
        equity: Decimal,
        quantity_override: Decimal | None = None,
        reduce_only_requested: bool = False,
    ) -> OrderSubmissionResult:
        """Validate and submit an order through the guard stack.

        Returns:
            OrderSubmissionResult describing success/blocked/failed.
        """
        side = OrderSide.BUY if decision.action == Action.BUY else OrderSide.SELL

        # Early check: is this order actually reduce-only? (needed for degradation check)
        current_pos = self._current_positions.get(symbol)
        is_reducing = self._is_reduce_only_order(current_pos, side)
        reduce_only_flag = is_reducing

        decision_id = self._order_submitter.generate_client_order_id(None)
        trace = OrderDecisionTrace(
            symbol=symbol,
            side=side.value,
            price=price,
            equity=equity,
            quantity=None,
            reduce_only=reduce_only_requested,
            reduce_only_final=reduce_only_flag,
            reason=decision.reason,
            decision_id=decision_id,
            bot_id=str(self.context.bot_id) if self.context.bot_id is not None else None,
        )

        config = getattr(self.context.risk_manager, "config", None)
        kill_switch_enabled = getattr(config, "kill_switch_enabled", False) is True
        if kill_switch_enabled:
            trace.record_outcome(
                "kill_switch",
                "blocked",
                detail="kill_switch_enabled",
            )
            self._order_submitter.record_rejection(
                symbol,
                side.value,
                Decimal("0"),
                price,
                "kill_switch",
                client_order_id=decision_id,
            )
            return self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason="kill_switch",
            )

        result = await self._check_degradation_gate(
            symbol=symbol,
            side=side,
            price=price,
            trace=trace,
            reduce_only_flag=reduce_only_flag,
        )
        if result is not None:
            return result

        quantity, result = self._calculate_quantity_and_record(
            symbol=symbol,
            side=side,
            price=price,
            equity=equity,
            quantity_override=quantity_override,
            trace=trace,
        )
        if result is not None:
            return result

        result = await self._check_reduce_only_request(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            reduce_only_requested=reduce_only_requested,
            is_reducing=is_reducing,
            trace=trace,
        )
        if result is not None:
            return result

        result = await self._run_security_validation(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            equity=equity,
            trace=trace,
        )
        if result is not None:
            return result

        quantity, result = await self._apply_reduce_only_mode(
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            reduce_only_flag=reduce_only_flag,
            is_reducing=is_reducing,
            current_pos=current_pos,
            trace=trace,
        )
        if result is not None:
            return result

        result = await self._check_mark_staleness(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            reduce_only_flag=reduce_only_flag,
            trace=trace,
        )
        if result is not None:
            return result

        (
            quantity,
            effective_price,
            reduce_only_flag,
            result,
        ) = await self._run_order_validator_guards(
            symbol=symbol,
            side=side,
            price=price,
            equity=equity,
            quantity=quantity,
            reduce_only_flag=reduce_only_flag,
            trace=trace,
        )
        if result is not None:
            return result

        # Place order via OrderSubmitter for proper ID tracking and telemetry
        submission_outcome = await self._broker_calls(
            self._order_submitter.submit_order_with_result,
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            order_quantity=quantity,
            price=None,  # Market order
            effective_price=effective_price,
            stop_price=None,
            tif=self.context.config.time_in_force,
            reduce_only=reduce_only_flag,
            leverage=None,
            client_order_id=decision_id,
        )

        # Notify on successful order placement
        if submission_outcome.success:
            order_id = submission_outcome.order_id
            trace.order_id = order_id
            await self._notify(
                title="Order Executed",
                message=f"{side.value} {quantity} {symbol} at ~{price}",
                severity=AlertSeverity.INFO,
                context={
                    "symbol": symbol,
                    "side": side.value,
                    "quantity": str(quantity),
                    "price": str(price),
                    "order_id": order_id,
                },
            )

            # Record trade in status reporter
            self._status_reporter.add_trade(
                {
                    "symbol": symbol,
                    "side": side.value,
                    "quantity": str(quantity),
                    "price": str(price),
                    "order_id": order_id,
                }
            )
            trace.record_outcome("submit_order", "success", order_id=order_id)
            return self._finalize_decision_trace(
                trace,
                status=OrderSubmissionStatus.SUCCESS,
                order_id=order_id,
            )
        reason = submission_outcome.reason or "broker_rejected"
        logger.warning(
            "Order submission failed",
            symbol=symbol,
            side=side.value,
            reason=reason,
            reason_detail=submission_outcome.reason_detail,
            operation="order_submit",
            stage="failed",
        )
        trace.record_outcome(
            "submit_order",
            "failed",
            detail=reason,
            reason_detail=submission_outcome.reason_detail,
            error=submission_outcome.error,
        )
        return self._finalize_decision_trace(
            trace,
            status=OrderSubmissionStatus.FAILED,
            reason=reason,
            error=submission_outcome.error_message,
        )

    def reset_daily_tracking(self) -> None:
        """Reset daily PnL tracking and guard cache (start of trading day)."""
        try:
            broker = self.context.broker
            if broker is None:
                logger.warning(
                    "Cannot reset daily tracking without broker",
                    operation="daily_tracking",
                    stage="missing_broker",
                )
                return

            balances = broker.list_balances()
            equity, _, _ = self._state_collector.calculate_equity_from_balances(balances)

            if self.context.risk_manager is not None:
                self.context.risk_manager.reset_daily_tracking()

            if self._guard_manager is not None:
                self._guard_manager.invalidate_cache()

            logger.info(
                "Daily tracking reset",
                operation="daily_tracking",
                stage="reset",
                equity=float(equity),
            )
        except Exception as exc:
            logger.error(
                "Failed to reset daily tracking",
                error_message=str(exc),
                operation="daily_tracking",
                stage="reset",
            )

    # =========================================================================
    # PUBLIC SUBMISSION ENTRYPOINT
    # =========================================================================
    # This is the canonical order submission path. All order execution should
    # route through this method to ensure the full guard stack is applied:
    # degradation gate → sizing → security → risk → staleness → validator
    # =========================================================================

    async def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        equity: Decimal,
        *,
        quantity_override: Decimal | None = None,
        reduce_only: bool = False,
        reason: str = "external_submission",
        confidence: float = 1.0,
    ) -> OrderSubmissionResult:
        """Public entrypoint for order submission through the canonical guard stack.

        This method provides external callers access to the full pre-trade
        validation pipeline. All orders should route through here to ensure
        consistent guard enforcement.

        Args:
            symbol: Trading symbol (e.g., "BTC-USD").
            side: Order side (BUY or SELL).
            price: Current market price for validation.
            equity: Current account equity for position sizing.
            quantity_override: If provided, uses this quantity instead of dynamic sizing.
            reduce_only: If True, order is reduce-only (affects degradation gate).
            reason: Reason for the order (for logging/telemetry).
            confidence: Decision confidence score (0.0-1.0).

        Note:
            This method delegates to _validate_and_place_order after constructing
            a Decision object. The full guard stack is applied.

        Returns:
            OrderSubmissionResult describing the outcome.
        """
        # Construct Decision from inputs
        action = Action.BUY if side == OrderSide.BUY else Action.SELL
        decision = Decision(
            action=action,
            reason=reason,
            confidence=confidence,
        )

        # Pass quantity override through to guard stack sizing.
        return await self._validate_and_place_order(
            symbol,
            decision,
            price,
            equity,
            quantity_override=quantity_override,
            reduce_only_requested=reduce_only,
        )

    async def shutdown(self) -> None:
        self._transition_state(EngineState.STOPPING, reason="shutdown_called")
        await self._runtime_engine.shutdown(self._runtime_lifecycle_plan())
        self._background_tasks.clear()
        self._shutdown_complete = True
        if self._runtime_engine.graceful_shutdown_failed:
            self._transition_state(
                EngineState.ERROR,
                reason="runtime_shutdown_failed",
                details={"error": self._runtime_engine.last_error},
            )
            return
        self._transition_state(EngineState.STOPPED, reason="shutdown_complete")

    async def _stop_ws_health_watchdog_task(self) -> None:
        if self._ws_health_task is not None and not self._ws_health_task.done():
            self._ws_health_task.cancel()
            try:
                await self._ws_health_task
            except asyncio.CancelledError:
                pass
        self._ws_health_task = None
        logger.info(
            "Stopped WS health watchdog",
            operation="ws_health",
            stage="stop",
        )

    async def _runtime_stop_streaming(self) -> None:
        await self._stop_streaming()
        logger.info(
            "Stopped WebSocket streaming",
            operation="streaming",
            stage="stop",
        )

    def _runtime_shutdown_broker_calls(self) -> None:
        shutdown = getattr(self._broker_calls, "shutdown", None)
        if callable(shutdown) and not self._preserve_broker_calls_on_shutdown:
            shutdown()

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=self.running, component=self.name)

    async def _audit_orders(self) -> None:
        """Audit open orders for reconciliation."""
        await self._order_audit.audit_orders()
