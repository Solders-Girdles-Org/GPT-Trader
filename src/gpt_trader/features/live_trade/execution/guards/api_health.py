"""
API Health Guard - monitors broker API resilience and triggers protective actions.

This guard checks circuit breaker states, error rates, and rate limit usage
to detect API degradation before it impacts trading.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gpt_trader.features.live_trade.execution.guards.protocol import RuntimeGuardState
from gpt_trader.features.live_trade.guard_errors import (
    RiskGuardDataUnavailable,
    RiskLimitExceeded,
)
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.core.protocols import BrokerProtocol
    from gpt_trader.features.live_trade.risk.protocols import RiskManagerProtocol

logger = get_logger(__name__, component="api_health_guard")

# Default thresholds
DEFAULT_API_ERROR_RATE_THRESHOLD = 0.2  # 20% error rate
DEFAULT_API_RATE_LIMIT_USAGE_THRESHOLD = 0.9  # 90% rate limit usage


class ApiHealthGuard:
    """
    Guard that monitors API health and triggers reduce-only mode on degradation.

    Checks:
    - Circuit breaker states (any open breaker triggers)
    - Error rate threshold
    - Rate limit usage threshold
    """

    def __init__(
        self,
        broker: BrokerProtocol,
        risk_manager: RiskManagerProtocol,
    ) -> None:
        """
        Initialize API health guard.

        Args:
            broker: Broker service (accesses client for resilience status)
            risk_manager: Risk manager for config and reduce-only triggering
        """
        self._broker = broker
        self._risk_manager = risk_manager

        # Get client handle - try broker.client, fall back to broker itself
        self._client: Any = getattr(broker, "client", None) or getattr(broker, "_client", None)
        if self._client is None and hasattr(broker, "get_resilience_status"):
            self._client = broker

    @property
    def name(self) -> str:
        return "api_health"

    def _get_thresholds(self) -> tuple[float, float]:
        """Get configured thresholds from risk manager config or use defaults."""
        config = getattr(self._risk_manager, "config", None)

        error_rate_threshold = getattr(
            config, "api_error_rate_threshold", DEFAULT_API_ERROR_RATE_THRESHOLD
        )
        rate_limit_threshold = getattr(
            config, "api_rate_limit_usage_threshold", DEFAULT_API_RATE_LIMIT_USAGE_THRESHOLD
        )

        return float(error_rate_threshold), float(rate_limit_threshold)

    def check(self, state: RuntimeGuardState, incremental: bool = False) -> None:
        """
        Check API health and raise if degraded.

        Args:
            state: Current account state snapshot (not used directly, but part of protocol)
            incremental: Whether this is an incremental check

        Raises:
            RiskLimitExceeded: If API health thresholds are breached
            RiskGuardDataUnavailable: If status cannot be retrieved (recoverable)
        """
        # Skip if no client available
        if self._client is None:
            logger.debug("No client available for API health check")
            return

        # Get resilience status
        if not hasattr(self._client, "get_resilience_status"):
            logger.debug("Client does not expose get_resilience_status")
            return

        try:
            status = self._client.get_resilience_status()
        except Exception as exc:
            raise RiskGuardDataUnavailable(
                guard_name=self.name,
                message=f"Failed to get API resilience status: {exc}",
                details={"error": str(exc)},
            ) from exc

        if status is None:
            return

        # Parse metrics
        metrics = (status or {}).get("metrics") or {}
        error_rate = float(metrics.get("error_rate", 0.0))
        endpoint_metrics = metrics.get("endpoints") if isinstance(metrics, dict) else None
        endpoint_errors: dict[str, int] = {}
        if isinstance(endpoint_metrics, dict):
            for category, entry in endpoint_metrics.items():
                if not isinstance(entry, dict):
                    continue
                total_errors = entry.get("total_errors")
                try:
                    total_errors_int = int(total_errors) if total_errors is not None else 0
                except (TypeError, ValueError):
                    total_errors_int = 0
                if total_errors_int > 0:
                    endpoint_errors[str(category)] = total_errors_int

        # Parse rate limit usage (may be string like "45%" or float)
        rate_limit_usage = (status or {}).get("rate_limit_usage", 0.0)
        if isinstance(rate_limit_usage, str):
            rate_limit_usage = float(rate_limit_usage.rstrip("%")) / 100.0
        else:
            rate_limit_usage = float(rate_limit_usage or 0.0)

        # Parse circuit breakers
        breakers = (status or {}).get("circuit_breakers") or {}
        open_breakers = []
        for breaker_name, entry in breakers.items():
            if isinstance(entry, dict):
                breaker_state = entry.get("state", "")
            else:
                breaker_state = str(entry)
            if breaker_state == "open":
                open_breakers.append(breaker_name)

        # Get thresholds
        error_rate_threshold, rate_limit_threshold = self._get_thresholds()

        # Check trip conditions
        trip_reasons = []
        details: dict[str, Any] = {
            "error_rate": error_rate,
            "error_rate_threshold": error_rate_threshold,
            "rate_limit_usage": rate_limit_usage,
            "rate_limit_threshold": rate_limit_threshold,
        }
        if isinstance(metrics, dict):
            details["total_requests"] = metrics.get("total_requests", 0)
            details["total_errors"] = metrics.get("total_errors", 0)
        if endpoint_errors:
            details["endpoint_errors"] = endpoint_errors

        if open_breakers:
            trip_reasons.append(f"circuit breakers open: {', '.join(open_breakers)}")
            details["open_breakers"] = open_breakers

        if error_rate >= error_rate_threshold:
            trip_reasons.append(
                f"error rate {error_rate:.1%} >= threshold {error_rate_threshold:.1%}"
            )

        if rate_limit_usage >= rate_limit_threshold:
            trip_reasons.append(
                f"rate limit usage {rate_limit_usage:.1%} >= threshold {rate_limit_threshold:.1%}"
            )

        if trip_reasons:
            logger.warning(
                "API health degraded",
                reasons=trip_reasons,
                details=details,
                operation="api_health_guard",
            )
            raise RiskLimitExceeded(
                guard_name=self.name,
                message=f"API health degraded: {'; '.join(trip_reasons)}",
                details=details,
            )


__all__ = ["ApiHealthGuard"]
