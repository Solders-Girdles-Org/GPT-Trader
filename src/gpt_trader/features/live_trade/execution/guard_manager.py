"""
Runtime guard management for live trading execution.

This module provides the GuardManager orchestrator that coordinates runtime
safety checks using individual guard implementations from the guards subpackage.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import is_dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from gpt_trader.core import Balance
from gpt_trader.core.protocols import BrokerProtocol
from gpt_trader.features.live_trade.execution.broker_executor import BrokerExecutor
from gpt_trader.features.live_trade.execution.guards.api_health import ApiHealthGuard
from gpt_trader.features.live_trade.execution.guards.cache import GuardStateCache
from gpt_trader.features.live_trade.execution.guards.daily_loss import DailyLossGuard
from gpt_trader.features.live_trade.execution.guards.liquidation_buffer import (
    LiquidationBufferGuard,
)
from gpt_trader.features.live_trade.execution.guards.mark_staleness import MarkStalenessGuard
from gpt_trader.features.live_trade.execution.guards.pnl_telemetry import PnLTelemetryGuard
from gpt_trader.features.live_trade.execution.guards.protocol import Guard, RuntimeGuardState
from gpt_trader.features.live_trade.execution.guards.risk_metrics import RiskMetricsGuard
from gpt_trader.features.live_trade.execution.guards.volatility import VolatilityGuard
from gpt_trader.features.live_trade.guard_errors import (
    GuardError,
    RiskGuardComputationError,
    record_guard_failure,
    record_guard_success,
)
from gpt_trader.monitoring.tracing import trace_span
from gpt_trader.utilities.logging_patterns import get_logger
from gpt_trader.utilities.quantities import quantity_from

if TYPE_CHECKING:
    from gpt_trader.features.live_trade.risk.protocols import RiskManagerProtocol

logger = get_logger(__name__, component="execution_guards")


class GuardManager:
    """Orchestrates runtime safety guards for live trading execution."""

    def __init__(
        self,
        broker: BrokerProtocol,
        risk_manager: RiskManagerProtocol,
        equity_calculator: Callable[[list[Balance]], tuple[Decimal, list[Balance], Decimal]],
        open_orders: list[str],
        invalidate_cache_callback: Callable[[], None],
        cancel_retries_enabled: bool = False,
    ) -> None:
        """
        Initialize guard manager.

        Args:
            broker: Brokerage adapter
            risk_manager: Risk manager instance
            equity_calculator: Function to calculate equity from balances
            open_orders: List of open order IDs to manage
            invalidate_cache_callback: Function to invalidate guard cache
        """
        self.broker = broker
        self.risk_manager = risk_manager
        self._calculate_equity = equity_calculator
        self.open_orders = open_orders
        self._cancel_retries_enabled = cancel_retries_enabled
        self._broker_executor = BrokerExecutor(broker=broker)
        # Backward compat: store callback reference for tests
        self._invalidate_cache_callback = invalidate_cache_callback

        # Initialize cache with callback
        self._cache = GuardStateCache(
            full_interval=60.0,
            invalidate_callback=invalidate_cache_callback,
        )

        # Initialize individual guards
        self._guards: list[Guard] = [
            PnLTelemetryGuard(),
            DailyLossGuard(
                risk_manager=risk_manager,
                cancel_all_orders=self.cancel_all_orders,  # type: ignore[arg-type]
                invalidate_cache=self._cache.invalidate,
            ),
            LiquidationBufferGuard(broker=cast(BrokerProtocol, broker), risk_manager=risk_manager),
            MarkStalenessGuard(broker=broker, risk_manager=risk_manager),
            RiskMetricsGuard(risk_manager=risk_manager),
            VolatilityGuard(broker=cast(BrokerProtocol, broker), risk_manager=risk_manager),
            ApiHealthGuard(broker=broker, risk_manager=risk_manager),
        ]

    def _extract_order_ids(self, orders: Any) -> list[str]:
        """Normalize broker list_orders responses into order ID strings."""
        if orders is None:
            return []

        items: list[Any]
        if isinstance(orders, dict):
            items = list(orders.get("orders", []))
        elif isinstance(orders, list):
            items = orders
        else:
            return []

        order_ids: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item is None:
                continue
            order_id = None
            if isinstance(item, dict):
                order_id = item.get("id") or item.get("order_id")
            else:
                order_id = getattr(item, "id", None) or getattr(item, "order_id", None)
            if order_id is None:
                continue
            order_id = str(order_id)
            if order_id in seen:
                continue
            seen.add(order_id)
            order_ids.append(order_id)
        return order_ids

    def _fetch_open_order_ids(self) -> tuple[list[str], bool]:
        """Fetch open order IDs from broker if supported, otherwise use tracked IDs."""
        list_orders = getattr(self.broker, "list_orders", None)
        if list_orders is None or not callable(list_orders):
            return list(self.open_orders), False

        try:
            response = list_orders(status=["OPEN"])
        except TypeError:
            try:
                response = list_orders(order_status=["OPEN"])
            except Exception as exc:
                logger.warning(
                    "Failed to list open orders",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="order_cancel",
                    stage="list_orders",
                )
                return list(self.open_orders), False
        except Exception as exc:
            logger.warning(
                "Failed to list open orders",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="order_cancel",
                stage="list_orders",
            )
            return list(self.open_orders), False

        if not isinstance(response, (list, dict)):
            logger.warning(
                "Unexpected list_orders response type",
                response_type=type(response).__name__,
                operation="order_cancel",
                stage="list_orders",
            )
            return list(self.open_orders), False

        return self._extract_order_ids(response), True

    # Backward compatibility properties
    @property
    def _runtime_guard_state(self) -> RuntimeGuardState | None:
        return self._cache.state

    @_runtime_guard_state.setter
    def _runtime_guard_state(self, value: RuntimeGuardState | None) -> None:
        if value is not None:
            self._cache.update(value, time.time())
        else:
            self._cache.invalidate()

    @property
    def _runtime_guard_dirty(self) -> bool:
        return self._cache._dirty

    @_runtime_guard_dirty.setter
    def _runtime_guard_dirty(self, value: bool) -> None:
        if value:
            self._cache._dirty = True

    @property
    def _runtime_guard_last_full_ts(self) -> float:
        return self._cache._last_full_ts

    @_runtime_guard_last_full_ts.setter
    def _runtime_guard_last_full_ts(self, value: float) -> None:
        self._cache._last_full_ts = value

    @property
    def _runtime_guard_full_interval(self) -> float:
        return self._cache._full_interval

    @_runtime_guard_full_interval.setter
    def _runtime_guard_full_interval(self, value: float) -> None:
        self._cache._full_interval = value

    def invalidate_cache(self) -> None:
        """Force the next runtime guard invocation to refresh cached state."""
        self._cache.invalidate()

    def should_run_full_guard(self, now: float) -> bool:
        """Check if a full guard run is needed."""
        return self._cache.should_run_full(now)

    def collect_runtime_guard_state(self) -> RuntimeGuardState:
        """Collect current account state for guard evaluation."""
        balances = self.broker.list_balances()
        equity, _, _ = self._calculate_equity(balances)
        if equity == Decimal("0") and balances:
            equity = sum((b.available for b in balances), Decimal("0"))

        positions = self.broker.list_positions()

        positions_pnl: dict[str, dict[str, Decimal]] = {}
        for pos in positions:
            if hasattr(self.broker, "get_position_pnl"):
                try:
                    pnl_data = self.broker.get_position_pnl(pos.symbol)
                    if isinstance(pnl_data, dict):
                        positions_pnl[pos.symbol] = {
                            "realized_pnl": Decimal(str(pnl_data.get("realized_pnl", "0"))),
                            "unrealized_pnl": Decimal(str(pnl_data.get("unrealized_pnl", "0"))),
                        }
                        continue
                except Exception as exc:
                    logger.error(
                        "Failed to get position PnL from broker",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        operation="collect_runtime_guard_state",
                        symbol=pos.symbol,
                    )

            try:
                entry_price = Decimal(str(getattr(pos, "entry_price", "0")))
                mark_price = Decimal(str(getattr(pos, "mark_price", "0")))
                position_quantity = quantity_from(pos) or Decimal("0")
                side = getattr(pos, "side", "").lower()
                side_multiplier = Decimal("1") if side == "long" else Decimal("-1")
                unrealized = (mark_price - entry_price) * position_quantity * side_multiplier
            except Exception as exc:
                logger.error(
                    "Failed to calculate unrealized PnL for position",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="collect_runtime_guard_state",
                    symbol=pos.symbol,
                )
                unrealized = Decimal("0")
            positions_pnl[pos.symbol] = {
                "realized_pnl": Decimal("0"),
                "unrealized_pnl": unrealized,
            }

        positions_dict: dict[str, dict[str, Decimal]] = {}
        for pos in positions:
            try:
                positions_dict[pos.symbol] = {
                    "quantity": quantity_from(pos) or Decimal("0"),
                    "mark": Decimal(str(getattr(pos, "mark_price", "0"))),
                    "entry": Decimal(str(getattr(pos, "entry_price", "0"))),
                }
            except Exception as exc:
                logger.error(
                    "Failed to parse position data for positions_dict",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="collect_runtime_guard_state",
                    symbol=getattr(pos, "symbol", "unknown"),
                )
                continue

        return RuntimeGuardState(
            timestamp=time.time(),
            balances=balances,
            equity=equity,
            positions=positions,
            positions_pnl=positions_pnl,
            positions_dict=positions_dict,
            guard_events=[],
        )

    def run_guard_step(self, guard_name: str, func: Callable[[], None]) -> None:
        """Execute a guard step and apply unified error handling."""
        with trace_span(
            "guard_check",
            {"guard_name": guard_name},
        ) as span:
            try:
                func()
            except GuardError as err:
                record_guard_failure(err)
                if span is not None:
                    span.set_attribute("outcome", "failure")
                    span.set_attribute("error_type", type(err).__name__)
                    span.set_attribute("recoverable", err.recoverable)
                if err.recoverable:
                    return
                raise
            except Exception as exc:
                fallback_err = RiskGuardComputationError(
                    guard_name=guard_name,
                    message=f"Unexpected failure in guard '{guard_name}'",
                    details={},
                )
                record_guard_failure(fallback_err)
                if span is not None:
                    span.set_attribute("outcome", "failure")
                    span.set_attribute("error_type", type(exc).__name__)
                raise fallback_err
            else:
                record_guard_success(guard_name)
                if span is not None:
                    span.set_attribute("outcome", "success")

    def run_guards_for_state(self, state: RuntimeGuardState, incremental: bool) -> None:
        """Run all guards for the given state."""
        for guard in self._guards:
            self.run_guard_step(
                guard.name,
                lambda g=guard: g.check(state, incremental),  # type: ignore[misc]
            )

    def _prepare_cached_state(self, state: RuntimeGuardState) -> RuntimeGuardState:
        """Create a cache-safe state snapshot without per-run guard events."""
        if is_dataclass(state):
            return cast(RuntimeGuardState, replace(state, guard_events=[]))
        try:
            setattr(state, "guard_events", [])
        except Exception:
            pass
        return state

    def run_runtime_guards(self, force_full: bool = False) -> RuntimeGuardState:
        """
        Execute runtime guards and return the guard state.

        Args:
            force_full: Force a full guard run even if cached

        Returns:
            RuntimeGuardState snapshot after guard execution
        """
        now = time.time()
        incremental = not force_full and not self._cache.should_run_full(now)

        if not incremental or self._cache.state is None:
            state = self.collect_runtime_guard_state()
        else:
            state = self._cache.state

        invalidation_count_before_run = self._cache.invalidation_count
        self.run_guards_for_state(state, incremental)

        if self._cache.invalidation_count != invalidation_count_before_run:
            return state

        cached_state = self._prepare_cached_state(state)
        self._cache.update(cached_state, now, update_last_full_ts=not incremental)
        return state

    def cancel_all_orders(self) -> int:
        """
        Cancel all open orders (used on risk trips).

        Returns:
            Number of orders cancelled
        """
        cancelled = 0

        order_ids, synced = self._fetch_open_order_ids()
        if synced:
            self.open_orders[:] = order_ids

        for order_id in list(order_ids):  # Copy list to avoid modification during iteration
            try:
                did_cancel = self._broker_executor.cancel_order(
                    order_id,
                    use_retry=self._cancel_retries_enabled,
                    allow_idempotent=True,
                )
                if did_cancel:
                    cancelled += 1
                    if order_id in self.open_orders:
                        self.open_orders.remove(order_id)
                    logger.info(
                        "Cancelled order",
                        order_id=order_id,
                        operation="order_cancel",
                        stage="single",
                    )
                else:
                    logger.warning(
                        "Cancel returned false",
                        order_id=order_id,
                        operation="order_cancel",
                        stage="single",
                    )
            except Exception as e:
                logger.error(
                    "Failed to cancel order %s: %s",
                    order_id,
                    e,
                    operation="order_cancel",
                    stage="single",
                    order_id=order_id,
                )

        if cancelled > 0:
            logger.info(
                "Cancelled open orders due to risk trip",
                cancelled=cancelled,
                operation="order_cancel",
                stage="bulk",
            )
            self._cache.invalidate()

        return cancelled

    def safe_run_runtime_guards(self, force_full: bool = False) -> None:
        """
        Run runtime risk guards and take action if needed.
        Handles exceptions and fallback logic.
        """
        try:
            self.run_runtime_guards(force_full=force_full)

        except GuardError as err:
            level = logging.WARNING if err.recoverable else logging.ERROR
            logger.log(
                level,
                "Runtime guard failure: %s",
                err,
                exc_info=not err.recoverable,
                operation="runtime_guards",
                stage="guard_failure",
                recoverable=err.recoverable,
            )
            if not err.recoverable:
                try:
                    self.risk_manager.set_reduce_only_mode(True, reason="guard_failure")
                except Exception:
                    logger.warning(
                        "Failed to set reduce-only mode after guard failure",
                        exc_info=True,
                        operation="runtime_guards",
                        stage="reduce_only",
                    )
                self._cache.invalidate()

        except Exception as exc:
            logger.error(
                "Runtime guards error: %s",
                exc,
                operation="runtime_guards",
                stage="exception",
            )

    # Backward compatibility: legacy method names that delegate to guards
    def log_guard_telemetry(self, state: RuntimeGuardState) -> None:
        """Log P&L telemetry for all positions."""
        PnLTelemetryGuard().check(state)

    def guard_daily_loss(self, state: RuntimeGuardState) -> None:
        """Check daily loss limits and cancel orders if breached."""
        self._guards[1].check(state)  # DailyLossGuard is at index 1

    def guard_liquidation_buffers(self, state: RuntimeGuardState, incremental: bool) -> None:
        """Check liquidation price buffers for all positions."""
        self._guards[2].check(state, incremental)  # LiquidationBufferGuard

    def guard_mark_staleness(self, state: RuntimeGuardState) -> None:
        """Check if mark prices are stale."""
        self._guards[3].check(state)  # MarkStalenessGuard

    def guard_risk_metrics(self, state: RuntimeGuardState) -> None:
        """Append risk metrics for monitoring."""
        self._guards[4].check(state)  # RiskMetricsGuard

    def guard_volatility(self, state: RuntimeGuardState) -> None:
        """Check volatility circuit breakers."""
        self._guards[5].check(state)  # VolatilityGuard

    def guard_api_health(self, state: RuntimeGuardState) -> None:
        """Check API health (error rate, rate limits, circuit breakers)."""
        self._guards[6].check(state)  # ApiHealthGuard
