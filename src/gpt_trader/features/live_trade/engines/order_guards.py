"""Per-order guard pipeline for the live trading engine.

Extracted from engines/strategy.py: the ordered pre-submission gates that
_validate_and_place_order runs — degradation pause, sizing, reduce-only
request/mode enforcement, security validation, mark staleness, and the
OrderValidator guard chain. Functions take the engine explicitly, mirroring
the cycle_runner and decision_flow seams.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from gpt_trader.core import OrderSide, OrderType, Position
from gpt_trader.features.live_trade.execution.decision_trace import OrderDecisionTrace
from gpt_trader.features.live_trade.execution.submission_result import (
    OrderSubmissionResult,
    OrderSubmissionStatus,
)
from gpt_trader.features.live_trade.risk.manager import ValidationError
from gpt_trader.monitoring.alert_types import AlertSeverity
from gpt_trader.monitoring.profiling import profile_span
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="trading_engine")


def _finalize(
    engine: Any,
    trace: OrderDecisionTrace,
    *,
    status: OrderSubmissionStatus,
    order_id: str | None = None,
    reason: str | None = None,
    error: str | None = None,
) -> OrderSubmissionResult:
    """Typed pass-through to the engine's decision-trace finalizer."""
    result: OrderSubmissionResult = engine._finalize_decision_trace(
        trace, status=status, order_id=order_id, reason=reason, error=error
    )
    return result


async def check_degradation_gate(
    engine: Any,
    *,
    symbol: str,
    side: OrderSide,
    price: Decimal,
    trace: OrderDecisionTrace,
    reduce_only_flag: bool,
) -> OrderSubmissionResult | None:
    if engine._degradation.is_paused(symbol, is_reduce_only=reduce_only_flag):
        pause_reason = engine._degradation.get_pause_reason(symbol) or "unknown"
        logger.warning(
            f"Order blocked: trading paused for {symbol}",
            symbol=symbol,
            side=side.value,
            reason=pause_reason,
            operation="degradation",
            stage="order_blocked",
        )
        engine._emit_trade_gate_blocked(
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
        engine._order_submitter.record_rejection(
            symbol, side.value, Decimal("0"), price, f"paused:{pause_reason}"
        )
        await engine._notify(
            title="Order Blocked - Trading Paused",
            message=f"Cannot place {side.value} order for {symbol}: {pause_reason}",
            severity=AlertSeverity.WARNING,
            context={"symbol": symbol, "side": side.value, "reason": pause_reason},
        )
        trace.record_outcome("degradation_gate", "blocked", detail=pause_reason)
        return _finalize(
            engine,
            trace,
            status=OrderSubmissionStatus.BLOCKED,
            reason=f"paused:{pause_reason}",
        )
    trace.record_outcome("degradation_gate", "passed")
    return None


def calculate_quantity_and_record(
    engine: Any,
    *,
    symbol: str,
    side: OrderSide,
    price: Decimal,
    equity: Decimal,
    quantity_override: Decimal | None,
    trace: OrderDecisionTrace,
) -> tuple[Decimal, OrderSubmissionResult | None]:
    quantity = engine._calculate_order_quantity(
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
        engine._emit_trade_gate_blocked(
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
        engine._order_submitter.record_rejection(
            symbol, side.value, quantity, price, "quantity_zero"
        )
        return quantity, _finalize(
            engine,
            trace,
            status=OrderSubmissionStatus.BLOCKED,
            reason="quantity_zero",
        )
    trace.record_outcome("sizing", "passed")
    return quantity, None


async def check_reduce_only_request(
    engine: Any,
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
        engine._emit_trade_gate_blocked(
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
        engine._order_submitter.record_rejection(
            symbol, side.value, quantity, price, "reduce_only_not_reducing"
        )
        return _finalize(
            engine,
            trace,
            status=OrderSubmissionStatus.BLOCKED,
            reason="reduce_only_not_reducing",
        )
    return None


async def run_security_validation(
    engine: Any,
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
    if hasattr(engine.context.config, "risk"):
        risk = engine.context.config.risk
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
        engine._order_submitter.record_rejection(
            symbol,
            side.value,
            quantity,
            price,
            "security_validation_failed",
        )
        await engine._notify(
            title="Security Validation Failed",
            message=error_msg,
            severity=AlertSeverity.ERROR,
            context=security_order,
        )
        trace.record_outcome("security_validation", "blocked", detail=error_msg)
        return _finalize(
            engine,
            trace,
            status=OrderSubmissionStatus.BLOCKED,
            reason=error_msg,
        )
    trace.record_outcome("security_validation", "passed")
    return None


async def apply_reduce_only_mode(
    engine: Any,
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
    risk_manager = engine.context.risk_manager
    if risk_manager is None:
        error_msg = "risk_manager_unavailable"
        logger.error(
            "Order blocked - risk manager unavailable",
            symbol=symbol,
            side=side.value,
            operation="reduce_only",
            stage="missing_risk_manager",
        )
        engine._emit_trade_gate_blocked(
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
        engine._order_submitter.record_rejection(symbol, side.value, quantity, price, error_msg)
        await engine._notify(
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
        return quantity, _finalize(
            engine,
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
            engine._emit_trade_gate_blocked(
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
            engine._order_submitter.record_rejection(
                symbol, side.value, quantity, price, "reduce_only_empty_position"
            )
            return quantity, _finalize(
                engine,
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
        engine._emit_trade_gate_blocked(
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
        await engine._notify(
            title="Order Blocked - Reduce Only Mode",
            message=f"Cannot open new {side.value} position for {symbol} while in reduce-only mode",
            severity=AlertSeverity.WARNING,
            context=order_for_check,
        )
        trace.record_outcome("reduce_only", "blocked", detail=error_msg)
        engine._order_submitter.record_rejection(
            symbol, side.value, quantity, price, "reduce_only_mode_blocked"
        )
        return quantity, _finalize(
            engine,
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


async def check_mark_staleness(
    engine: Any,
    *,
    symbol: str,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal,
    reduce_only_flag: bool,
    trace: OrderDecisionTrace,
) -> OrderSubmissionResult | None:
    if engine.context.risk_manager is None:
        trace.record_outcome("mark_staleness", "skipped")
        return None

    if engine.context.risk_manager.check_mark_staleness(symbol):
        config = engine.context.risk_manager.config
        if config is not None:
            allow_reduce = config.mark_staleness_allow_reduce_only
            cooldown = config.mark_staleness_cooldown_seconds
            engine._append_event(
                "stale_mark_detected",
                {
                    "symbol": symbol,
                    "side": side.value,
                    "allowed_reduce_only": allow_reduce and reduce_only_flag,
                    "timestamp": time.time(),
                },
            )
            engine._degradation.pause_symbol(
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
            engine._emit_trade_gate_blocked(
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
            engine._order_submitter.record_rejection(
                symbol, side.value, quantity, price, "mark_staleness"
            )
            await engine._notify(
                title="Order Blocked - Stale Mark Price",
                message=f"Cannot place order for {symbol}: mark price data is stale",
                severity=AlertSeverity.WARNING,
                context={"symbol": symbol, "side": side.value},
            )
            trace.record_outcome("mark_staleness", "blocked", detail="stale")
            return _finalize(
                engine,
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason="mark_staleness",
            )

        logger.warning(f"Order blocked: mark price stale for {symbol}")
        engine._emit_trade_gate_blocked(
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
        engine._append_event(
            "stale_mark_detected",
            {
                "symbol": symbol,
                "side": side.value,
                "allowed_reduce_only": False,
                "timestamp": time.time(),
            },
        )
        await engine._notify(
            title="Order Blocked - Stale Mark Price",
            message=f"Cannot place order for {symbol}: mark price data is stale",
            severity=AlertSeverity.WARNING,
            context={"symbol": symbol, "side": side.value},
        )
        trace.record_outcome("mark_staleness", "blocked", detail="stale")
        return _finalize(
            engine,
            trace,
            status=OrderSubmissionStatus.BLOCKED,
            reason="mark_staleness",
        )

    trace.record_outcome("mark_staleness", "passed")
    return None


async def run_order_validator_guards(
    engine: Any,
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
    if engine._order_validator is None:
        trace.record_outcome("order_validation", "skipped")
        return quantity, effective_price, reduce_only_flag, None

    try:
        with profile_span("pre_trade_validation", {"symbol": symbol}) as _val_span:
            product = engine._state_collector.require_product(symbol, product=None)
            effective_price = engine._state_collector.resolve_effective_price(
                symbol, side.value.lower(), price, product
            )

            try:
                quantity, _ = engine._order_validator.validate_exchange_rules(
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

            current_positions_dict = engine._state_collector.build_positions_dict(
                list(engine._current_positions.values())
            )
            try:
                engine._order_validator.run_pre_trade_validation(
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
                engine._order_validator.enforce_slippage_guard(
                    symbol, side, quantity, effective_price
                )
                trace.record_outcome("slippage_guard", "passed")
                engine._degradation.reset_slippage_failures(symbol)
            except ValidationError as slippage_exc:
                trace.record_outcome(
                    "slippage_guard",
                    "blocked",
                    detail=str(slippage_exc),
                )
                config = engine.context.risk_manager.config if engine.context.risk_manager else None
                if config is not None:
                    engine._degradation.record_slippage_failure(symbol, config)
                raise slippage_exc

            # Use container's tracker (validated at init, asserted non-None here)
            assert engine.context.container is not None
            failure_tracker = engine.context.container.validation_failure_tracker
            config = engine.context.risk_manager.config if engine.context.risk_manager else None
            preview_disable_threshold = config.preview_failure_disable_after if config else 5

            if (
                engine._order_validator.enable_order_preview
                and failure_tracker.get_failure_count("order_preview") >= preview_disable_threshold
            ):
                logger.warning(
                    "Auto-disabling order preview due to repeated failures",
                    consecutive_failures=failure_tracker.get_failure_count("order_preview"),
                    threshold=preview_disable_threshold,
                    operation="degradation",
                    stage="preview_disable",
                )
                engine._order_validator.enable_order_preview = False

            if engine._order_validator.enable_order_preview:
                try:
                    await engine._order_validator.maybe_preview_order_async(
                        symbol=symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        order_quantity=quantity,
                        effective_price=effective_price,
                        stop_price=None,
                        tif=engine.context.config.time_in_force,
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

            reduce_only_flag = engine._order_validator.finalize_reduce_only_flag(
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
        engine._emit_trade_gate_blocked(
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
        engine._order_submitter.record_rejection(
            symbol, side.value, quantity, effective_price, reason_code
        )
        await engine._notify(
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
            _finalize(
                engine,
                trace,
                status=OrderSubmissionStatus.BLOCKED,
                reason=str(exc),
            ),
        )
    except Exception as exc:
        logger.error(f"Guard check error: {exc}")
        engine._order_submitter.record_rejection(symbol, side.value, quantity, price, "guard_error")
        await engine._notify(
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
            _finalize(
                engine,
                trace,
                status=OrderSubmissionStatus.FAILED,
                error=str(exc),
            ),
        )

    return quantity, effective_price, reduce_only_flag, None
