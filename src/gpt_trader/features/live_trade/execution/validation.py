"""
Order validation logic for live trading execution.

This module handles pre-trade validation including exchange rules,
mark price staleness checks, slippage guards, and order previews.

Error Handling Strategy:
    ValidationError exceptions are always propagated as they indicate
    actual trading rule violations. Generic exceptions from monitoring
    infrastructure (API errors, timeouts) are logged and counted via
    metrics, but do not block trade execution. This prevents monitoring
    failures from causing trade failures.

    The ValidationFailureTracker monitors consecutive failures and can
    trigger reduce-only mode if thresholds are exceeded, providing a
    safety net when validation infrastructure is consistently failing.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, cast, runtime_checkable

from gpt_trader.core import OrderSide, OrderType, Product, TimeInForce
from gpt_trader.core.protocols import BrokerProtocol
from gpt_trader.features.brokerages.coinbase.specs import validate_order as spec_validate_order
from gpt_trader.features.live_trade.risk import ValidationError
from gpt_trader.features.live_trade.risk.protocols import RiskManagerProtocol
from gpt_trader.monitoring.metrics_collector import record_counter
from gpt_trader.utilities.logging_patterns import get_logger
from gpt_trader.utilities.quantization import quantize_price_side_aware

logger = get_logger(__name__, component="order_validation")


# Metric names for validation failures
METRIC_MARK_STALENESS_CHECK_FAILED = "gpt_trader_validation_mark_staleness_check_failed_total"
METRIC_SLIPPAGE_GUARD_CHECK_FAILED = "gpt_trader_validation_slippage_guard_check_failed_total"
METRIC_ORDER_PREVIEW_FAILED = "gpt_trader_validation_order_preview_failed_total"
METRIC_CONSECUTIVE_FAILURES_ESCALATION = "gpt_trader_validation_escalations_total"


@dataclass
class ValidationFailureTracker:
    """Tracks consecutive validation failures and triggers escalation.

    When validation infrastructure fails repeatedly (not actual validation
    rejections, but failures to perform the checks), this tracker can
    trigger reduce-only mode to prevent trading with broken safety checks.

    Attributes:
        consecutive_failures: Count of consecutive failures per check type.
        escalation_threshold: Number of failures before triggering escalation.
        escalation_callback: Optional callback to trigger reduce-only mode.
    """

    consecutive_failures: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    escalation_threshold: int = 5
    escalation_callback: Any = None  # Callable[[], None] | None

    def record_failure(self, check_type: str) -> bool:
        """Record a failure and check if escalation is needed.

        Args:
            check_type: Type of validation check that failed.

        Returns:
            True if escalation was triggered, False otherwise.
        """
        self.consecutive_failures[check_type] += 1
        count = self.consecutive_failures[check_type]

        if count >= self.escalation_threshold:
            logger.warning(
                "Validation check failing repeatedly - triggering escalation",
                check_type=check_type,
                consecutive_failures=count,
                threshold=self.escalation_threshold,
                operation="validation_escalation",
            )
            record_counter(METRIC_CONSECUTIVE_FAILURES_ESCALATION)

            if self.escalation_callback is not None:
                try:
                    self.escalation_callback()
                except Exception as callback_error:
                    logger.error(
                        "Failed to execute escalation callback",
                        error=str(callback_error),
                        check_type=check_type,
                    )
            return True
        return False

    def record_success(self, check_type: str) -> None:
        """Record a successful check, resetting the failure counter.

        Args:
            check_type: Type of validation check that succeeded.
        """
        self.consecutive_failures[check_type] = 0

    def get_failure_count(self, check_type: str) -> int:
        """Get current consecutive failure count for a check type.

        Args:
            check_type: Type of validation check.

        Returns:
            Current consecutive failure count.
        """
        return self.consecutive_failures[check_type]


_failure_tracker_provider: Callable[[], ValidationFailureTracker | None] | None = None


def get_failure_tracker() -> ValidationFailureTracker:
    """Get the failure tracker instance.

    Returns:
        The ValidationFailureTracker instance.

    Raises:
        RuntimeError: If no application container is set.
    """
    if _failure_tracker_provider is None:
        raise RuntimeError(
            "No application container set. Call set_application_container() "
            "before using get_failure_tracker()."
        )
    tracker = _failure_tracker_provider()
    if tracker is None:
        raise RuntimeError(
            "No application container set. Call set_application_container() "
            "before using get_failure_tracker()."
        )
    return tracker


def set_failure_tracker_provider(
    provider: Callable[[], ValidationFailureTracker | None] | None,
) -> None:
    """Set the provider used by compatibility helpers that need the global tracker."""
    global _failure_tracker_provider
    _failure_tracker_provider = provider


def configure_failure_tracker(
    escalation_threshold: int = 5,
    escalation_callback: Any = None,
) -> None:
    """Configure the failure tracker.

    Args:
        escalation_threshold: Number of consecutive failures before escalation.
        escalation_callback: Optional callback to trigger on escalation.
    """
    tracker = get_failure_tracker()
    tracker.escalation_threshold = escalation_threshold
    tracker.escalation_callback = escalation_callback


def get_validation_metrics(tracker: ValidationFailureTracker) -> dict[str, Any]:
    """Get validation failure metrics for monitoring display.

    This function accepts the tracker explicitly for pure DI.
    Business logic should pass the tracker from the container.

    Args:
        tracker: The ValidationFailureTracker instance to read metrics from.

    Returns:
        Dict with keys:
            - failures: Dict mapping check_type to consecutive failure count
            - escalation_threshold: Number of failures before escalation
            - any_escalated: True if any check type has reached threshold
    """
    failures = dict(tracker.consecutive_failures)
    any_escalated = any(count >= tracker.escalation_threshold for count in failures.values())
    return {
        "failures": failures,
        "escalation_threshold": tracker.escalation_threshold,
        "any_escalated": any_escalated,
    }


def get_validation_metrics_from_container() -> dict[str, Any]:
    """Entry-point wrapper that gets tracker from global container.

    Use this only in CLI/entry-point code where DI is not available.
    Business logic should use get_validation_metrics(tracker) directly.

    Returns:
        Dict with validation metrics from the global container's tracker.
    """
    return get_validation_metrics(get_failure_tracker())


class OrderValidator:
    """Validates orders before submission to broker."""

    def __init__(
        self,
        broker: BrokerProtocol,
        risk_manager: RiskManagerProtocol,
        enable_order_preview: bool,
        record_preview_callback: Any,
        record_rejection_callback: Any,
        failure_tracker: ValidationFailureTracker,
        broker_calls: Any | None = None,
    ) -> None:
        """
        Initialize order validator.

        Args:
            broker: Brokerage adapter
            risk_manager: Risk manager instance
            enable_order_preview: Whether to preview orders before submission
            record_preview_callback: Function to record preview results
            record_rejection_callback: Function to record rejections
            failure_tracker: Tracker for consecutive validation failures.
        """
        self.broker = broker
        self.risk_manager = risk_manager
        self.enable_order_preview = enable_order_preview
        self._record_preview = record_preview_callback
        self._record_rejection = record_rejection_callback
        self._failure_tracker = failure_tracker
        self._broker_calls = (
            broker_calls
            if broker_calls is not None
            and asyncio.iscoroutinefunction(getattr(broker_calls, "__call__", None))
            else None
        )

    async def _call_broker(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        broker_calls = self._broker_calls
        if broker_calls is not None:
            return await broker_calls(func, *args, **kwargs)
        return await asyncio.to_thread(func, *args, **kwargs)

    def validate_exchange_rules(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
        product: Product,
    ) -> tuple[Decimal, Decimal | None]:
        """
        Validate order against exchange rules and quantization.

        Args:
            symbol: Trading symbol
            side: Order side
            order_type: Order type
            order_quantity: Order quantity
            price: Order price (None for market orders)
            effective_price: Effective price for validation
            product: Product specifications

        Returns:
            Tuple of (adjusted_quantity, adjusted_price)

        Raises:
            ValidationError: If validation fails
        """
        validator_price: Decimal | None
        if order_type == OrderType.MARKET:
            validator_price = None
        else:
            candidate = price if price is not None else effective_price
            validator_price = Decimal(str(candidate)) if candidate is not None else None

        vr = spec_validate_order(
            product=product,
            side=side.value,
            quantity=order_quantity,
            order_type=order_type.value.lower(),
            price=validator_price,
        )
        if not vr.ok:
            reason_code = vr.reason or "spec_violation"
            self._record_rejection(
                symbol, side.value, order_quantity, price or effective_price, reason_code
            )
            raise ValidationError(f"Spec validation failed: {reason_code}")

        if order_type == OrderType.LIMIT and price is not None:
            price = quantize_price_side_aware(
                Decimal(str(price)), product.price_increment, side.value
            )
        if vr.adjusted_price is not None:
            price = vr.adjusted_price
        if vr.adjusted_quantity is not None:
            order_quantity = vr.adjusted_quantity
        return order_quantity, price

    def ensure_mark_is_fresh(self, symbol: str) -> None:
        """
        Ensure mark price is not stale.

        Args:
            symbol: Trading symbol

        Raises:
            ValidationError: If mark price is stale

        Note:
            Non-ValidationError exceptions are logged and counted via metrics
            but do not block execution. This prevents monitoring failures from
            blocking trades. Use the failure tracker to detect repeated failures.
        """
        try:
            if self.risk_manager.check_mark_staleness(symbol):
                raise ValidationError(f"Mark price is stale for {symbol}; halting order placement")
            # Check succeeded - reset failure counter
            self._failure_tracker.record_success("mark_staleness")
        except ValidationError:
            raise
        except Exception as exc:
            # Record metric for visibility
            record_counter(METRIC_MARK_STALENESS_CHECK_FAILED)
            # Track consecutive failures
            self._failure_tracker.record_failure("mark_staleness")

            logger.error(
                "Failed to check mark price staleness",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="ensure_mark_is_fresh",
                symbol=symbol,
                consecutive_failures=self._failure_tracker.get_failure_count("mark_staleness"),
            )

    def enforce_slippage_guard(
        self,
        symbol: str,
        side: OrderSide,
        order_quantity: Decimal,
        effective_price: Decimal,
    ) -> None:
        """
        Enforce slippage guard to prevent excessive market impact.

        Args:
            symbol: Trading symbol
            side: Order side
            order_quantity: Order quantity
            effective_price: Effective price

        Raises:
            ValidationError: If expected slippage exceeds limit

        Note:
            Non-ValidationError exceptions are logged and counted via metrics
            but do not block execution. This prevents monitoring failures from
            blocking trades. Use the failure tracker to detect repeated failures.
        """
        try:
            snapshot = None
            if hasattr(self.broker, "get_market_snapshot"):
                snapshot = self.broker.get_market_snapshot(symbol)
            if snapshot:
                spread_bps = Decimal(str(snapshot.get("spread_bps", 0)))
                depth_l1 = Decimal(str(snapshot.get("depth_l1", 0)))
                notional = order_quantity * Decimal(str(effective_price))
                depth = depth_l1 if depth_l1 and depth_l1 > 0 else Decimal("1")
                impact_bps = Decimal("10000") * (notional / depth) * Decimal("0.5")
                expected_bps = spread_bps + impact_bps
                if self.risk_manager.config:
                    guard_limit = Decimal(str(self.risk_manager.config.slippage_guard_bps))
                    if expected_bps > guard_limit:
                        raise ValidationError(
                            f"Expected slippage {expected_bps:.0f} bps exceeds guard {guard_limit}"
                        )
            # Check succeeded - reset failure counter
            self._failure_tracker.record_success("slippage_guard")
        except ValidationError:
            raise
        except Exception as exc:
            # Record metric for visibility
            record_counter(METRIC_SLIPPAGE_GUARD_CHECK_FAILED)
            # Track consecutive failures
            self._failure_tracker.record_failure("slippage_guard")

            logger.error(
                "Failed to enforce slippage guard",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="enforce_slippage_guard",
                symbol=symbol,
                side=side.value,
                consecutive_failures=self._failure_tracker.get_failure_count("slippage_guard"),
            )

    def run_pre_trade_validation(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_quantity: Decimal,
        effective_price: Decimal,
        product: Product,
        equity: Decimal,
        current_positions: dict[str, dict[str, Any]],
    ) -> None:
        """
        Run comprehensive pre-trade validation checks.

        Args:
            symbol: Trading symbol
            side: Order side
            order_quantity: Order quantity
            effective_price: Effective price
            product: Product specifications
            equity: Current account equity
            current_positions: Current position map

        Raises:
            ValidationError: If validation fails
        """
        self.risk_manager.pre_trade_validate(
            symbol=symbol,
            side=side.value,
            quantity=order_quantity,
            price=effective_price,
            product=product,
            equity=equity,
            current_positions=current_positions,
        )

    async def maybe_preview_order_async(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        effective_price: Decimal,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
    ) -> None:
        """
        Preview order if enabled (asynchronous).

        Args:
            symbol: Trading symbol
            side: Order side
            order_type: Order type
            order_quantity: Order quantity
            effective_price: Effective price
            stop_price: Stop price for stop orders
            tif: Time in force
            reduce_only: Whether order is reduce-only
            leverage: Leverage multiplier

        Note:
            Preview failures are informational and do not block order execution.
            Non-ValidationError exceptions are logged at debug level and counted
            via metrics for monitoring purposes.
        """
        if not self.enable_order_preview:
            logger.debug(
                "Order preview disabled",
                operation="order_preview",
                stage="disabled",
            )
            return
        broker = self.broker
        if not isinstance(broker, _PreviewBroker):
            logger.debug(
                "Broker does not support order preview",
                operation="order_preview",
                stage="unsupported",
            )
            return
        preview_broker = cast(_PreviewBroker, broker)
        try:
            tif_value = tif if isinstance(tif, TimeInForce) else (tif or TimeInForce.GTC)

            preview_data = await self._call_broker(
                preview_broker.preview_order,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=order_quantity,
                price=effective_price,
                stop_price=stop_price,
                tif=tif_value,
                reduce_only=reduce_only,
                leverage=leverage,
            )

            self._record_preview(
                symbol, side, order_type, order_quantity, effective_price, preview_data
            )
            # Preview succeeded - reset failure counter
            self._failure_tracker.record_success("order_preview")
        except ValidationError:
            raise
        except Exception as exc:
            # Record metric for visibility (preview failures are less critical)
            record_counter(METRIC_ORDER_PREVIEW_FAILED)
            # Track consecutive failures (but don't trigger escalation for preview)
            self._failure_tracker.record_failure("order_preview")

            logger.debug(
                "Preview call failed",
                error=str(exc),
                operation="order_preview",
                stage="error",
                consecutive_failures=self._failure_tracker.get_failure_count("order_preview"),
            )

    def maybe_preview_order(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        effective_price: Decimal,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
    ) -> None:
        """
        Preview order if enabled.

        Args:
            symbol: Trading symbol
            side: Order side
            order_type: Order type
            order_quantity: Order quantity
            effective_price: Effective price
            stop_price: Stop price for stop orders
            tif: Time in force
            reduce_only: Whether order is reduce-only
            leverage: Leverage multiplier

        Note:
            Preview failures are informational and do not block order execution.
            Non-ValidationError exceptions are logged at debug level and counted
            via metrics for monitoring purposes.
        """
        if not self.enable_order_preview:
            logger.debug(
                "Order preview disabled",
                operation="order_preview",
                stage="disabled",
            )
            return
        broker = self.broker
        if not isinstance(broker, _PreviewBroker):
            logger.debug(
                "Broker does not support order preview",
                operation="order_preview",
                stage="unsupported",
            )
            return
        preview_broker = cast(_PreviewBroker, broker)
        try:
            tif_value = tif if isinstance(tif, TimeInForce) else (tif or TimeInForce.GTC)
            preview_data = preview_broker.preview_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=order_quantity,
                price=effective_price,
                stop_price=stop_price,
                tif=tif_value,
                reduce_only=reduce_only,
                leverage=leverage,
            )
            self._record_preview(
                symbol, side, order_type, order_quantity, effective_price, preview_data
            )
            # Preview succeeded - reset failure counter
            self._failure_tracker.record_success("order_preview")
        except ValidationError:
            raise
        except Exception as exc:
            # Record metric for visibility (preview failures are less critical)
            record_counter(METRIC_ORDER_PREVIEW_FAILED)
            # Track consecutive failures (but don't trigger escalation for preview)
            self._failure_tracker.record_failure("order_preview")

            logger.debug(
                "Preview call failed",
                error=str(exc),
                operation="order_preview",
                stage="error",
                consecutive_failures=self._failure_tracker.get_failure_count("order_preview"),
            )

    def finalize_reduce_only_flag(self, reduce_only: bool, symbol: str) -> bool:
        """
        Finalize reduce-only flag based on risk manager state.

        Args:
            reduce_only: User-requested reduce-only flag
            symbol: Trading symbol

        Returns:
            Final reduce-only flag value
        """
        if self.risk_manager.is_reduce_only_mode():
            logger.info(
                "Reduce-only mode active - forcing reduce_only=True",
                symbol=symbol,
                operation="reduce_only",
                stage="enforce",
            )
            return True
        return reduce_only


@runtime_checkable
class _PreviewBroker(Protocol):
    def preview_order(self, **kwargs: Any) -> Any: ...

    def edit_order_preview(self, order_id: str, **kwargs: Any) -> Any: ...
