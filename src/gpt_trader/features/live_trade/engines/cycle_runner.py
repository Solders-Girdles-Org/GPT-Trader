"""Per-cycle orchestration for the live trading engine."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

from gpt_trader.core import Position
from gpt_trader.logging.correlation import correlation_context
from gpt_trader.monitoring.metrics_collector import record_histogram
from gpt_trader.monitoring.profiling import profile_span
from gpt_trader.monitoring.tracing import trace_span
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="trading_engine")


def _report_system_status(engine: Any) -> None:
    """Collect and report system health metrics.

    Delegates to SystemMaintenanceService for the actual reporting.
    """
    engine._system_maintenance.report_system_status(
        latency_seconds=engine._last_latency,
        connection_status=engine._connection_status,
    )


async def run_cycle(engine: Any) -> None:
    """One trading cycle."""
    assert engine.context.broker is not None, "Broker not initialized"
    engine._cycle_count += 1

    # Wrap entire cycle in correlation context and trace span
    start_time = time.perf_counter()
    result = "ok"
    with correlation_context(cycle=engine._cycle_count):
        with trace_span("cycle", {"cycle": engine._cycle_count}) as span:
            try:
                await _run_cycle_inner(engine)
            except Exception:
                result = "error"
                if span:
                    span.set_attribute("error", True)
                raise
            finally:
                duration = time.perf_counter() - start_time
                if span:
                    span.set_attribute("duration_seconds", duration)
                    span.set_attribute("result", result)
                record_histogram(
                    "gpt_trader_cycle_duration_seconds",
                    duration,
                    labels={"result": result},
                )


async def _run_cycle_inner(engine: Any) -> None:
    """Inner cycle logic wrapped in correlation context."""
    logger.info(f"=== CYCLE {engine._cycle_count} START ===")

    # Report system status at start of cycle
    _report_system_status(engine)
    broker = engine.context.broker
    if broker is None:
        logger.error("Broker not initialized", operation="cycle")
        engine._connection_status = "DISCONNECTED"
        return

    positions, audit_task = await _fetch_positions_and_audit(engine)
    equity = await _compute_equity(engine, positions)
    if equity is None:
        await _await_audit_task(engine, audit_task, context="during equity error path")
        return

    await _await_audit_task(engine, audit_task, context="post equity")
    _update_equity_and_risk(engine, equity)

    # Ensure symbols is a list to avoid iterator exhaustion during multiple iterations
    symbols = list(engine.context.config.symbols)
    tickers = await _fetch_batch_tickers(engine, broker, symbols)

    tasks = [
        engine._process_symbol(
            symbol=symbol,
            broker=broker,
            ticker=tickers.get(symbol),
            positions=positions,
            equity=equity,
        )
        for symbol in symbols
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures: list[Exception] = []

    for symbol, res in zip(symbols, results):
        if isinstance(res, Exception):
            logger.error(
                f"Failed to process symbol {symbol}: {res}",
                exc_info=res,
                symbol=symbol,
            )
            failures.append(res)

    if failures:
        raise ExceptionGroup("Cycle completed with symbol processing errors", failures)


async def _fetch_positions_and_audit(
    engine: Any,
) -> tuple[dict[str, Position], asyncio.Task[None]]:
    logger.info("Step 1: Fetching positions and auditing orders (parallel)...")
    positions_task = asyncio.create_task(engine._fetch_positions())
    # Order audit reconciles broker state and can cancel drifted orders
    # (order_reconciliation escalation). Skip it whenever the engine must not
    # mutate the broker: dry-run, or proposal-only mode where decisions are
    # routed to trade-idea proposals instead of the broker.
    dry_run = getattr(engine.context.config, "dry_run", False)
    # The event-driven paper lane implies proposal routing: decisions become
    # trade ideas (paper-executed in-process), never live broker orders.
    proposal_only = getattr(
        engine.context.config, "strategy_signal_proposals_enabled", False
    ) or getattr(engine.context.config, "event_driven_paper_lane_enabled", False)
    if dry_run or proposal_only:
        logger.info(
            "Skipping order audit (no broker mutation in this mode)",
            operation="order_audit",
            stage="skip",
            reason="dry_run" if dry_run else "proposal_only",
        )
        audit_task = asyncio.create_task(asyncio.sleep(0))
    else:
        audit_task = asyncio.create_task(engine._audit_orders())

    with profile_span("fetch_positions") as _pos_span:
        positions = await positions_task
    engine._current_positions = positions
    logger.info(f"Fetched {len(positions)} positions")

    engine._status_reporter.update_positions(engine._positions_to_status_format(positions))
    return positions, audit_task


async def _await_audit_task(engine: Any, task: asyncio.Task, *, context: str) -> None:
    try:
        await task
    except Exception as e:
        logger.warning(f"Order audit failed {context}: {e}")


async def _compute_equity(engine: Any, positions: dict[str, Position]) -> Decimal | None:
    logger.info("Step 2: Calculating total equity...")
    with profile_span("equity_computation") as _eq_span:
        equity: Decimal | None = await engine._fetch_total_equity(positions)
    if equity is None:
        logger.error(
            "Failed to fetch equity - cannot continue cycle. "
            "Check logs above for balance fetch errors."
        )
        engine._status_reporter.record_error("Failed to fetch equity")
        return None
    return equity


def _update_equity_and_risk(engine: Any, equity: Decimal) -> None:
    logger.info(f"Successfully calculated equity: ${equity}")
    engine._status_reporter.update_equity(equity)
    logger.info("Equity updated in status reporter")

    if engine.context.risk_manager:
        triggered = engine.context.risk_manager.track_daily_pnl(equity, {})
        if triggered:
            logger.warning("Daily loss limit triggered! Reduce-only mode activated.")

        rm = engine.context.risk_manager
        daily_loss_pct = 0.0
        start_equity = getattr(rm, "_start_of_day_equity", 0)
        if start_equity and start_equity > 0:
            daily_pnl = equity - start_equity
            daily_loss_pct = float(-daily_pnl / start_equity)

        engine._status_reporter.update_risk(
            max_leverage=float(getattr(rm.config, "max_leverage", 0.0) if rm.config else 0.0),
            daily_loss_limit=float(
                getattr(rm.config, "daily_loss_limit_pct", 0.0) if rm.config else 0.0
            ),
            current_daily_loss=daily_loss_pct,
            reduce_only=getattr(rm, "_reduce_only_mode", False),
            reduce_reason=getattr(rm, "_reduce_only_reason", ""),
        )


async def _fetch_batch_tickers(
    engine: Any, broker: Any, symbols: list[str]
) -> dict[str, dict[str, Any]]:
    tickers: dict[str, dict[str, Any]] = {}
    batch_start = time.time()

    get_tickers_method = getattr(broker, "get_tickers", None)
    if get_tickers_method is not None and callable(get_tickers_method):
        try:
            result = await engine._broker_calls(get_tickers_method, symbols)
            if isinstance(result, dict):
                tickers = result
                logger.debug(
                    f"Batch ticker fetch: {len(tickers)}/{len(symbols)} symbols "
                    f"in {time.time() - batch_start:.3f}s"
                )
        except Exception as e:
            logger.warning(f"Batch ticker fetch failed, falling back to individual: {e}")

    return tickers
