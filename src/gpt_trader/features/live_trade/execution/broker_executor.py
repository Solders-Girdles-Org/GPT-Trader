"""
Broker order execution for live trading.

This module handles the actual communication with the broker API,
including retry/timeout policies for resilient order submission.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypeVar

from gpt_trader.core import NotFoundError, OrderSide, OrderType, RateLimitError
from gpt_trader.monitoring.metrics_collector import record_histogram
from gpt_trader.utilities.backoff_policy import evaluate_backoff_delay
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.core.protocols import BrokerProtocol

logger = get_logger(__name__, component="broker_executor")

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for retry behavior on broker calls.

    Attributes:
        max_attempts: Maximum number of attempts (1 = no retries).
        base_delay: Initial delay between retries in seconds.
        max_delay: Maximum delay between retries in seconds.
        timeout_seconds: Timeout for each attempt (0 = no timeout).
        jitter: Random jitter factor (0.0-1.0) added to delays.
        retryable_exceptions: Exception types that trigger retries.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 5.0
    timeout_seconds: float = 30.0
    jitter: float = 0.1
    retryable_exceptions: tuple[type[Exception], ...] = field(
        default=(TimeoutError, ConnectionError, OSError, RateLimitError)
    )

    def __post_init__(self) -> None:
        """Validate and clamp jitter to [0.0, 1.0]."""
        clamped = max(0.0, min(1.0, self.jitter))
        if clamped != self.jitter:
            # Frozen dataclass requires object.__setattr__
            object.__setattr__(self, "jitter", clamped)

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number (1-indexed)."""
        decision = evaluate_backoff_delay(
            attempt=attempt,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            multiplier=2.0,
            jitter=self.jitter,
        )
        return decision.delay_seconds


# Default policy for order submission
DEFAULT_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=0.5,
    max_delay=5.0,
    timeout_seconds=30.0,
    jitter=0.1,
)


def _record_broker_call_latency(
    latency_seconds: float,
    operation: str,
    outcome: str,
    reason: str = "none",
) -> None:
    """Record broker API call latency histogram.

    Args:
        latency_seconds: Call latency in seconds.
        operation: Operation type (submit, cancel, preview).
        outcome: "success" or "failure".
        reason: Failure reason or "none" for success.
    """
    try:
        record_histogram(
            "gpt_trader_broker_call_latency_seconds",
            latency_seconds,
            labels={
                "operation": operation,
                "outcome": outcome,
                "reason": reason,
            },
        )
    except Exception:
        # Don't let metrics errors affect broker operations
        pass


def _classify_broker_error_reason(error: Exception) -> str:
    message = str(error).lower()
    if isinstance(error, TimeoutError) or "timeout" in message:
        return "timeout"
    if isinstance(error, ConnectionError) or "connection" in message or "network" in message:
        return "network"
    if isinstance(error, RateLimitError) or "rate" in message or "429" in message:
        return "rate_limit"
    if isinstance(error, NotFoundError) or "not found" in message or "unknown order" in message:
        return "not_found"
    return "error"


def _is_cancel_idempotent_error(error: Exception) -> bool:
    if isinstance(error, NotFoundError):
        return True
    message = str(error).lower()
    markers = (
        "not found",
        "unknown order",
        "already canceled",
        "already cancelled",
        "already filled",
        "too late to cancel",
        "order is done",
    )
    return any(marker in message for marker in markers)


def execute_with_retry(
    func: Callable[[], T],
    policy: RetryPolicy,
    *,
    client_order_id: str,
    operation: str = "broker_call",
    sleep_fn: Callable[[float], None] | None = None,
) -> T:
    """Execute a function with retry logic.

    Args:
        func: The function to execute (should be a zero-arg callable).
        policy: Retry policy configuration.
        client_order_id: Client order ID for logging (reused across retries).
        operation: Operation name for logging.
        sleep_fn: Injectable sleep function (defaults to time.sleep).

    Returns:
        The result of func() on success.

    Raises:
        The last exception if all retries are exhausted.
        TimeoutError if the operation times out.
    """
    sleep = sleep_fn or time.sleep
    last_exception: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            # Calculate and apply delay (no delay on first attempt)
            delay = policy.calculate_delay(attempt)
            if delay > 0:
                logger.info(
                    "Retrying broker call",
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                    delay_seconds=round(delay, 3),
                    client_order_id=client_order_id,
                    operation=operation,
                    stage="retry_delay",
                )
                sleep(delay)

            # Execute the function
            # Note: timeout enforcement is expected at the broker/HTTP layer
            # This wrapper handles retry logic; actual timeouts should be
            # configured in the HTTP client or broker implementation
            result = func()

            # Log success after retry
            if attempt > 1:
                logger.info(
                    "Broker call succeeded after retry",
                    attempt=attempt,
                    client_order_id=client_order_id,
                    operation=operation,
                    stage="retry_success",
                )

            return result

        except policy.retryable_exceptions as exc:
            last_exception = exc
            logger.warning(
                "Broker call failed (retryable)",
                attempt=attempt,
                max_attempts=policy.max_attempts,
                error_type=type(exc).__name__,
                error_message=str(exc),
                client_order_id=client_order_id,
                operation=operation,
                stage="retry_failed",
            )

            # Don't sleep after the last attempt
            if attempt >= policy.max_attempts:
                break

        except Exception as exc:
            # Non-retryable exception - re-raise immediately
            logger.error(
                "Broker call failed (non-retryable)",
                attempt=attempt,
                error_type=type(exc).__name__,
                error_message=str(exc),
                client_order_id=client_order_id,
                operation=operation,
                stage="non_retryable",
            )
            raise

    # All retries exhausted
    logger.error(
        "Broker call failed after all retries",
        max_attempts=policy.max_attempts,
        error_type=type(last_exception).__name__ if last_exception else "Unknown",
        client_order_id=client_order_id,
        operation=operation,
        stage="retries_exhausted",
    )
    if last_exception:
        raise last_exception
    raise RuntimeError(f"Broker call failed after {policy.max_attempts} attempts")


class BrokerExecutor:
    """Executes orders against the broker with retry support."""

    def __init__(
        self,
        broker: BrokerProtocol,
        *,
        retry_policy: RetryPolicy | None = None,
        integration_mode: bool = False,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        """
        Initialize broker executor.

        Args:
            broker: Brokerage adapter for order execution.
            retry_policy: Retry policy for broker calls (defaults to DEFAULT_RETRY_POLICY).
            integration_mode: Enable integration test mode (reserved for future use).
            sleep_fn: Injectable sleep function for testing (defaults to time.sleep).
        """
        self._broker = broker
        self._retry_policy = retry_policy or DEFAULT_RETRY_POLICY
        self._integration_mode = integration_mode
        self._sleep_fn = sleep_fn

    def execute_order(
        self,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
        *,
        use_retry: bool = False,
    ) -> Any:
        """
        Execute order placement against the broker.

        Args:
            submit_id: Client order ID (reused across retries).
            symbol: Trading symbol.
            side: Order side (BUY/SELL).
            order_type: Order type (LIMIT/MARKET/etc.).
            quantity: Order quantity.
            price: Limit price (None for market orders).
            stop_price: Stop price for stop orders.
            tif: Time in force.
            reduce_only: Whether order is reduce-only.
            leverage: Leverage multiplier.
            use_retry: Whether to use retry logic (default False for backward compat).

        Returns:
            Order object from broker.
        """
        if use_retry:
            return self._execute_with_retry(
                submit_id=submit_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                tif=tif,
                reduce_only=reduce_only,
                leverage=leverage,
            )

        return self._execute_broker_order(
            submit_id=submit_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            tif=tif,
            reduce_only=reduce_only,
            leverage=leverage,
        )

    def cancel_order(
        self,
        order_id: str,
        *,
        use_retry: bool = False,
        allow_idempotent: bool = True,
    ) -> bool:
        if use_retry:
            return self._execute_cancel_with_retry(
                order_id=order_id,
                allow_idempotent=allow_idempotent,
            )
        return self._execute_broker_cancel(
            order_id=order_id,
            allow_idempotent=allow_idempotent,
        )

    def _execute_broker_order(
        self,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
    ) -> Any:
        """Execute the actual broker call (no retry)."""
        start_time = time.perf_counter()
        try:
            result = self._broker.place_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                tif=tif if tif is not None else None,
                reduce_only=reduce_only,
                leverage=leverage,
                client_id=submit_id,
            )
            latency_seconds = time.perf_counter() - start_time
            _record_broker_call_latency(
                latency_seconds=latency_seconds,
                operation="submit",
                outcome="success",
            )
            return result
        except Exception as exc:
            latency_seconds = time.perf_counter() - start_time
            reason = _classify_broker_error_reason(exc)
            _record_broker_call_latency(
                latency_seconds=latency_seconds,
                operation="submit",
                outcome="failure",
                reason=reason,
            )
            raise

    def _execute_broker_cancel(
        self,
        order_id: str,
        *,
        allow_idempotent: bool,
    ) -> bool:
        start_time = time.perf_counter()
        try:
            result = self._broker.cancel_order(order_id)
            latency_seconds = time.perf_counter() - start_time
            _record_broker_call_latency(
                latency_seconds=latency_seconds,
                operation="cancel",
                outcome="success",
            )
            return bool(result)
        except Exception as exc:
            latency_seconds = time.perf_counter() - start_time
            reason = _classify_broker_error_reason(exc)
            if allow_idempotent and _is_cancel_idempotent_error(exc):
                _record_broker_call_latency(
                    latency_seconds=latency_seconds,
                    operation="cancel",
                    outcome="success",
                    reason="idempotent",
                )
                logger.info(
                    "Cancel treated as idempotent success",
                    order_id=order_id,
                    reason=reason,
                    operation="cancel_order",
                    stage="idempotent",
                )
                return True
            _record_broker_call_latency(
                latency_seconds=latency_seconds,
                operation="cancel",
                outcome="failure",
                reason=reason,
            )
            raise

    def _execute_with_retry(
        self,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
    ) -> Any:
        """Execute broker call with retry logic.

        The same submit_id (client_order_id) is reused across all retry attempts
        to ensure idempotency at the broker level.
        """

        def broker_call() -> Any:
            return self._execute_broker_order(
                submit_id=submit_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                tif=tif,
                reduce_only=reduce_only,
                leverage=leverage,
            )

        return execute_with_retry(
            broker_call,
            self._retry_policy,
            client_order_id=submit_id,
            operation="execute_order",
            sleep_fn=self._sleep_fn,
        )

    def _execute_cancel_with_retry(
        self,
        order_id: str,
        *,
        allow_idempotent: bool,
    ) -> bool:
        def broker_call() -> bool:
            return self._execute_broker_cancel(
                order_id=order_id,
                allow_idempotent=allow_idempotent,
            )

        return execute_with_retry(
            broker_call,
            self._retry_policy,
            client_order_id=order_id,
            operation="cancel_order",
            sleep_fn=self._sleep_fn,
        )


__all__ = [
    "BrokerExecutor",
    "RetryPolicy",
    "DEFAULT_RETRY_POLICY",
    "execute_with_retry",
]
