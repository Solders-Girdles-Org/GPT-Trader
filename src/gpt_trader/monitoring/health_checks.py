"""
Active health check implementations.

Provides concrete health check functions that probe system components
and return structured results for the health server.

Each check returns HealthCheckResult where:
- healthy: True if healthy, False if unhealthy
- details: Details including severity, latency, error messages
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from gpt_trader.monitoring.execution_health_signals import compute_execution_health_signals
from gpt_trader.monitoring.health_check_planning import (
    HealthCheckCycleError,
    HealthCheckDependencyError,
    HealthCheckDescriptor,
    HealthCheckMode,
    HealthCheckOutcome,
    HealthCheckPlanError,
    HealthCheckPlanner,
)
from gpt_trader.monitoring.interfaces import HealthCheckDependency, HealthCheckResult
from gpt_trader.monitoring.metrics_collector import record_counter
from gpt_trader.monitoring.profiling import profile_span
from gpt_trader.utilities.backoff_policy import BackoffDecision, evaluate_backoff_delay
from gpt_trader.utilities.datetime_helpers import age_since_timestamp_seconds
from gpt_trader.utilities.logging_patterns import get_logger
from gpt_trader.utilities.time_provider import TimeProvider, get_clock

if TYPE_CHECKING:
    from gpt_trader.app.health_server import HealthState
    from gpt_trader.core.protocols import BrokerProtocol
    from gpt_trader.features.live_trade.degradation import DegradationState
    from gpt_trader.features.live_trade.risk.protocols import RiskManagerProtocol
    from gpt_trader.utilities.async_tools.bounded_to_thread import BoundedToThread

# Runtime imports: TickerFreshnessProvider/Source are used at runtime (isinstance
# checks), so they cannot move under TYPE_CHECKING.
from gpt_trader.core.protocols import (
    TickerFreshnessProvider,
    TickerFreshnessProviderSource,
)

logger = get_logger(__name__, component="health_checks")

TickerFreshnessService = TickerFreshnessProviderSource | TickerFreshnessProvider

# Metrics emitted by the ticker freshness health check.
TICKER_FRESHNESS_CHECKS_COUNTER = "gpt_trader_ticker_freshness_checks_total"
TICKER_CACHE_UNAVAILABLE_COUNTER = "gpt_trader_ticker_cache_unavailable_total"
TICKER_STALE_SYMBOLS_COUNTER = "gpt_trader_ticker_stale_symbols_total"


HealthCheckCallable = Callable[[], HealthCheckResult | tuple[bool, dict[str, Any]]]


def _coerce_health_check_result(
    result: HealthCheckResult | tuple[bool, dict[str, Any]] | tuple[bool, dict[str, Any] | None],
) -> HealthCheckResult:
    if isinstance(result, HealthCheckResult):
        return result
    healthy, details = result
    return HealthCheckResult(healthy=healthy, details=details or {})


def run_health_check(check_fn: HealthCheckCallable) -> HealthCheckResult:
    """Run a health check callable and normalize the result."""
    try:
        return _coerce_health_check_result(check_fn())
    except Exception as exc:
        return HealthCheckResult(healthy=False, details={"error": str(exc)})


def record_health_check_result(
    health_state: HealthState,
    name: str,
    result: HealthCheckResult | tuple[bool, dict[str, Any]] | tuple[bool, dict[str, Any] | None],
) -> HealthCheckResult:
    """Record a normalized health check result in the shared health state."""
    normalized = _coerce_health_check_result(result)
    health_state.add_check(name, normalized)
    return normalized


def register_health_check(
    health_state: HealthState,
    name: str,
    check_fn: HealthCheckCallable,
) -> HealthCheckResult:
    """Run and record a health check in the shared health state."""
    return record_health_check_result(health_state, name, run_health_check(check_fn))


def check_broker_ping(broker: BrokerProtocol) -> HealthCheckResult:
    """
    Check broker connectivity by making a lightweight API call.

    Uses get_time() if available (cheapest), otherwise falls back to
    list_balances().

    Args:
        broker: Broker protocol instance.

    Returns:
        HealthCheckResult where details includes:
            - latency_ms: Round-trip time in milliseconds
            - error: Exception string if failed
            - method: Which method was used for the check
    """
    start = time.perf_counter()
    details: dict[str, Any] = {"severity": "critical"}

    try:
        # Prefer get_time() as it's the lightest-weight call
        if hasattr(broker, "get_time") and callable(broker.get_time):
            broker.get_time()
            method = "get_time"
        else:
            # Fall back to list_balances
            broker.list_balances()
            method = "list_balances"

        latency_ms = (time.perf_counter() - start) * 1000
        details.update(
            {
                "latency_ms": round(latency_ms, 2),
                "method": method,
            }
        )

        # Warn if latency is high (>2s is concerning for trading)
        if latency_ms > 2000:
            details["severity"] = "warning"
            details["warning"] = "High latency detected"

        return HealthCheckResult(healthy=True, details=details)

    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        details.update(
            {
                "latency_ms": round(latency_ms, 2),
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        )
        logger.warning(
            "Broker ping failed",
            operation="health_check",
            error=str(exc),
            latency_ms=latency_ms,
        )
        return HealthCheckResult(healthy=False, details=details)


def check_ws_freshness(
    broker: BrokerProtocol,
    message_stale_seconds: float = 60.0,
    heartbeat_stale_seconds: float = 120.0,
    time_provider: TimeProvider | None = None,
) -> HealthCheckResult:
    """
    Check WebSocket connection health and message freshness.

    Args:
        broker: Broker protocol instance (must have get_ws_health method).
        message_stale_seconds: Max age of last message before considered stale.
        heartbeat_stale_seconds: Max age of last heartbeat before considered stale.
        time_provider: Optional time provider for deterministic freshness checks.

    Returns:
        HealthCheckResult where details includes:
            - connected: Whether WS is currently connected
            - last_message_age_seconds: Age of last message
            - last_heartbeat_age_seconds: Age of last heartbeat
            - stale: Whether data is considered stale
            - max_attempts_triggered: Whether reconnection limit was hit
    """
    details: dict[str, Any] = {"severity": "warning"}

    # Check if broker supports WS health
    if not hasattr(broker, "get_ws_health"):
        details["error"] = "Broker does not support WebSocket health checks"
        details["ws_not_supported"] = True
        # Not having WS is not necessarily unhealthy - it's optional
        return HealthCheckResult(healthy=True, details=details)

    try:
        health = broker.get_ws_health()

        if not health:
            # WS not initialized - this is OK, streaming is optional
            details["ws_not_initialized"] = True
            return HealthCheckResult(healthy=True, details=details)

        clock = time_provider or get_clock()
        now = clock.time()
        connected = health.get("connected", False)
        last_message_ts = health.get("last_message_ts", 0)
        last_heartbeat_ts = health.get("last_heartbeat_ts", 0)
        max_attempts_triggered = health.get("max_attempts_triggered", False)

        # Calculate ages
        message_ts, message_age = age_since_timestamp_seconds(
            last_message_ts,
            now_seconds=now,
        )
        heartbeat_ts, heartbeat_age = age_since_timestamp_seconds(
            last_heartbeat_ts,
            now_seconds=now,
        )
        message_timestamp_unparseable = message_ts is None and (
            last_message_ts not in (None, "", 0, 0.0, False)
        )
        heartbeat_timestamp_unparseable = heartbeat_ts is None and (
            last_heartbeat_ts not in (None, "", 0, 0.0, False)
        )

        details.update(
            {
                "connected": connected,
                "last_message_age_seconds": (
                    round(message_age, 1) if message_age != float("inf") else None
                ),
                "last_heartbeat_age_seconds": (
                    round(heartbeat_age, 1) if heartbeat_age != float("inf") else None
                ),
                "gap_count": health.get("gap_count", 0),
                "reconnect_count": health.get("reconnect_count", 0),
                "max_attempts_triggered": max_attempts_triggered,
            }
        )

        # Determine health status
        is_stale = False
        if connected:
            if message_timestamp_unparseable:
                is_stale = True
                details["stale_reason"] = "message_timestamp_unparseable"
            elif heartbeat_timestamp_unparseable:
                is_stale = True
                details["stale_reason"] = "heartbeat_timestamp_unparseable"
            elif message_ts is not None and message_age > message_stale_seconds:
                is_stale = True
                details["stale_reason"] = "message"
            elif heartbeat_ts is not None and heartbeat_age > heartbeat_stale_seconds:
                is_stale = True
                details["stale_reason"] = "heartbeat"

        details["stale"] = is_stale

        # Max attempts triggered is critical
        if max_attempts_triggered:
            details["severity"] = "critical"
            return HealthCheckResult(healthy=False, details=details)

        # Disconnected or stale is a failure
        if not connected or is_stale:
            return HealthCheckResult(healthy=False, details=details)

        return HealthCheckResult(healthy=True, details=details)

    except Exception as exc:
        details["error"] = str(exc)
        details["error_type"] = type(exc).__name__
        logger.warning(
            "WS health check failed",
            operation="health_check",
            error=str(exc),
        )
        return HealthCheckResult(healthy=False, details=details)


def _coerce_symbol_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple, set)):
        return [str(symbol) for symbol in raw if symbol]
    return []


def _extract_market_data_symbols(market_data_service: Any) -> list[str]:
    for attribute_name in ("symbols", "_symbols"):
        raw = getattr(market_data_service, attribute_name, None)
        if raw is not None:
            return _coerce_symbol_list(raw)

    getter = getattr(market_data_service, "get_symbols", None)
    if callable(getter):
        try:
            return _coerce_symbol_list(getter())
        except Exception:
            return []

    return []


def _resolve_ticker_freshness_provider(
    market_data_service: Any,
) -> TickerFreshnessProvider | None:
    if market_data_service is None:
        return None

    # If the service itself is a provider, use it.
    if isinstance(market_data_service, TickerFreshnessProvider):
        return market_data_service

    # If the service explicitly exposes a provider, trust that contract.
    # Important: if it returns None, do NOT guess/fallback to internal attrs.
    if isinstance(market_data_service, TickerFreshnessProviderSource):
        try:
            provider = market_data_service.get_ticker_freshness_provider()
        except Exception:
            return None
        if isinstance(provider, TickerFreshnessProvider):
            return provider
        return None

    # Backwards-compatible attribute-based discovery (used by some tests / simple stubs).
    for attribute_name in ("ticker_cache", "_ticker_cache"):
        candidate = getattr(market_data_service, attribute_name, None)
        if candidate is not None and hasattr(candidate, "is_stale"):
            return cast(TickerFreshnessProvider, candidate)

    # Finally, accept any object that has a callable is_stale.
    if hasattr(market_data_service, "is_stale") and callable(
        getattr(market_data_service, "is_stale", None)
    ):
        return cast(TickerFreshnessProvider, market_data_service)

    return None


def _resolve_last_ticker_timestamp(market_data_service: Any) -> tuple[Any | None, dict[str, Any]]:
    details: dict[str, Any] = {}
    if market_data_service is None:
        details["market_data_service_unavailable"] = True
        return None, details

    for method_name in (
        "get_last_ticker_timestamp",
        "get_latest_ticker_timestamp",
        "get_last_update_timestamp",
    ):
        method = getattr(market_data_service, method_name, None)
        if callable(method):
            try:
                return method(), details
            except Exception as exc:
                details.update(
                    {
                        "timestamp_error": str(exc),
                        "timestamp_error_type": type(exc).__name__,
                    }
                )
                return None, details

    for attribute_name in ("ticker_cache", "_ticker_cache"):
        candidate = getattr(market_data_service, attribute_name, None)
        if candidate is None:
            continue
        for method_name in (
            "get_last_ticker_timestamp",
            "get_latest_ticker_timestamp",
            "get_last_update_timestamp",
        ):
            method = getattr(candidate, method_name, None)
            if callable(method):
                try:
                    return method(), details
                except Exception as exc:
                    details.update(
                        {
                            "timestamp_error": str(exc),
                            "timestamp_error_type": type(exc).__name__,
                        }
                    )
                    return None, details

    details["timestamp_unavailable"] = True
    return None, details


def check_ticker_freshness(
    market_data_service: TickerFreshnessService | None,
) -> HealthCheckResult:
    """
    Check market data ticker freshness using a freshness provider.

    Args:
        market_data_service: Market data service or provider that exposes ticker freshness.

    Returns:
        HealthCheckResult where details includes:
            - symbols_checked: Symbols evaluated
            - stale_symbols: Symbols with stale or missing tickers
            - stale_count: Count of stale symbols
            - symbol_count: Total symbol count

    Metrics:
        - gpt_trader_profile_duration_seconds{phase="ticker_freshness"}: duration histogram
          recorded via profile_span.
        - gpt_trader_ticker_freshness_checks_total{result="ok"|"error"}: check outcome.
        - gpt_trader_ticker_cache_unavailable_total: increments when the market data
          provider or ticker cache is unavailable.
        - gpt_trader_ticker_stale_symbols_total: increments by the number of stale
          symbols observed per check.
    """
    details: dict[str, Any] = {"severity": "warning"}

    def record_outcome(healthy: bool, details: dict[str, Any]) -> HealthCheckResult:
        result = "ok" if healthy else "error"
        record_counter(TICKER_FRESHNESS_CHECKS_COUNTER, labels={"result": result})
        return HealthCheckResult(healthy=healthy, details=details)

    with profile_span("ticker_freshness"):
        if market_data_service is None:
            record_counter(TICKER_CACHE_UNAVAILABLE_COUNTER)
            details.update(
                {
                    "skipped": True,
                    "reason": "market_data_service_unavailable",
                }
            )
            return record_outcome(True, details)

        symbols = _extract_market_data_symbols(market_data_service)
        details["symbol_count"] = len(symbols)
        details["symbols_checked"] = list(symbols)

        if not symbols:
            details.update(
                {
                    "stale_symbols": [],
                    "stale_count": 0,
                    "stale": False,
                }
            )
            return record_outcome(True, details)

        ticker_freshness_provider = _resolve_ticker_freshness_provider(market_data_service)
        if ticker_freshness_provider is None:
            record_counter(TICKER_CACHE_UNAVAILABLE_COUNTER)
            details.update(
                {
                    "ticker_cache_unavailable": True,
                    "ticker_freshness_provider_unavailable": True,
                    "skipped": True,
                    "reason": "ticker_freshness_provider_unavailable",
                }
            )
            # No provider means we cannot evaluate staleness. Treat this as skipped/healthy so /health doesn't permanently fail.
            return record_outcome(True, details)

        stale_symbols: list[str] = []
        fresh_symbols: list[str] = []
        try:
            for symbol in symbols:
                if ticker_freshness_provider.is_stale(symbol):
                    stale_symbols.append(symbol)
                else:
                    fresh_symbols.append(symbol)
        except Exception as exc:
            details.update(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "severity": "critical",
                }
            )
            logger.warning(
                "Ticker freshness check failed",
                operation="health_check",
                error=str(exc),
            )
            return record_outcome(False, details)

        details.update(
            {
                "fresh_symbols": fresh_symbols,
                "stale_symbols": stale_symbols,
                "stale_count": len(stale_symbols),
                "stale": bool(stale_symbols),
            }
        )

        if stale_symbols:
            record_counter(TICKER_STALE_SYMBOLS_COUNTER, increment=len(stale_symbols))
            if len(stale_symbols) == len(symbols):
                details["severity"] = "critical"
            return record_outcome(False, details)

        return record_outcome(True, details)


def check_market_data_feed_staleness(
    market_data_service: TickerFreshnessService | None,
    thresholds: HealthThresholds | None = None,
    time_provider: TimeProvider | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Check market data feed staleness using last ticker update timestamp."""
    from gpt_trader.monitoring.health_signals import HealthStatus, HealthThresholds

    if thresholds is None:
        thresholds = HealthThresholds()

    signal = check_market_data_staleness_signal(
        market_data_service,
        thresholds=thresholds,
        time_provider=time_provider,
    )

    details: dict[str, Any] = {
        "age_seconds": signal.value,
        "threshold_warn_seconds": signal.threshold_warn,
        "threshold_crit_seconds": signal.threshold_crit,
        "status": signal.status.value,
        **signal.details,
    }

    if signal.status == HealthStatus.UNKNOWN:
        details["skipped"] = True
        details.setdefault("reason", "market_data_timestamp_unavailable")
        return True, details

    if signal.status == HealthStatus.OK:
        details["severity"] = "ok"
        return True, details

    details["severity"] = "critical" if signal.status == HealthStatus.CRIT else "warning"
    return False, details


def check_degradation_state(
    degradation_state: DegradationState,
    risk_manager: RiskManagerProtocol | None = None,
) -> HealthCheckResult:
    """
    Check trading degradation state.

    Args:
        degradation_state: DegradationState instance.
        risk_manager: Optional LiveRiskManager to check reduce-only mode.

    Returns:
        HealthCheckResult where details includes:
            - global_paused: Whether all trading is paused
            - global_pause_reason: Reason for global pause
            - paused_symbol_count: Number of symbols with individual pauses
            - reduce_only_mode: Whether reduce-only is active
            - reduce_only_reason: Reason for reduce-only mode
    """
    details: dict[str, Any] = {"severity": "warning"}

    try:
        status = degradation_state.get_status()

        global_paused = status.get("global_paused", False)
        global_reason = status.get("global_reason")
        paused_symbols = status.get("paused_symbols", {})
        if not isinstance(paused_symbols, dict):
            paused_symbols = {}

        details.update(
            {
                "global_paused": global_paused,
                "global_pause_reason": global_reason,
                "global_remaining_seconds": status.get("global_remaining_seconds", 0),
                "paused_symbol_count": len(paused_symbols),
            }
        )

        if paused_symbols:
            details["paused_symbols"] = list(paused_symbols.keys())

        # Check reduce-only mode from risk manager
        reduce_only = False
        reduce_only_reason = None

        if risk_manager is not None:
            if hasattr(risk_manager, "is_reduce_only_mode"):
                reduce_only = risk_manager.is_reduce_only_mode()
            elif hasattr(risk_manager, "_reduce_only_mode"):
                reduce_only = risk_manager._reduce_only_mode

            if reduce_only and hasattr(risk_manager, "_reduce_only_reason"):
                reduce_only_reason = risk_manager._reduce_only_reason

            # Also check CFM reduce-only mode
            cfm_reduce_only = False
            if hasattr(risk_manager, "is_cfm_reduce_only_mode"):
                cfm_reduce_only = risk_manager.is_cfm_reduce_only_mode()
            elif hasattr(risk_manager, "_cfm_reduce_only_mode"):
                cfm_reduce_only = risk_manager._cfm_reduce_only_mode

            if cfm_reduce_only:
                reduce_only = True
                if hasattr(risk_manager, "_cfm_reduce_only_reason"):
                    reduce_only_reason = risk_manager._cfm_reduce_only_reason

        details["reduce_only_mode"] = reduce_only
        if reduce_only_reason:
            details["reduce_only_reason"] = reduce_only_reason

        # Global pause is critical
        if global_paused:
            details["severity"] = "critical"
            return HealthCheckResult(healthy=False, details=details)

        # Reduce-only or symbol pauses are warnings but not failures
        if reduce_only or paused_symbols:
            details["severity"] = "warning"
            # Return healthy but with warning details
            return HealthCheckResult(healthy=True, details=details)

        return HealthCheckResult(healthy=True, details=details)

    except Exception as exc:
        details["error"] = str(exc)
        details["error_type"] = type(exc).__name__
        details["severity"] = "critical"
        logger.warning(
            "Degradation state check failed",
            operation="health_check",
            error=str(exc),
        )
        return HealthCheckResult(healthy=False, details=details)


class HealthCheckRunner:
    """
    Executes health checks on a configurable cadence.

    Runs checks in a background task and updates the HealthState
    with results for the health server to expose.
    """

    def __init__(
        self,
        health_state: HealthState,
        broker: BrokerProtocol | None = None,
        degradation_state: DegradationState | None = None,
        risk_manager: RiskManagerProtocol | None = None,
        market_data_service: TickerFreshnessService | None = None,
        interval_seconds: float = 30.0,
        message_stale_seconds: float = 60.0,
        heartbeat_stale_seconds: float = 120.0,
        broker_calls: BoundedToThread | None = None,
        time_provider: TimeProvider | None = None,
        signal_thresholds: HealthThresholds | None = None,
    ) -> None:
        """
        Initialize the health check runner.

        Args:
            health_state: HealthState instance to update with results.
            broker: Broker for connectivity checks.
            degradation_state: DegradationState for pause/reduce-only checks.
            risk_manager: RiskManager for reduce-only mode checks.
            market_data_service: Market data service or provider for ticker freshness checks.
            interval_seconds: How often to run checks (default 30s).
            message_stale_seconds: WS message staleness threshold.
            heartbeat_stale_seconds: WS heartbeat staleness threshold.
            time_provider: Optional time provider for deterministic staleness checks.
            signal_thresholds: Optional thresholds for health signals.
        """
        self._health_state = health_state
        self._broker = broker
        self._degradation_state = degradation_state
        self._risk_manager = risk_manager
        self._market_data_service = market_data_service
        self._interval = interval_seconds
        self._message_stale_seconds = message_stale_seconds
        self._heartbeat_stale_seconds = heartbeat_stale_seconds
        self._broker_calls = broker_calls
        self._time_provider = time_provider
        self._signal_thresholds = signal_thresholds
        self._running = False
        self._task: Any = None

    def set_broker(self, broker: BrokerProtocol) -> None:
        """Set the broker for connectivity checks."""
        self._broker = broker

    def set_degradation_state(self, state: DegradationState) -> None:
        """Set the degradation state for pause checks."""
        self._degradation_state = state

    def set_risk_manager(self, manager: RiskManagerProtocol) -> None:
        """Set the risk manager for reduce-only checks."""
        self._risk_manager = manager

    def set_market_data_service(self, market_data_service: TickerFreshnessService) -> None:
        """Set the market data service or provider for ticker freshness checks."""
        self._market_data_service = market_data_service

    def _health_check_registry(self) -> tuple[HealthCheckDescriptor, ...]:
        """Build the registry of health checks to execute."""
        checks: list[HealthCheckDescriptor] = []

        broker = self._broker
        if broker is not None:
            checks.append(
                HealthCheckDescriptor(
                    name="broker",
                    mode="blocking",
                    run=partial(check_broker_ping, broker),
                )
            )
            checks.append(
                HealthCheckDescriptor(
                    name="websocket",
                    mode="blocking",
                    run=partial(
                        check_ws_freshness,
                        broker,
                        message_stale_seconds=self._message_stale_seconds,
                        heartbeat_stale_seconds=self._heartbeat_stale_seconds,
                        time_provider=self._time_provider,
                    ),
                    dependencies=(HealthCheckDependency(name="broker"),),
                )
            )

        market_data_service = self._market_data_service
        if market_data_service is not None:
            checks.append(
                HealthCheckDescriptor(
                    name="ticker_freshness",
                    mode="fast",
                    run=partial(check_ticker_freshness, market_data_service),
                )
            )
            checks.append(
                HealthCheckDescriptor(
                    name="market_data_feed",
                    mode="fast",
                    run=cast(
                        Callable[[], HealthCheckOutcome],
                        partial(
                            check_market_data_feed_staleness,
                            market_data_service,
                            thresholds=self._signal_thresholds,
                            time_provider=self._time_provider,
                        ),
                    ),
                    dependencies=(HealthCheckDependency(name="ticker_freshness"),),
                )
            )

        degradation_state = self._degradation_state
        if degradation_state is not None:
            checks.append(
                HealthCheckDescriptor(
                    name="degradation",
                    mode="fast",
                    run=partial(check_degradation_state, degradation_state, self._risk_manager),
                )
            )

        return tuple(checks)

    def _planned_checks(self) -> tuple[HealthCheckDescriptor, ...]:
        """Resolve deterministic execution order for health checks."""
        registry = self._health_check_registry()
        planner = HealthCheckPlanner(registry)
        try:
            return planner.build_order()
        except HealthCheckPlanError as exc:
            logger.warning(
                "Health check planning failed; using fallback order",
                operation="health_check_planner",
                error=str(exc),
            )
            return tuple(sorted(registry, key=lambda check: check.name))

    async def start(self) -> None:
        """Start the health check runner background task."""
        import asyncio

        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Health check runner started",
            operation="health_check_runner",
            interval_seconds=self._interval,
        )

    async def stop(self) -> None:
        """Stop the health check runner."""
        import asyncio

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # Expected when cancelling
            except Exception:
                pass
            self._task = None
        logger.info("Health check runner stopped", operation="health_check_runner")

    async def _run_loop(self) -> None:
        """Main loop that executes checks periodically."""
        import asyncio

        while self._running:
            try:
                await self._execute_checks()
            except Exception as exc:
                logger.warning(
                    "Health check execution failed",
                    operation="health_check_runner",
                    error=str(exc),
                )

            await asyncio.sleep(self._interval)

    async def _execute_checks(self) -> None:
        """Execute all configured health checks."""
        import asyncio

        health_state = self._health_state
        broker_calls = self._broker_calls
        if broker_calls is not None and not asyncio.iscoroutinefunction(
            getattr(broker_calls, "__call__", None)
        ):
            broker_calls = None

        async def run_blocking(
            callable_obj: HealthCheckCallable,
        ) -> HealthCheckResult:
            if broker_calls is None:
                return await asyncio.to_thread(run_health_check, callable_obj)
            return await broker_calls(partial(run_health_check, callable_obj))

        for check in self._planned_checks():
            try:
                if check.mode == "blocking":
                    result = await run_blocking(check.run)
                else:
                    result = run_health_check(check.run)
            except Exception as exc:
                result = HealthCheckResult(healthy=False, details={"error": str(exc)})
            record_health_check_result(health_state, check.name, result)

    def run_checks_sync(self) -> dict[str, HealthCheckResult]:
        """
        Run all checks synchronously and return results.

        Useful for testing or one-off health checks.

        Returns:
            Dict mapping check name to HealthCheckResult.
        """
        results: dict[str, HealthCheckResult] = {}

        for check in self._planned_checks():
            results[check.name] = run_health_check(check.run)

        return results


def _evaluate_monitoring_timeout_decision(
    age_seconds: float,
    warn_threshold: float,
    crit_threshold: float,
) -> tuple[BackoffDecision, int]:
    """Determine timeout decision from elapsed age and warn/crit thresholds."""

    warn_delay = max(0.0, warn_threshold)
    crit_delay = max(warn_delay, crit_threshold)

    multiplier = crit_delay / warn_delay if warn_delay > 0 else 1.0
    multiplier = max(multiplier, 1.0)

    if age_seconds < warn_delay:
        attempt = 1
    elif age_seconds < crit_delay:
        attempt = 2
    else:
        attempt = 3

    if warn_delay == 0.0 and crit_delay > 0.0:
        decision = (
            BackoffDecision(attempt=attempt, delay_seconds=0.0, capped=False)
            if attempt < 3
            else BackoffDecision(attempt=attempt, delay_seconds=crit_delay, capped=True)
        )
    else:
        decision = evaluate_backoff_delay(
            attempt=attempt,
            base_delay=warn_delay,
            max_delay=crit_delay,
            multiplier=multiplier,
            jitter=0.0,
        )

    return decision, attempt


def check_market_data_staleness_signal(
    market_data_service: TickerFreshnessService | None,
    thresholds: HealthThresholds | None = None,
    time_provider: TimeProvider | None = None,
) -> HealthSignal:
    """Check market data feed staleness as a health signal."""
    from gpt_trader.monitoring.health_signals import (
        HealthSignal,
        HealthStatus,
        HealthThresholds,
    )

    if thresholds is None:
        thresholds = HealthThresholds()

    timestamp, details = _resolve_last_ticker_timestamp(market_data_service)
    if timestamp is None:
        details.setdefault("timestamp_unavailable", True)
        return HealthSignal(
            name="market_data_staleness",
            status=HealthStatus.UNKNOWN,
            value=0.0,
            threshold_warn=thresholds.market_data_staleness_seconds_warn,
            threshold_crit=thresholds.market_data_staleness_seconds_crit,
            unit="seconds",
            details=details,
        )

    clock = time_provider or get_clock()
    now = clock.time()
    last_ts, staleness = age_since_timestamp_seconds(
        timestamp,
        now_seconds=now,
    )

    if staleness == float("inf"):
        staleness = 9999.0

    decision, attempt = _evaluate_monitoring_timeout_decision(
        staleness,
        warn_threshold=thresholds.market_data_staleness_seconds_warn,
        crit_threshold=thresholds.market_data_staleness_seconds_crit,
    )

    status = (
        HealthStatus.OK
        if attempt == 1
        else HealthStatus.WARN if attempt == 2 else HealthStatus.CRIT
    )

    return HealthSignal(
        name="market_data_staleness",
        status=status,
        value=staleness,
        threshold_warn=thresholds.market_data_staleness_seconds_warn,
        threshold_crit=thresholds.market_data_staleness_seconds_crit,
        unit="seconds",
        details={
            **details,
            "last_update_ts": last_ts if last_ts is not None else 0.0,
            "timeout_attempt": attempt,
            "timeout_delay_seconds": decision.delay_seconds,
            "timeout_capped": decision.capped,
        },
    )


def check_ws_staleness_signal(
    broker: BrokerProtocol,
    thresholds: HealthThresholds | None = None,
    time_provider: TimeProvider | None = None,
) -> HealthSignal:
    """Check WebSocket staleness as a health signal.

    Args:
        broker: Broker protocol instance.
        thresholds: Optional custom thresholds.
        time_provider: Optional time provider for deterministic staleness checks.

    Returns:
        HealthSignal for WebSocket staleness.
    """
    from gpt_trader.monitoring.health_signals import (
        HealthSignal,
        HealthStatus,
        HealthThresholds,
    )

    if thresholds is None:
        thresholds = HealthThresholds()

    # Check if broker supports WS health
    if not hasattr(broker, "get_ws_health"):
        return HealthSignal(
            name="ws_staleness",
            status=HealthStatus.UNKNOWN,
            value=0.0,
            threshold_warn=thresholds.ws_staleness_seconds_warn,
            threshold_crit=thresholds.ws_staleness_seconds_crit,
            unit="seconds",
            details={"ws_not_supported": True},
        )

    try:
        health = broker.get_ws_health()
        if not health:
            return HealthSignal(
                name="ws_staleness",
                status=HealthStatus.OK,
                value=0.0,
                threshold_warn=thresholds.ws_staleness_seconds_warn,
                threshold_crit=thresholds.ws_staleness_seconds_crit,
                unit="seconds",
                details={"ws_not_initialized": True},
            )

        clock = time_provider or get_clock()
        now = clock.time()
        raw_last_message_ts = health.get("last_message_ts", 0)
        message_ts, staleness = age_since_timestamp_seconds(
            raw_last_message_ts,
            now_seconds=now,
        )

        # Cap staleness at a reasonable max for display
        if staleness == float("inf"):
            staleness = 9999.0

        return HealthSignal.from_value(
            name="ws_staleness",
            value=staleness,
            threshold_warn=thresholds.ws_staleness_seconds_warn,
            threshold_crit=thresholds.ws_staleness_seconds_crit,
            unit="seconds",
            details={
                "connected": health.get("connected", False),
                "last_message_ts": message_ts if message_ts is not None else 0.0,
            },
        )

    except Exception as exc:
        return HealthSignal(
            name="ws_staleness",
            status=HealthStatus.UNKNOWN,
            value=0.0,
            threshold_warn=thresholds.ws_staleness_seconds_warn,
            threshold_crit=thresholds.ws_staleness_seconds_crit,
            unit="seconds",
            details={"error": str(exc)},
        )


# Import for type hints
if TYPE_CHECKING:
    from gpt_trader.monitoring.health_signals import (
        HealthSignal,
        HealthThresholds,
    )


__all__ = [
    "HealthCheckCycleError",
    "HealthCheckDependencyError",
    "HealthCheckDescriptor",
    "HealthCheckMode",
    "HealthCheckPlanError",
    "HealthCheckPlanner",
    "HealthCheckResult",
    "HealthCheckRunner",
    "TICKER_FRESHNESS_CHECKS_COUNTER",
    "TICKER_CACHE_UNAVAILABLE_COUNTER",
    "TICKER_STALE_SYMBOLS_COUNTER",
    "register_health_check",
    "check_broker_ping",
    "check_ticker_freshness",
    "check_market_data_feed_staleness",
    "check_ws_freshness",
    "check_degradation_state",
    "compute_execution_health_signals",
    "check_market_data_staleness_signal",
    "check_ws_staleness_signal",
]
