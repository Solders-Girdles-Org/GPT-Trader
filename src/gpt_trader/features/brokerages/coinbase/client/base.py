"""
Simplified Base Client.
Handles HTTP requests with basic retries.
"""

import json
import random
import threading
import time
from typing import Any, cast

import requests

from gpt_trader.config.constants import (
    ADAPTIVE_THROTTLE_ENABLED,
    CACHE_DEFAULT_TTL,
    CACHE_ENABLED,
    CACHE_MAX_SIZE,
    CIRCUIT_BREAKER_ENABLED,
    CIRCUIT_FAILURE_THRESHOLD,
    CIRCUIT_RECOVERY_TIMEOUT,
    CIRCUIT_SUCCESS_THRESHOLD,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    MAX_HTTP_RETRIES,
    METRICS_ENABLED,
    METRICS_HISTORY_SIZE,
    PRIORITY_ENABLED,
    PRIORITY_THRESHOLD_CRITICAL,
    PRIORITY_THRESHOLD_HIGH,
    RATE_LIMIT_WARNING_THRESHOLD,
    RATE_LIMIT_WINDOW_SECONDS,
    RETRY_BACKOFF_MULTIPLIER,
    RETRY_BASE_DELAY,
    THROTTLE_TARGET_UTILIZATION,
)
from gpt_trader.features.brokerages.coinbase.client.circuit_breaker import (
    CircuitBreakerRegistry,
    CircuitOpenError,
)
from gpt_trader.features.brokerages.coinbase.client.constants import (
    BASE_URL,
    DEFAULT_API_VERSION,
    ENDPOINT_MAP,
)
from gpt_trader.features.brokerages.coinbase.client.metrics import (
    APIMetricsCollector,
    categorize_endpoint,
)
from gpt_trader.features.brokerages.coinbase.client.priority import (
    PriorityManager,
    RequestDeferredError,
)
from gpt_trader.features.brokerages.coinbase.client.response_cache import ResponseCache
from gpt_trader.features.brokerages.coinbase.errors import InvalidRequestError, map_http_error
from gpt_trader.logging.correlation import get_correlation_id
from gpt_trader.monitoring.metrics_collector import record_counter, record_gauge, record_histogram
from gpt_trader.monitoring.tracing import trace_span
from gpt_trader.utilities.logging_patterns import get_logger

# Maximum delay for retry backoff to prevent excessive waits
MAX_RETRY_DELAY_SECONDS = 30.0
# Maximum value for retry-after header to prevent DoS via server response
MAX_RETRY_AFTER_SECONDS = 60.0
# Jitter range to add randomness to backoff timing
RETRY_JITTER_MAX_SECONDS = 0.5

logger = get_logger(__name__, component="coinbase_client")


class CoinbaseClientBase:
    def __init__(
        self,
        base_url: str = BASE_URL,
        auth: Any | None = None,
        api_mode: str = "advanced",
        timeout: int | None = None,
        api_version: str = DEFAULT_API_VERSION,
        rate_limit_per_minute: int | None = None,
        enable_throttle: bool = True,
        enable_keep_alive: bool = True,
        enable_response_cache: bool | None = None,
        **kwargs: Any,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.api_mode = api_mode
        self.timeout = timeout if timeout is not None else DEFAULT_HTTP_TIMEOUT
        self.api_version = api_version
        self.rate_limit_per_minute = (
            rate_limit_per_minute
            if rate_limit_per_minute is not None
            else DEFAULT_RATE_LIMIT_PER_MINUTE
        )
        self.enable_throttle = enable_throttle
        self.enable_keep_alive = enable_keep_alive
        self._is_cdp = (
            hasattr(auth, "key_name") and auth.key_name.startswith("organizations/")
            if auth
            else False
        )

        self.session = requests.Session()
        self._transport: Any | None = None  # For testing
        self._request_times: list[float] = []
        self._rate_limit_lock = threading.Lock()  # Thread-safe rate limiting

        # API resilience components (feature-flagged)
        self._response_cache: ResponseCache | None = None
        response_cache_enabled = (
            CACHE_ENABLED if enable_response_cache is None else enable_response_cache
        )
        if response_cache_enabled:
            self._response_cache = ResponseCache(
                default_ttl=CACHE_DEFAULT_TTL,
                max_size=CACHE_MAX_SIZE,
            )

        self._circuit_breaker: CircuitBreakerRegistry | None = None
        if CIRCUIT_BREAKER_ENABLED:
            self._circuit_breaker = CircuitBreakerRegistry(
                default_failure_threshold=CIRCUIT_FAILURE_THRESHOLD,
                default_recovery_timeout=CIRCUIT_RECOVERY_TIMEOUT,
                default_success_threshold=CIRCUIT_SUCCESS_THRESHOLD,
            )

        self._metrics: APIMetricsCollector | None = None
        if METRICS_ENABLED:
            self._metrics = APIMetricsCollector(max_history=METRICS_HISTORY_SIZE)

        self._priority_manager: PriorityManager | None = None
        if PRIORITY_ENABLED:
            self._priority_manager = PriorityManager(
                threshold_high=PRIORITY_THRESHOLD_HIGH,
                threshold_critical=PRIORITY_THRESHOLD_CRITICAL,
            )

        self._adaptive_throttle_enabled = ADAPTIVE_THROTTLE_ENABLED
        self._throttle_target = THROTTLE_TARGET_UTILIZATION

    def set_transport_for_testing(self, transport: Any) -> None:
        self._transport = transport

    def _get_endpoint_path(self, endpoint_name: str, **kwargs: str) -> str:
        """
        Resolve endpoint name to full path using centralized ENDPOINT_MAP.

        Args:
            endpoint_name: Key from ENDPOINT_MAP (e.g., "products", "cfm_positions")
            **kwargs: Path parameters to substitute (e.g., product_id="BTC-USD")

        Returns:
            Fully resolved endpoint path.

        Raises:
            InvalidRequestError: If endpoint not found or API mode invalid.
        """
        if self.api_mode not in ENDPOINT_MAP:
            raise InvalidRequestError(f"Unknown API mode: {self.api_mode}")

        mode_map = ENDPOINT_MAP.get(self.api_mode, {})
        path = mode_map.get(endpoint_name)

        if path is None:
            # Check if it exists in advanced mode but not current mode
            if endpoint_name in ENDPOINT_MAP["advanced"] and self.api_mode != "advanced":
                raise InvalidRequestError(
                    f"Endpoint {endpoint_name} not available in {self.api_mode} mode"
                )
            raise InvalidRequestError(f"Unknown endpoint: {endpoint_name}")

        try:
            return path.format(**kwargs)
        except KeyError:
            return path

    def _make_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _normalize_path(self, path: str) -> str:
        if path.startswith(self.base_url):
            path = path[len(self.base_url) :]
        if path.startswith("/"):
            return path
        return path

    def _build_path_with_params(self, path: str, params: dict[str, Any] | None) -> str:
        if not params:
            return path
        query_parts = []
        for k, v in params.items():
            if v is not None:
                query_parts.append(f"{k}={v}")
        if not query_parts:
            return path

        connector = "&" if "?" in path else "?"
        return f"{path}{connector}{'&'.join(query_parts)}"

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        pagination_key: str | None = None,
        cursor_param: str = "cursor",
        cursor_field: str | None = None,
    ) -> Any:
        """
        Generator that yields items from paginated endpoints.
        """
        params = params or {}
        cursor = params.get(cursor_param)
        has_more = True

        while has_more:
            current_params = params.copy()
            if cursor:
                current_params[cursor_param] = cursor

            response = self.get(path, current_params)

            # Extract data list
            data = response
            if pagination_key and isinstance(response, dict):
                data = response.get(pagination_key, [])

            if isinstance(data, list):
                yield from data
            elif data:
                # Yield single item if not a list (rare for pagination but possible)
                yield data

            # Update cursor
            if isinstance(response, dict):
                # Coinbase APIs vary: 'cursor', 'next_cursor', 'pagination' dict
                next_cursor = None

                if cursor_field:
                    next_cursor = response.get(cursor_field)
                else:
                    next_cursor = response.get("cursor") or response.get("next_cursor")

                # Some APIs return a 'pagination' object
                if not next_cursor and "pagination" in response:
                    # If cursor_field is inside pagination, we assume standard structure
                    # or user must handle complex extraction.
                    # For now, fallback to standard pagination.next_cursor
                    next_cursor = response["pagination"].get("next_cursor")

                if next_cursor and next_cursor != cursor:
                    cursor = next_cursor
                else:
                    has_more = False
            else:
                has_more = False

    def _check_rate_limit(self) -> None:
        if not self.enable_throttle:
            return

        with self._rate_limit_lock:
            now = time.time()
            # Remove requests older than 1 minute
            self._request_times = [
                t for t in self._request_times if now - t < RATE_LIMIT_WINDOW_SECONDS
            ]

            current_usage = len(self._request_times) / self.rate_limit_per_minute

            # Adaptive throttling: proactive pacing before hitting limit
            if self._adaptive_throttle_enabled and current_usage >= self._throttle_target:
                # Calculate proportional delay to smooth out request rate
                overage = current_usage - self._throttle_target
                delay = min(overage * 0.5, 1.0)  # Cap at 1 second
                if delay > 0.01:  # Only sleep if meaningful
                    self._rate_limit_lock.release()
                    try:
                        time.sleep(delay)
                    finally:
                        self._rate_limit_lock.acquire()
                    # Refresh after sleep
                    now = time.time()
                    self._request_times = [
                        t for t in self._request_times if now - t < RATE_LIMIT_WINDOW_SECONDS
                    ]

            if len(self._request_times) >= self.rate_limit_per_minute:
                logger.info(
                    f"Rate limit reached ({len(self._request_times)}/{self.rate_limit_per_minute}). throttling..."
                )
                sleep_time = RATE_LIMIT_WINDOW_SECONDS - (now - self._request_times[0]) + 1
                if sleep_time > 0:
                    # Release lock while sleeping to avoid blocking other threads
                    self._rate_limit_lock.release()
                    try:
                        time.sleep(sleep_time)
                    finally:
                        self._rate_limit_lock.acquire()
                # After sleep, clean up again
                now = time.time()
                self._request_times = [
                    t for t in self._request_times if now - t < RATE_LIMIT_WINDOW_SECONDS
                ]
            elif (
                len(self._request_times)
                >= self.rate_limit_per_minute * RATE_LIMIT_WARNING_THRESHOLD
            ):
                logger.warning(
                    "Approaching rate limit: %d/%d requests in last minute",
                    len(self._request_times),
                    self.rate_limit_per_minute,
                )

            self._request_times.append(now)

    def get_rate_limit_usage(self) -> float:
        """Return current rate limit usage as a fraction (0.0 to 1.0+)."""
        with self._rate_limit_lock:
            now = time.time()
            active_requests = sum(
                1 for t in self._request_times if now - t < RATE_LIMIT_WINDOW_SECONDS
            )
            return (
                active_requests / self.rate_limit_per_minute
                if self.rate_limit_per_minute > 0
                else 0.0
            )

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        start_time = time.perf_counter()

        # Check priority before proceeding (only block lower priority under pressure)
        if self._priority_manager:
            usage = self.get_rate_limit_usage()
            if not self._priority_manager.should_allow(path, usage):
                raise RequestDeferredError(path, self._priority_manager.get_priority(path), usage)

        # Check circuit breaker
        if self._circuit_breaker and not self._circuit_breaker.can_proceed(path):
            breaker = self._circuit_breaker.get_breaker(path)
            status = breaker.get_status()
            raise CircuitOpenError(
                self._circuit_breaker._categorize_endpoint(path),
                status.get("time_until_half_open", 0),
            )

        # Check cache for GET requests
        if method == "GET" and self._response_cache:
            cached = self._response_cache.get(path)
            if cached is not None:
                return cached

        # Wrap HTTP request in trace span for distributed tracing
        with trace_span(
            "http_request",
            {
                "http.method": method,
                "http.path": path,
                "api.mode": self.api_mode,
            },
        ) as span:
            return self._perform_http_request(method, path, payload, start_time, span)

    def _is_public_market_endpoint(self, path: str) -> bool:
        normalized_path = self._normalize_path(path)
        normalized_path = normalized_path.split("?", 1)[0]
        if normalized_path and not normalized_path.startswith("/"):
            normalized_path = "/" + normalized_path
        return self.api_mode == "advanced" and normalized_path.startswith(
            "/api/v3/brokerage/market/"
        )

    def _build_auth_path(self, path: str) -> str:
        auth_path = path if path.startswith("/") else f"/{path}"
        if path.startswith("http"):
            auth_path = "/" + path.split("/", 3)[-1]
        if "?" in auth_path:
            auth_path = auth_path.split("?", 1)[0]
        return auth_path

    def _apply_auth_headers(
        self,
        headers: dict[str, str],
        method: str,
        path: str,
        payload: dict | None,
    ) -> None:
        auth = self.auth
        if not auth:
            return

        if hasattr(auth, "get_headers"):
            auth_path = self._build_auth_path(path)
            if payload:
                headers.update(auth.get_headers(method, auth_path, payload))
            else:
                headers.update(auth.get_headers(method, auth_path))
        elif hasattr(auth, "sign"):
            headers.update(auth.sign(method, path, payload))

    def _build_headers(self, method: str, path: str, payload: dict | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "gpt-trader/v2",
            "CB-VERSION": self.api_version,
        }

        if self.auth and not self._is_public_market_endpoint(path):
            self._apply_auth_headers(headers, method, path, payload)

        corr_id = get_correlation_id()
        if corr_id:
            headers["X-Correlation-Id"] = corr_id

        return headers

    def _perform_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict | None,
    ) -> requests.Response:
        if self._transport:
            status, resp_headers, text = self._transport(
                method, url, headers, json.dumps(payload) if payload else None, self.timeout
            )
            resp = requests.Response()
            resp.status_code = status
            resp._content = text.encode() if text else b""
            resp.headers = resp_headers
            return resp

        return self.session.request(
            method, url, json=payload, headers=headers, timeout=self.timeout
        )

    def _record_api_retry(self, path: str, reason: str) -> None:
        category = categorize_endpoint(path)
        record_counter(
            "gpt_trader_api_retries_total",
            labels={"category": category, "reason": reason},
        )

    def _sleep_retry_after(self, resp: requests.Response) -> None:
        try:
            retry_after = float(resp.headers.get("retry-after", 1))
        except ValueError:
            retry_after = 1.0
        retry_after = min(retry_after, MAX_RETRY_AFTER_SECONDS)
        time.sleep(retry_after)

    def _sleep_backoff(self, attempt: int) -> None:
        base_delay = RETRY_BASE_DELAY * (RETRY_BACKOFF_MULTIPLIER**attempt)
        jitter = random.uniform(0, RETRY_JITTER_MAX_SECONDS)
        delay = min(base_delay + jitter, MAX_RETRY_DELAY_SECONDS)
        time.sleep(delay)

    def _raise_client_error(self, resp: requests.Response) -> None:
        try:
            data = resp.json()
            msg = data.get("message", "Bad request")
            code = data.get("error")
        except (json.JSONDecodeError, ValueError, KeyError):
            msg = resp.text
            code = None

        if resp.status_code == 400:
            raise InvalidRequestError(msg)
        raise map_http_error(resp.status_code, code, msg)

    def _record_success(self, path: str, resp: requests.Response, span: Any | None) -> None:
        if self._circuit_breaker:
            self._circuit_breaker.record_success(path)
        if span:
            span.set_attribute("http.status_code", resp.status_code)

    def _parse_response(self, resp: requests.Response) -> dict[str, Any]:
        if resp.content:
            try:
                return cast(dict[Any, Any], resp.json())
            except ValueError:
                return {"raw": resp.text}
        return {}

    def _cache_response(self, method: str, path: str, result: dict[str, Any]) -> None:
        if method == "GET" and self._response_cache:
            self._response_cache.set(path, result)

    def _invalidate_cache(self, method: str, path: str) -> None:
        if method not in ("POST", "DELETE") or not self._response_cache:
            return

        path_lower = path.lower()
        if "order" in path_lower:
            self._response_cache.invalidate("**/orders*")
            self._response_cache.invalidate("**/fills*")
        elif "account" in path_lower or "position" in path_lower:
            self._response_cache.invalidate("**/accounts*")
            self._response_cache.invalidate("**/positions*")

    def _record_request_metrics(
        self,
        path: str,
        latency_seconds: float,
        is_error: bool,
        is_rate_limited: bool,
        span: Any | None,
    ) -> None:
        latency_ms = latency_seconds * 1000

        if self._metrics:
            self._metrics.record_request(
                path, latency_ms, error=is_error, rate_limited=is_rate_limited
            )

        category = categorize_endpoint(path)
        request_result = "error" if is_error else "success"
        record_histogram(
            "gpt_trader_api_latency_seconds",
            latency_seconds,
            labels={"category": category, "result": request_result},
        )
        record_counter(
            "gpt_trader_api_requests_total",
            labels={"category": category, "result": request_result},
        )

        if span:
            span.set_attribute("http.latency_ms", latency_ms)
            span.set_attribute("http.rate_limited", is_rate_limited)

    def _perform_http_request(
        self,
        method: str,
        path: str,
        payload: dict | None,
        start_time: float,
        span: Any | None,
    ) -> dict:
        """Execute the HTTP request with retries and error handling.

        This is extracted to work with trace_span context manager.
        """
        is_error = False
        is_rate_limited = False

        self._check_rate_limit()
        url = self._make_url(path)
        headers = self._build_headers(method, path, payload)

        try:
            # Retry logic with configurable parameters
            max_retries = MAX_HTTP_RETRIES
            resp: requests.Response | None = None
            for attempt in range(max_retries + 1):
                try:
                    resp = self._perform_request(method, url, headers, payload)

                    if resp.status_code == 429:
                        is_rate_limited = True
                        self._record_api_retry(path, "rate_limited")
                        self._sleep_retry_after(resp)
                        continue

                    if 500 <= resp.status_code < 600:
                        is_error = True
                        if attempt < max_retries:
                            self._record_api_retry(path, "server_error")
                            self._sleep_backoff(attempt)
                            continue

                    if 400 <= resp.status_code < 500:
                        is_error = True
                        self._raise_client_error(resp)

                    resp.raise_for_status()

                    # Request ultimately succeeded; keep error metrics aligned with the final
                    # outcome (retries are tracked separately via _record_api_retry).
                    is_error = False

                    # Success - record in circuit breaker and span
                    self._record_success(path, resp, span)

                    result = self._parse_response(resp)

                    # Cache successful GET responses
                    self._cache_response(method, path, result)

                    # Invalidate cache on mutations
                    self._invalidate_cache(method, path)

                    return result

                except requests.exceptions.HTTPError as e:
                    is_error = True
                    # Catch HTTPError from raise_for_status (e.g. 500s)
                    try:
                        data = e.response.json()
                        msg = data.get("message", str(e))
                        code = data.get("error")
                    except (json.JSONDecodeError, ValueError, KeyError, AttributeError):
                        msg = str(e)
                        code = None
                    raise map_http_error(e.response.status_code, code, msg)

                except (requests.ConnectionError, requests.Timeout, ConnectionError):
                    is_error = True
                    if attempt < max_retries:
                        self._record_api_retry(path, "network_timeout")
                        self._sleep_backoff(attempt)
                        continue
                    raise

            # If loop finishes without return (e.g. all 429s handled but retries exhausted)
            if resp is not None and resp.status_code == 429:
                raise map_http_error(429, "rate_limited", "Rate limit exceeded (rate_limited)")

            return {}

        except Exception as e:
            is_error = True
            # Record failure in circuit breaker
            if self._circuit_breaker:
                self._circuit_breaker.record_failure(path, e)
            # Mark span as errored
            if span:
                span.set_attribute("error", True)
                span.set_attribute("error.type", type(e).__name__)
            raise

        finally:
            # Calculate latency
            latency_seconds = time.perf_counter() - start_time
            self._record_request_metrics(
                path,
                latency_seconds,
                is_error,
                is_rate_limited,
                span,
            )

    # === API Resilience Status Methods ===

    def get_api_metrics(self) -> dict[str, Any] | None:
        """Get API metrics summary for monitoring."""
        if self._metrics:
            return self._metrics.get_summary()
        return None

    def get_circuit_breaker_status(self) -> dict[str, Any] | None:
        """Get circuit breaker status for all endpoint categories."""
        if self._circuit_breaker:
            return self._circuit_breaker.get_all_status()
        return None

    def get_cache_stats(self) -> dict[str, Any] | None:
        """Get response cache statistics."""
        if self._response_cache:
            return self._response_cache.get_stats()
        return None

    def get_priority_stats(self) -> dict[str, Any] | None:
        """Get request priority enforcement statistics."""
        if self._priority_manager:
            return self._priority_manager.get_stats()
        return None

    def get_resilience_status(self) -> dict[str, Any]:
        """Get comprehensive API resilience status for monitoring.

        Returns a dict suitable for status reporting with all resilience
        component states. Also records current gauges for Prometheus export.
        """
        rate_limit_usage = self.get_rate_limit_usage()
        metrics = self.get_api_metrics()
        circuit_breakers = self.get_circuit_breaker_status()

        # Record Prometheus gauges for current state
        record_gauge("gpt_trader_rate_limit_usage_ratio", rate_limit_usage)

        if metrics:
            error_rate = metrics.get("error_rate", 0.0)
            record_gauge("gpt_trader_api_error_rate", error_rate)

        # Record circuit breaker state gauges
        # State values: 0=closed (healthy), 1=half_open (recovering), 2=open (blocking)
        state_values = {"closed": 0.0, "half_open": 1.0, "open": 2.0}
        if circuit_breakers:
            for category, status in circuit_breakers.items():
                state = status.get("state", "closed")
                state_value = state_values.get(state, 0.0)
                record_gauge(
                    "gpt_trader_circuit_breaker_state",
                    state_value,
                    labels={"category": category},
                )

        return {
            "rate_limit_usage": rate_limit_usage,
            "metrics": metrics,
            "circuit_breakers": circuit_breakers,
            "cache": self.get_cache_stats(),
            "priority": self.get_priority_stats(),
            "adaptive_throttle_enabled": self._adaptive_throttle_enabled,
        }

    def close(self) -> None:
        """Close the HTTP session and release resources.

        Should be called when the client is no longer needed to prevent
        connection pool accumulation across multiple client instances.
        """
        if self.session:
            try:
                self.session.close()
                logger.debug("HTTP session closed")
            except Exception as e:
                logger.warning(f"Error closing HTTP session: {e}")

    def __enter__(self) -> "CoinbaseClientBase":
        """Context manager entry."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit - ensures session cleanup."""
        self.close()

    def __del__(self) -> None:
        """Destructor - ensures session cleanup on garbage collection."""
        try:
            self.close()
        except Exception:
            pass  # Suppress errors during interpreter shutdown

    # Helper aliases used by mixins
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if params:
            path = self._build_path_with_params(path, params)
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, payload)

    def delete(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self._request("DELETE", path, payload)
