"""Decision-flow seam for the live trading engine.

Extracted from engines/strategy.py: per-symbol decision production
(ticker + candles -> strategy.decide) and decision routing — proposal-only
mode, BUY/SELL order placement, and CLOSE reduce-only exits. Functions take
the engine explicitly, mirroring the cycle_runner seam.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from gpt_trader.core import Position
from gpt_trader.features.live_trade.strategies.perps_baseline import Action, Decision
from gpt_trader.features.strategy_tools import StrategySignalContext
from gpt_trader.features.trade_ideas import ProductType
from gpt_trader.monitoring.alert_types import AlertSeverity
from gpt_trader.monitoring.profiling import profile_span
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="trading_engine")


async def process_symbol(
    engine: Any,
    *,
    symbol: str,
    broker: Any,
    ticker: dict[str, Any] | None,
    positions: dict[str, Position],
    equity: Decimal,
) -> None:
    candles: list[Any] = []
    start_time = time.time()

    if ticker is None:
        try:
            ticker = await engine._broker_calls(broker.get_ticker, symbol)
        except Exception as e:
            logger.error(f"Failed to fetch ticker for {symbol}: {e}")
            engine._connection_status = "DISCONNECTED"
            return

    if ticker is None or not ticker.get("price"):
        logger.error(f"No ticker data for {symbol}")
        engine._connection_status = "DISCONNECTED"
        return

    try:
        candles_result = await engine._broker_calls(
            broker.get_candles,
            symbol,
            granularity="ONE_MINUTE",
        )
        if isinstance(candles_result, Exception):
            logger.warning(f"Failed to fetch candles for {symbol}: {candles_result}")
        else:
            candles = candles_result or []
    except Exception as e:
        logger.warning(f"Failed to fetch candles for {symbol}: {e}")

    engine._last_latency = time.time() - start_time
    engine._connection_status = "CONNECTED"

    price = Decimal(str(ticker.get("price", 0)))
    logger.info(f"{symbol} price: {price}")

    if engine.context.risk_manager is not None:
        engine.context.risk_manager.last_mark_update[symbol] = time.time()

    engine._status_reporter.update_price(symbol, price)
    await engine._price_tick_store.record_price_tick_async(symbol, price)

    position_state = engine._build_position_state(symbol, positions)
    with profile_span("strategy_decision", {"symbol": symbol}) as _strat_span:
        decision = engine.strategy.decide(
            symbol=symbol,
            current_mark=price,
            position_state=position_state,
            recent_marks=engine.price_history[symbol],
            equity=equity,
            product=None,
            candles=candles,
        )

    logger.info(f"Strategy Decision for {symbol}: {decision.action} ({decision.reason})")

    active_strats = getattr(
        engine.strategy, "active_strategies", [engine.strategy.__class__.__name__]
    )
    decision_record = {
        "symbol": symbol,
        "action": decision.action.value,
        "reason": decision.reason,
        "confidence": str(decision.confidence),
        "timestamp": time.time(),
    }
    engine._status_reporter.update_strategy(active_strats, [decision_record])

    # Route through the engine method (a thin delegate to handle_decision) so
    # overrides/monkeypatches of the class seam keep gating live decisions.
    await engine._handle_decision(
        symbol=symbol,
        decision=decision,
        price=price,
        equity=equity,
        position_state=position_state,
    )


async def handle_decision(
    engine: Any,
    *,
    symbol: str,
    decision: Decision,
    price: Decimal,
    equity: Decimal,
    position_state: dict[str, Any] | None,
) -> None:
    if engine._strategy_proposal_adapter is not None:
        # Proposal-only mode: map the decision into a human-review trade idea
        # and return before any broker interaction. This is the sole action
        # taken while the gate is on — no orders are submitted for any action.
        _propose_strategy_decision(
            engine,
            symbol=symbol,
            decision=decision,
            price=price,
            position_state=position_state,
        )
        return

    if decision.action in (Action.BUY, Action.SELL):
        logger.info(
            "Executing order",
            symbol=symbol,
            action=decision.action.value,
            operation="order_placement",
            stage="start",
        )
        try:
            with profile_span(
                "order_placement", {"symbol": symbol, "action": decision.action.value}
            ):
                result = await engine._validate_and_place_order(
                    symbol=symbol,
                    decision=decision,
                    price=price,
                    equity=equity,
                )
            if result.blocked:
                logger.warning(
                    "Order blocked",
                    symbol=symbol,
                    action=decision.action.value,
                    reason=result.reason,
                    operation="order_placement",
                    stage="blocked",
                )
            elif result.failed:
                logger.error(
                    "Order submission failed",
                    symbol=symbol,
                    action=decision.action.value,
                    reason=result.reason,
                    error_message=result.error,
                    operation="order_placement",
                    stage="failed",
                )
                failure_detail = result.error or result.reason or "unknown"
                await engine._notify(
                    title="Order Submission Failed",
                    message=(
                        f"Failed to submit {decision.action.value} order for {symbol}: "
                        f"{failure_detail}"
                    ),
                    severity=AlertSeverity.ERROR,
                    context={
                        "symbol": symbol,
                        "action": decision.action.value,
                        "reason": result.reason,
                        "error": result.error,
                    },
                )
        except Exception as e:
            logger.error(
                "Order placement failed",
                symbol=symbol,
                action=decision.action.value,
                error_message=str(e),
                operation="order_placement",
                stage="failed",
            )
            await engine._notify(
                title="Order Placement Failed",
                message=f"Failed to execute {decision.action} for {symbol}: {e}",
                severity=AlertSeverity.ERROR,
                context={
                    "symbol": symbol,
                    "action": decision.action.value,
                    "error": str(e),
                },
            )
    elif decision.action == Action.CLOSE:
        if position_state is None:
            logger.info(
                "CLOSE signal ignored - no open position",
                symbol=symbol,
                action=decision.action.value,
                operation="order_placement",
                stage="skip",
            )
            return

        close_order = engine._resolve_close_order(position_state)
        if close_order is None:
            logger.warning(
                "CLOSE signal ignored - invalid position state",
                symbol=symbol,
                action=decision.action.value,
                position_state=position_state,
                operation="order_placement",
                stage="invalid_position_state",
            )
            return

        close_side, close_quantity = close_order
        logger.info(
            "Executing close order",
            symbol=symbol,
            action=decision.action.value,
            side=close_side.value,
            quantity=str(close_quantity),
            operation="order_placement",
            stage="start",
        )
        try:
            with profile_span(
                "order_placement",
                {"symbol": symbol, "action": decision.action.value, "side": close_side.value},
            ):
                result = await engine.submit_order(
                    symbol=symbol,
                    side=close_side,
                    price=price,
                    equity=equity,
                    quantity_override=close_quantity,
                    reduce_only=True,
                    reason=decision.reason,
                    confidence=decision.confidence,
                )
            if result.blocked:
                logger.warning(
                    "Close order blocked",
                    symbol=symbol,
                    action=decision.action.value,
                    side=close_side.value,
                    reason=result.reason,
                    operation="order_placement",
                    stage="blocked",
                )
            elif result.failed:
                logger.error(
                    "Close order submission failed",
                    symbol=symbol,
                    action=decision.action.value,
                    side=close_side.value,
                    reason=result.reason,
                    error_message=result.error,
                    operation="order_placement",
                    stage="failed",
                )
                failure_detail = result.error or result.reason or "unknown"
                await engine._notify(
                    title="Close Order Submission Failed",
                    message=f"Failed to close {symbol}: {failure_detail}",
                    severity=AlertSeverity.ERROR,
                    context={
                        "symbol": symbol,
                        "action": decision.action.value,
                        "side": close_side.value,
                        "reason": result.reason,
                        "error": result.error,
                    },
                )
        except Exception as e:
            logger.error(
                "Close order placement failed",
                symbol=symbol,
                action=decision.action.value,
                side=close_side.value,
                error_message=str(e),
                operation="order_placement",
                stage="failed",
            )
            await engine._notify(
                title="Close Order Placement Failed",
                message=f"Failed to close {symbol}: {e}",
                severity=AlertSeverity.ERROR,
                context={
                    "symbol": symbol,
                    "action": decision.action.value,
                    "side": close_side.value,
                    "error": str(e),
                },
            )


def _propose_strategy_decision(
    engine: Any,
    *,
    symbol: str,
    decision: Decision,
    price: Decimal,
    position_state: dict[str, Any] | None,
) -> None:
    """Route a live decision into the approval-gated trade-idea workflow.

    Proposal-only: this creates an auditable ``proposed`` trade idea through
    ``TradeIdeaService.propose()`` and never calls the broker, approves an
    idea, or submits an order. Only supported buy shapes map to an idea;
    other actions (sell/close/hold) are recorded as skipped. Any mapping or
    persistence failure is logged and swallowed so a broken proposal never
    falls through to direct execution while the gate is on.
    """
    assert engine._strategy_proposal_adapter is not None
    assert engine._trade_idea_service is not None
    product_type = _proposal_product_type(engine, symbol, position_state)
    if product_type is not ProductType.SPOT:
        logger.info(
            "Proposal-only mode: product type is not supported for trade ideas",
            symbol=symbol,
            action=decision.action.value,
            product_type=product_type.value,
            operation="strategy_proposal",
            stage="skipped",
        )
        return
    context = StrategySignalContext(
        symbol=symbol,
        current_mark=price,
        as_of=datetime.now(UTC),
        strategy_name=_proposal_strategy_name(engine),
        product_type=product_type,
        data_source="live-strategy:decision",
    )
    try:
        view = engine._strategy_proposal_adapter.propose_decision(
            decision, context, engine._trade_idea_service
        )
    except Exception as exc:
        logger.error(
            "Strategy-signal proposal failed",
            symbol=symbol,
            action=decision.action.value,
            error_type=type(exc).__name__,
            error_message=str(exc),
            operation="strategy_proposal",
            stage="failed",
        )
        return

    if view is None:
        logger.info(
            "Proposal-only mode: decision not eligible for a trade idea",
            symbol=symbol,
            action=decision.action.value,
            operation="strategy_proposal",
            stage="skipped",
        )
        return

    logger.info(
        "Strategy decision proposed for human review",
        symbol=symbol,
        action=decision.action.value,
        decision_id=view.idea.decision_id,
        state=view.state.value,
        operation="strategy_proposal",
        stage="proposed",
    )


def _proposal_strategy_name(engine: Any) -> str:
    """Best-effort human-readable strategy name recorded on proposed ideas."""
    active = getattr(engine.strategy, "active_strategies", None)
    if isinstance(active, (list, tuple)):
        if active:
            return str(active[0])
    elif active:
        return str(active)
    configured = getattr(engine.context.config, "strategy_type", None)
    if configured:
        return str(configured)
    return str(engine.strategy.__class__.__name__)


def _proposal_product_type(
    engine: Any,
    symbol: str,
    position_state: dict[str, Any] | None,
) -> ProductType:
    """Infer the broker-neutral product type before proposing a trade idea.

    The current adapter only supports spot ideas. Returning ``FUTURES`` lets
    proposal-only mode fail closed for CFM/futures contexts instead of
    recording a futures signal as a spot idea.
    """
    raw_position_type = None
    if position_state:
        raw_position_type = position_state.get("product_type")
    if raw_position_type is not None:
        normalized = str(getattr(raw_position_type, "value", raw_position_type)).strip().lower()
        if normalized == ProductType.SPOT.value:
            return ProductType.SPOT
        if normalized in {"future", "futures", "perpetual", "perp"}:
            return ProductType.FUTURES
        try:
            return ProductType(normalized)
        except ValueError:
            return ProductType.OTHER

    config = engine.context.config
    cfm_symbols = {
        str(cfm_symbol).strip().upper()
        for cfm_symbol in getattr(config, "cfm_symbols", [])
        if str(cfm_symbol).strip()
    }
    if symbol.strip().upper() in cfm_symbols:
        return ProductType.FUTURES

    trading_modes = {
        str(mode).strip().lower()
        for mode in getattr(config, "trading_modes", [])
        if str(mode).strip()
    }
    if "cfm" in trading_modes and "spot" not in trading_modes:
        return ProductType.FUTURES
    if symbol.strip().upper().endswith("-FUTURES"):
        return ProductType.FUTURES
    return ProductType.SPOT
