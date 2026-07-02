"""
Order submission and recording for live trading execution.

This module handles the actual submission of orders to the broker and
recording of order events, previews, and rejections.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

from gpt_trader.app.protocols import EventStoreProtocol
from gpt_trader.core import OrderSide, OrderType
from gpt_trader.core.protocols import BrokerProtocol
from gpt_trader.features.live_trade.execution.broker_executor import BrokerExecutor, RetryPolicy
from gpt_trader.features.live_trade.execution.decision_trace import OrderDecisionTrace
from gpt_trader.features.live_trade.execution.order_event_recorder import OrderEventRecorder
from gpt_trader.features.live_trade.execution.rejection_reason import (
    normalize_rejection_reason,
)
from gpt_trader.features.live_trade.execution.status_codec import (
    ExecutionStatusCodecError,
    execution_status_for_store,
)
from gpt_trader.features.live_trade.execution.submission_outcome import (
    OrderSubmissionOutcome,
    OrderSubmissionOutcomeStatus,
)
from gpt_trader.logging.correlation import order_context
from gpt_trader.monitoring.metrics_collector import (
    record_counter,
    record_histogram,
    record_trade_blocked,
    record_trade_executed,
)
from gpt_trader.monitoring.tracing import trace_span
from gpt_trader.persistence.orders_store import (
    OrderRecord,
    OrdersStore,
)
from gpt_trader.persistence.orders_store import (
    OrderStatus as StoreOrderStatus,
)
from gpt_trader.utilities.logging_patterns import get_logger
from gpt_trader.utilities.time_provider import TimeProvider, get_clock

logger = get_logger(__name__, component="order_submission")

SUBMISSION_RETRY_POLICY = RetryPolicy(
    max_attempts=2,
    base_delay=0.25,
    max_delay=1.0,
    jitter=0.0,
)
TRANSIENT_SUBMISSION_REASONS = {"timeout", "network", "rate_limit"}
MISSING_DECISION_ID_METRIC = "gpt_trader_order_missing_decision_id_total"


def _classify_rejection_reason(status_or_error: str) -> str:
    """Classify a rejection or error into a standardized reason category."""
    return normalize_rejection_reason(status_or_error)[0]


def _is_transient_submission_error(error: Exception, reason: str | None = None) -> bool:
    """Return True if the error is transient and worth retrying."""
    if isinstance(error, SUBMISSION_RETRY_POLICY.retryable_exceptions):
        return True
    if error.__class__.__name__ == "TransientBrokerError":
        return True
    normalized_reason = reason or normalize_rejection_reason(str(error))[0]
    return normalized_reason in TRANSIENT_SUBMISSION_REASONS


def _record_execution_telemetry(
    latency_ms: float,
    success: bool,
    rejected: bool = False,
    failure_reason: str = "",
    rejection_reason: str = "",
    reason_detail: str | None = None,
    symbol: str = "",
    side: str = "",
    quantity: float = 0.0,
    price: float = 0.0,
) -> None:
    """Record execution telemetry for the order submission.

    This is a best-effort operation - failures are silently ignored
    to avoid impacting order execution.

    Args:
        latency_ms: Time from submission to response.
        success: Whether the submission succeeded.
        rejected: Whether broker rejected the order.
        failure_reason: Human-readable failure reason.
        rejection_reason: Categorized reason for rejection/failure (normalized).
        reason_detail: Additional context for rejection/failure (optional).
        symbol: Order symbol.
        side: Order side (BUY/SELL).
        quantity: Order quantity.
        price: Order price.
    """
    try:
        from gpt_trader.features.live_trade.telemetry import get_execution_telemetry

        collector = get_execution_telemetry()
        collector.record_submission(
            latency_ms=latency_ms,
            success=success,
            rejected=rejected,
            failure_reason=failure_reason,
            rejection_reason=rejection_reason,
            reason_detail=reason_detail or "",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
        )
    except Exception:
        # Don't let telemetry errors affect order execution
        pass


def _record_order_submission_metric(
    result: str,
    reason: str,
    side: str,
) -> None:
    """Record order submission metric with labels.

    Args:
        result: "success", "rejected", "failed", or "error"
        reason: Rejection/failure reason or "none" for success (normalized)
        side: "buy" or "sell"
    """
    try:
        record_counter(
            "gpt_trader_order_submission_total",
            labels={
                "result": result,
                "reason": reason,
                "side": side.lower(),
            },
        )
    except Exception:
        # Don't let metrics errors affect order execution
        pass


def _record_order_submission_latency(
    latency_seconds: float,
    result: str,
    side: str,
) -> None:
    """Record order submission latency histogram.

    Args:
        latency_seconds: Total submission latency in seconds.
        result: "success", "rejected", or "failed"
        side: "buy" or "sell"
    """
    try:
        record_histogram(
            "gpt_trader_order_submission_latency_seconds",
            latency_seconds,
            labels={
                "result": result,
                "side": side.lower(),
            },
        )
    except Exception:
        # Don't let metrics errors affect order execution
        pass


class OrderSubmitter:
    """Handles order submission and event recording."""

    def __init__(
        self,
        broker: BrokerProtocol,
        event_store: EventStoreProtocol,
        bot_id: str,
        open_orders: list[str],
        *,
        enable_retries: bool = False,
        orders_store: OrdersStore | None = None,
        integration_mode: bool = False,
        sleep_fn: Callable[[float], None] | None = None,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """
        Initialize order submitter.

        Args:
            broker: Brokerage adapter
            event_store: Event store for recording
            bot_id: Bot identifier
            open_orders: List to track open order IDs
            enable_retries: Enable one retry for transient submission failures.
            integration_mode: Enable integration test mode
            sleep_fn: Optional sleep function for retry backoff (defaults to time.sleep).
            time_provider: Optional time provider for deterministic timing.
        """
        self.broker = broker
        self.event_store = event_store
        self.bot_id = bot_id
        self.open_orders = open_orders
        self.integration_mode = integration_mode
        self.orders_store = orders_store
        self._enable_retries = enable_retries
        self._sleep_fn = sleep_fn or time.sleep
        self._time_provider = time_provider or get_clock()
        self._event_recorder = OrderEventRecorder(event_store, bot_id)
        self._broker_executor = BrokerExecutor(
            cast(BrokerProtocol, broker),
            integration_mode=integration_mode,
            retry_policy=SUBMISSION_RETRY_POLICY,
            sleep_fn=sleep_fn,
        )
        if self.orders_store is not None:
            try:
                self.orders_store.initialize()
            except Exception as exc:
                logger.warning(
                    "Failed to initialize orders store",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="orders_store_init",
                )

    @staticmethod
    def _normalize_side(side: OrderSide | str) -> str:
        value = side.value if isinstance(side, OrderSide) else str(side)
        return value.lower()

    @staticmethod
    def _normalize_decision_id(decision_id: str | None) -> str | None:
        if decision_id is None:
            return None
        normalized = str(decision_id).strip()
        return normalized if normalized else None

    @staticmethod
    def _normalize_order_type(order_type: OrderType | str) -> str:
        value = order_type.value if isinstance(order_type, OrderType) else str(order_type)
        return value.lower()

    @staticmethod
    def _normalize_status(status: Any) -> StoreOrderStatus:
        return execution_status_for_store(
            status,
            context="order_submission:_normalize_status",
        )

    @staticmethod
    def _normalize_tif(tif: Any) -> str:
        if tif is None:
            return "GTC"
        value = tif.value if hasattr(tif, "value") else tif
        return str(value)

    @staticmethod
    def _normalize_timestamp(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _ensure_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    def _get_existing_record(self, client_order_id: str) -> OrderRecord | None:
        if self.orders_store is None:
            return None
        try:
            return self.orders_store.get_order_by_client_order_id(client_order_id)
        except Exception as exc:
            logger.warning(
                "Failed to fetch order record",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="orders_store_lookup",
                client_order_id=client_order_id,
            )
            return None

    def _persist_order(self, record: OrderRecord) -> None:
        if self.orders_store is None:
            return
        try:
            self.orders_store.upsert_by_client_id(record)
        except Exception as exc:
            logger.warning(
                "Failed to persist order record",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="orders_store_persist",
                order_id=record.order_id,
                client_order_id=record.client_order_id,
            )

    def _build_submission_record(
        self,
        *,
        order_id: str,
        client_order_id: str,
        symbol: str,
        side: OrderSide | str,
        order_type: OrderType | str,
        quantity: Decimal,
        price: Decimal | None,
        status: StoreOrderStatus,
        filled_quantity: Decimal | None = None,
        average_fill_price: Decimal | None = None,
        tif: Any | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> OrderRecord:
        created = self._normalize_timestamp(created_at)
        updated = self._normalize_timestamp(updated_at or created)
        return OrderRecord(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=self._normalize_side(side),
            order_type=self._normalize_order_type(order_type),
            quantity=quantity,
            price=price,
            status=status,
            filled_quantity=filled_quantity if filled_quantity is not None else Decimal("0"),
            average_fill_price=average_fill_price,
            created_at=created,
            updated_at=updated,
            bot_id=self.bot_id,
            time_in_force=self._normalize_tif(tif),
            metadata=metadata,
        )

    def _record_pending_submission(
        self,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
    ) -> None:
        metadata: dict[str, Any] = {"reduce_only": reduce_only}
        if leverage is not None:
            metadata["leverage"] = leverage
        record = self._build_submission_record(
            order_id=submit_id,
            client_order_id=submit_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=order_quantity,
            price=price,
            status=StoreOrderStatus.PENDING,
            tif=tif,
            metadata=metadata,
        )
        self._persist_order(record)

    def _record_final_submission(
        self,
        order: Any,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        tif: Any | None,
        status: StoreOrderStatus,
        reduce_only: bool,
        leverage: int | None,
    ) -> None:
        metadata: dict[str, Any] = {"reduce_only": reduce_only}
        if leverage is not None:
            metadata["leverage"] = leverage
        record = self._build_submission_record(
            order_id=str(getattr(order, "id", submit_id)),
            client_order_id=str(getattr(order, "client_id", submit_id) or submit_id),
            symbol=str(getattr(order, "symbol", symbol)),
            side=getattr(order, "side", side),
            order_type=getattr(order, "type", order_type),
            quantity=self._ensure_decimal(getattr(order, "quantity", order_quantity)),
            price=getattr(order, "price", price),
            status=status,
            filled_quantity=self._ensure_decimal(getattr(order, "filled_quantity", None)),
            average_fill_price=getattr(order, "avg_fill_price", None),
            tif=getattr(order, "tif", tif),
            metadata=metadata,
            created_at=getattr(order, "created_at", None) or getattr(order, "submitted_at", None),
            updated_at=getattr(order, "updated_at", None),
        )
        self._persist_order(record)

    def _record_failed_submission(
        self,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
        status: StoreOrderStatus = StoreOrderStatus.FAILED,
    ) -> None:
        metadata: dict[str, Any] = {"reduce_only": reduce_only}
        if leverage is not None:
            metadata["leverage"] = leverage
        record = self._build_submission_record(
            order_id=submit_id,
            client_order_id=submit_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=order_quantity,
            price=price,
            status=status,
            tif=tif,
            metadata=metadata,
        )
        self._persist_order(record)

    def _handle_existing_submission_outcome(
        self,
        record: OrderRecord,
        *,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
    ) -> OrderSubmissionOutcome:
        order_id = record.order_id or submit_id
        status = record.status
        if status in {
            StoreOrderStatus.PENDING,
            StoreOrderStatus.OPEN,
            StoreOrderStatus.PARTIALLY_FILLED,
        }:
            if order_id not in self.open_orders:
                self.open_orders.append(order_id)
            logger.info(
                "Idempotent submission hit",
                order_id=order_id,
                client_order_id=submit_id,
                symbol=symbol,
                side=side.value,
                order_type=self._normalize_order_type(order_type),
                status=status.value,
                operation="order_submit",
                stage="idempotent_open",
            )
            return OrderSubmissionOutcome(
                status=OrderSubmissionOutcomeStatus.SUCCESS,
                order_id=order_id,
            )
        if status is StoreOrderStatus.FILLED:
            logger.info(
                "Idempotent submission hit for filled order",
                order_id=order_id,
                client_order_id=submit_id,
                symbol=symbol,
                side=side.value,
                order_type=self._normalize_order_type(order_type),
                status=status.value,
                operation="order_submit",
                stage="idempotent_filled",
            )
            return OrderSubmissionOutcome(
                status=OrderSubmissionOutcomeStatus.SUCCESS,
                order_id=order_id,
            )
        if status in {
            StoreOrderStatus.CANCELLED,
            StoreOrderStatus.REJECTED,
            StoreOrderStatus.EXPIRED,
            StoreOrderStatus.FAILED,
        }:
            reason = f"broker_status_{status.value}"
            self.record_rejection(
                symbol,
                side.value,
                order_quantity,
                price if price is not None else effective_price,
                reason,
                client_order_id=submit_id,
            )
            logger.warning(
                "Idempotent submission blocked by terminal status",
                order_id=order_id,
                client_order_id=submit_id,
                symbol=symbol,
                side=side.value,
                order_type=self._normalize_order_type(order_type),
                status=status.value,
                operation="order_submit",
                stage="idempotent_terminal",
            )
            normalized_reason, reason_detail = normalize_rejection_reason(reason)
            return OrderSubmissionOutcome(
                status=OrderSubmissionOutcomeStatus.REJECTED,
                order_id=order_id,
                reason=normalized_reason,
                reason_detail=reason_detail,
            )
        return OrderSubmissionOutcome(
            status=OrderSubmissionOutcomeStatus.FAILED,
            order_id=order_id,
            reason="unknown",
            reason_detail=str(status),
        )

    def _handle_existing_submission(
        self,
        record: OrderRecord,
        *,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
    ) -> str | None:
        outcome = self._handle_existing_submission_outcome(
            record,
            submit_id=submit_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            order_quantity=order_quantity,
            price=price,
            effective_price=effective_price,
        )
        if self.integration_mode and outcome.order is not None:
            return cast(str | None, outcome.order)
        if outcome.success:
            return outcome.order_id
        return None

    def generate_client_order_id(self, client_order_id: str | None = None) -> str:
        """Generate a stable client order ID using submission rules."""
        return self._generate_submit_id(client_order_id)

    def record_preview(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
        preview: dict[str, Any] | None,
    ) -> None:
        """Record order preview for analysis."""
        self._event_recorder.record_preview(symbol, side, order_type, quantity, price, preview)

    def record_rejection(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal | None,
        reason: str,
        *,
        client_order_id: str | None = None,
    ) -> None:
        """Record order rejection for analysis.

        Args:
            symbol: Trading symbol.
            side: Order side (buy/sell).
            quantity: Order quantity.
            price: Order price (None for market orders).
            reason: Rejection reason.
            client_order_id: Client order ID for tracking (optional).
        """
        self._event_recorder.record_rejection(
            symbol, side, quantity, price, reason, client_order_id=client_order_id
        )

    def record_decision_trace(self, trace: OrderDecisionTrace) -> None:
        """Record decision trace for later analysis."""
        self._event_recorder.record_decision_trace(trace)

    def _reject_missing_decision_id(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_quantity: Decimal,
        price: Decimal | None,
    ) -> OrderSubmissionOutcome:
        side_str = side.value if hasattr(side, "value") else str(side)
        logger.error(
            "Order submission blocked: missing decision_id",
            symbol=symbol,
            side=side_str,
            quantity=float(order_quantity),
            price=float(price) if price is not None else None,
            operation="order_submit",
            stage="missing_decision_id",
        )
        try:
            record_counter(MISSING_DECISION_ID_METRIC)
        except Exception:
            pass
        try:
            record_trade_blocked()
        except Exception:
            pass
        self.record_rejection(
            symbol,
            side_str,
            order_quantity,
            price,
            "missing_decision_id",
        )
        _record_order_submission_metric(
            result="rejected",
            reason="missing_decision_id",
            side=side_str,
        )
        return OrderSubmissionOutcome(
            status=OrderSubmissionOutcomeStatus.REJECTED,
            reason="missing_decision_id",
            reason_detail="client_order_id_required",
        )

    def submit_order(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
        client_order_id: str | None,
    ) -> str | None:
        """
        Submit order to broker and record the result.

        Args:
            symbol: Trading symbol
            side: Order side
            order_type: Order type
            order_quantity: Order quantity
            price: Order price (None for market)
            effective_price: Effective price for recording
            stop_price: Stop price for stop orders
            tif: Time in force
            reduce_only: Whether order is reduce-only
            leverage: Leverage multiplier
            client_order_id: Decision-linked client order ID (required)

        Returns:
            Order ID if successful, None otherwise
        """
        outcome = self.submit_order_with_result(
            symbol=symbol,
            side=side,
            order_type=order_type,
            order_quantity=order_quantity,
            price=price,
            effective_price=effective_price,
            stop_price=stop_price,
            tif=tif,
            reduce_only=reduce_only,
            leverage=leverage,
            client_order_id=client_order_id,
        )
        if self.integration_mode and outcome.order is not None:
            return cast(str | None, outcome.order)
        if outcome.success:
            return outcome.order_id
        return None

    def submit_order_with_result(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
        client_order_id: str | None,
    ) -> OrderSubmissionOutcome:
        """
        Submit order to broker and record the result.

        Args:
            symbol: Trading symbol
            side: Order side
            order_type: Order type
            order_quantity: Order quantity
            price: Order price (None for market)
            effective_price: Effective price for recording
            stop_price: Stop price for stop orders
            tif: Time in force
            reduce_only: Whether order is reduce-only
            leverage: Leverage multiplier
            client_order_id: Decision-linked client order ID (required)

        Returns:
            Structured outcome describing success/rejection/failure.
        """
        decision_id = self._normalize_decision_id(client_order_id)
        if decision_id is None:
            return self._reject_missing_decision_id(
                symbol=symbol,
                side=side,
                order_quantity=order_quantity,
                price=price,
            )

        submit_id = self._generate_submit_id(decision_id)

        with order_context(order_id=submit_id, symbol=symbol):
            with trace_span(
                "order_submit",
                {
                    "bot_id": self.bot_id,
                    "client_order_id": submit_id,
                    "symbol": symbol,
                    "side": side.value if hasattr(side, "value") else str(side),
                    "order_type": (
                        order_type.value if hasattr(order_type, "value") else str(order_type)
                    ),
                    "quantity": float(order_quantity),
                    "reduce_only": reduce_only,
                },
            ):
                return self._submit_order_inner_result(
                    submit_id=submit_id,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    order_quantity=order_quantity,
                    price=price,
                    effective_price=effective_price,
                    stop_price=stop_price,
                    tif=tif,
                    reduce_only=reduce_only,
                    leverage=leverage,
                )

    def _submit_order_inner_result(
        self,
        *,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
    ) -> OrderSubmissionOutcome:
        """Inner order submission logic wrapped in correlation context."""
        existing_record = self._get_existing_record(submit_id)
        if existing_record is not None:
            known_statuses = {
                StoreOrderStatus.PENDING,
                StoreOrderStatus.OPEN,
                StoreOrderStatus.PARTIALLY_FILLED,
                StoreOrderStatus.FILLED,
                StoreOrderStatus.CANCELLED,
                StoreOrderStatus.REJECTED,
                StoreOrderStatus.EXPIRED,
                StoreOrderStatus.FAILED,
            }
            if existing_record.status in known_statuses:
                return self._handle_existing_submission_outcome(
                    existing_record,
                    submit_id=submit_id,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    order_quantity=order_quantity,
                    price=price,
                    effective_price=effective_price,
                )
            logger.warning(
                "Order record has unexpected status",
                client_order_id=submit_id,
                order_id=existing_record.order_id,
                status=getattr(existing_record.status, "value", str(existing_record.status)),
                symbol=symbol,
                side=side.value,
                operation="order_submit",
                stage="idempotent_unknown",
            )
        self._log_submission_attempt(submit_id, symbol, side, order_type, order_quantity, price)
        self._record_pending_submission(
            submit_id,
            symbol,
            side,
            order_type,
            order_quantity,
            price,
            tif,
            reduce_only,
            leverage,
        )

        start_time = self._time_provider.monotonic()
        max_attempts = SUBMISSION_RETRY_POLICY.max_attempts if self._enable_retries else 1

        for attempt in range(1, max_attempts + 1):
            try:
                order = self._execute_broker_order(
                    submit_id=submit_id,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    order_quantity=order_quantity,
                    price=price,
                    stop_price=stop_price,
                    tif=tif,
                    reduce_only=reduce_only,
                    leverage=leverage,
                    use_retry=False,
                )
                latency_ms = (self._time_provider.monotonic() - start_time) * 1000

                result = self._handle_order_result(
                    order,
                    symbol,
                    side,
                    order_type,
                    order_quantity,
                    price,
                    effective_price,
                    tif,
                    reduce_only,
                    leverage,
                    submit_id,
                )

                side_str = side.value if hasattr(side, "value") else str(side)
                price_float = float(price) if price is not None else 0.0
                qty_float = float(order_quantity)
                latency_seconds = latency_ms / 1000.0

                if result is not None:
                    order_id: str | None = None
                    order_payload: Any | None = None
                    if self.integration_mode:
                        order_payload = result
                        raw_order_id = getattr(result, "id", None)
                        if raw_order_id is not None:
                            order_id = str(raw_order_id)
                    else:
                        order_id = cast(str, result)
                    if attempt > 1:
                        logger.info(
                            "Order submission succeeded after retry",
                            attempt=attempt,
                            max_attempts=max_attempts,
                            client_order_id=submit_id,
                            order_id=order_id,
                            operation="order_submit",
                            stage="retry_success",
                        )
                    _record_execution_telemetry(
                        latency_ms=latency_ms,
                        success=True,
                        symbol=symbol,
                        side=side_str,
                        quantity=qty_float,
                        price=price_float,
                    )
                    _record_order_submission_metric(
                        result="success",
                        reason="none",
                        side=side_str,
                    )
                    try:
                        record_trade_executed()
                    except Exception:
                        pass
                    _record_order_submission_latency(
                        latency_seconds=latency_seconds,
                        result="success",
                        side=side_str,
                    )
                    return OrderSubmissionOutcome(
                        status=OrderSubmissionOutcomeStatus.SUCCESS,
                        order_id=order_id,
                        order=order_payload,
                    )
                if attempt > 1:
                    logger.warning(
                        "Order submission rejected after retry",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        client_order_id=submit_id,
                        operation="order_submit",
                        stage="retry_rejected",
                    )
                _record_execution_telemetry(
                    latency_ms=latency_ms,
                    success=False,
                    rejected=True,
                    rejection_reason="broker_rejected",
                    symbol=symbol,
                    side=side_str,
                    quantity=qty_float,
                    price=price_float,
                )
                _record_order_submission_metric(
                    result="rejected",
                    reason="broker_rejected",
                    side=side_str,
                )
                _record_order_submission_latency(
                    latency_seconds=latency_seconds,
                    result="rejected",
                    side=side_str,
                )
                return OrderSubmissionOutcome(
                    status=OrderSubmissionOutcomeStatus.REJECTED,
                    reason="broker_rejected",
                )

            except Exception as exc:
                error_str = str(exc)
                reason, reason_detail = normalize_rejection_reason(error_str)

                if attempt < max_attempts and _is_transient_submission_error(exc, reason):
                    delay_seconds = SUBMISSION_RETRY_POLICY.calculate_delay(attempt + 1)
                    logger.warning(
                        "Transient order submission failure, retrying",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        delay_seconds=round(delay_seconds, 3),
                        error_type=type(exc).__name__,
                        error_message=error_str,
                        client_order_id=submit_id,
                        operation="order_submit",
                        stage="retry_wait",
                    )
                    self._sleep_fn(delay_seconds)
                    continue

                latency_ms = (self._time_provider.monotonic() - start_time) * 1000
                is_rejection = reason in {"broker_rejected", "broker_status"}

                side_str = side.value if hasattr(side, "value") else str(side)
                price_float = float(price) if price is not None else 0.0
                qty_float = float(order_quantity)

                if attempt > 1:
                    logger.error(
                        "Order submission failed after retry",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error_type=type(exc).__name__,
                        error_message=error_str,
                        client_order_id=submit_id,
                        operation="order_submit",
                        stage="retry_failed",
                    )

                _record_execution_telemetry(
                    latency_ms=latency_ms,
                    success=False,
                    rejected=is_rejection,
                    failure_reason=error_str[:100],
                    rejection_reason=reason,
                    reason_detail=reason_detail,
                    symbol=symbol,
                    side=side_str,
                    quantity=qty_float,
                    price=price_float,
                )
                metric_result = "rejected" if is_rejection else "failed"
                _record_order_submission_metric(
                    result=metric_result,
                    reason=reason,
                    side=side_str,
                )
                _record_order_submission_latency(
                    latency_seconds=latency_ms / 1000.0,
                    result=metric_result,
                    side=side_str,
                )
                self._record_failed_submission(
                    submit_id,
                    symbol,
                    side,
                    order_type,
                    order_quantity,
                    price,
                    tif,
                    reduce_only,
                    leverage,
                )
                self._handle_order_failure(exc, symbol, side, order_quantity)
                return OrderSubmissionOutcome(
                    status=(
                        OrderSubmissionOutcomeStatus.REJECTED
                        if is_rejection
                        else OrderSubmissionOutcomeStatus.FAILED
                    ),
                    reason=reason,
                    reason_detail=reason_detail,
                    error=error_str,
                )

        raise RuntimeError("Order submission exited retry loop without producing an outcome")

    def _submit_order_inner(
        self,
        *,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
    ) -> str | None:
        outcome = self._submit_order_inner_result(
            submit_id=submit_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            order_quantity=order_quantity,
            price=price,
            effective_price=effective_price,
            stop_price=stop_price,
            tif=tif,
            reduce_only=reduce_only,
            leverage=leverage,
        )
        if self.integration_mode and outcome.order is not None:
            return cast(str | None, outcome.order)
        if outcome.success:
            return outcome.order_id
        return None

    def _generate_submit_id(self, client_order_id: str | None) -> str:
        """Generate or retrieve client order ID."""
        submit_id = (
            client_order_id
            or f"{self.bot_id}_{int(self._time_provider.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        )
        if self.integration_mode:
            forced_id = os.getenv("INTEGRATION_TEST_ORDER_ID")
            if forced_id:
                submit_id = forced_id
        return submit_id

    def _log_submission_attempt(
        self,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
    ) -> None:
        """Log the order submission attempt to monitoring."""
        self._event_recorder.record_submission_attempt(
            submit_id, symbol, side, order_type, quantity, price
        )

    def _execute_broker_order(
        self,
        submit_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        order_quantity: Decimal,
        price: Decimal | None,
        stop_price: Decimal | None,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
        *,
        use_retry: bool | None = None,
    ) -> Any:
        """Execute the order placement against the broker."""
        retry_enabled = self._enable_retries if use_retry is None else use_retry
        return self._broker_executor.execute_order(
            submit_id=submit_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=order_quantity,
            price=price,
            stop_price=stop_price,
            tif=tif,
            reduce_only=reduce_only,
            leverage=leverage,
            use_retry=retry_enabled,
        )

    def _handle_order_result(
        self,
        order: Any,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
        submit_id: str,
    ) -> str | None:
        """Process the result from the broker, handling rejections and successes."""
        if not (order and order.id):
            # Should have raised exception if failed, but handle None case just in case
            self._record_failed_submission(
                submit_id,
                symbol,
                side,
                order_type,
                quantity,
                price,
                tif,
                reduce_only,
                leverage,
                status=StoreOrderStatus.REJECTED,
            )
            return None

        status_value = getattr(order, "status", None)
        if status_value is not None and hasattr(status_value, "value"):
            status_name = status_value.value
        else:
            status_name = str(status_value or "")
        try:
            store_status = self._normalize_status(status_value)
        except ExecutionStatusCodecError as exc:
            logger.error(
                "Order submission reported unsupported status",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="order_submit",
                stage="status_codec_error",
                client_order_id=submit_id,
                status=str(status_value),
            )
            raise

        # Check for Rejection
        if str(status_name).upper() in {"REJECTED", "CANCELLED", "FAILED"}:
            return cast(
                str | None,
                self._process_rejection(
                    order,
                    status_name,
                    symbol,
                    side,
                    order_type,
                    quantity,
                    price,
                    effective_price,
                    tif,
                    reduce_only,
                    leverage,
                    submit_id,
                    store_status,
                ),
            )

        # Process Success
        self.open_orders.append(order.id)
        display_price = price if price is not None else "market"

        self._log_success(order, symbol, side, quantity, display_price, reduce_only)
        self._record_trade_event(order, symbol, side, quantity, price, effective_price, submit_id)
        self._record_final_submission(
            order,
            submit_id,
            symbol,
            side,
            order_type,
            quantity,
            price,
            tif,
            store_status,
            reduce_only,
            leverage,
        )

        return cast(str | None, order if self.integration_mode else order.id)

    def _process_rejection(
        self,
        order: Any,
        status_name: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
        tif: Any | None,
        reduce_only: bool,
        leverage: int | None,
        submit_id: str,
        store_status: StoreOrderStatus,
    ) -> Any:
        """Handle rejected orders."""
        self._record_final_submission(
            order,
            submit_id,
            symbol,
            side,
            order_type,
            quantity,
            price,
            tif,
            store_status,
            reduce_only,
            leverage,
        )
        if self.integration_mode:
            self._event_recorder.record_integration_rejection(order, symbol, status_name)
            return order

        self._event_recorder.record_broker_rejection(
            order, status_name, symbol, side, quantity, price, effective_price
        )
        raise RuntimeError(f"Order rejected by broker: {status_name}")

    def _log_success(
        self,
        order: Any,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        display_price: Any,
        reduce_only: bool,
    ) -> None:
        """Log successful order placement."""
        self._event_recorder.record_success(
            order, symbol, side, quantity, display_price, reduce_only
        )

    def _record_trade_event(
        self,
        order: Any,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal | None,
        effective_price: Decimal,
        submit_id: str,
    ) -> None:
        """Record trade event to event store and monitoring."""
        self._event_recorder.record_trade_event(
            order, symbol, side, quantity, price, effective_price, submit_id
        )

    def _handle_order_failure(
        self,
        exc: Exception,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
    ) -> None:
        """Handle exceptions during order placement."""
        self._event_recorder.record_failure(exc, symbol, side, quantity)
