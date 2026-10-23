# Reliability Layer

---
status: current
---

This document describes the reliability layer that protects GPT-Trader during
startup and live trading. It covers the decision matrix, configuration defaults,
the chaos harness for fault injection, and a rollout checklist.

## Scope

Reliability is delivered by four packages that work together:

- API Health Guard: runtime checks of error rate, rate limits, and circuit breakers.
- Preflight Diagnostics: startup checks for API connectivity, accounts, and market data.
- Graceful Degradation: pause and reduce-only responses when safety checks trip.
- Chaos Harness: fault injection utilities for testing degradation behavior.

Implementation references:

- `src/gpt_trader/features/live_trade/execution/guards/api_health.py`
- `src/gpt_trader/preflight/checks/diagnostics.py`
- `src/gpt_trader/features/live_trade/degradation.py` (canonical location)
- `tests/support/chaos.py`

## Decision Matrix

| Trigger | Signal | Response | Config |
| --- | --- | --- | --- |
| Preflight diagnostics fail | `scripts/production_preflight.py` errors on API, accounts, or market data | Exit non-zero; block startup unless warn-only | `--warn-only` or `GPT_TRADER_PREFLIGHT_WARN_ONLY=1` |
| API health trip | ApiHealthGuard sees open circuit or thresholds | Cancel open orders, reduce-only, global pause | `RISK_API_HEALTH_COOLDOWN_SECONDS` |
| Mark staleness | `check_mark_staleness` true | Pause symbol; allow reduce-only if configured | `RISK_MARK_STALENESS_THRESHOLD_SECONDS`, `RISK_MARK_STALENESS_COOLDOWN_SECONDS`, `RISK_MARK_STALENESS_ALLOW_REDUCE_ONLY` |
| Slippage failures | Repeated slippage guard ValidationError | Pause symbol after threshold | `RISK_SLIPPAGE_FAILURE_PAUSE_AFTER`, `RISK_SLIPPAGE_PAUSE_SECONDS` |
| Validation infra failure | ValidationFailureTracker escalation | Reduce-only + global pause | `RISK_VALIDATION_FAILURE_COOLDOWN_SECONDS` |
| Preview failures | Preview exceptions reach threshold | Disable preview for the session | `RISK_PREVIEW_FAILURE_DISABLE_AFTER` |
| Broker read failures | Consecutive balance/position read failures | Global pause (reduce-only allowed) | `RISK_BROKER_OUTAGE_MAX_FAILURES`, `RISK_BROKER_OUTAGE_COOLDOWN_SECONDS` |
| Order reconciliation drift | Broker open orders include bot-owned IDs missing from open_orders/orders_store | Reduce-only + global pause after 3 consecutive detections; attempt cancels; alert operator | `ORDER_RECONCILIATION_DRIFT_MAX_FAILURES` (constant), `RISK_API_HEALTH_COOLDOWN_SECONDS` |
| WS max reconnect | WebSocket exceeds max reconnection attempts | Global pause + callback for degradation | `GPT_TRADER_WS_RECONNECT_MAX_ATTEMPTS`, `GPT_TRADER_WS_RECONNECT_PAUSE_SECONDS` |

Notes:

- Global and symbol pauses allow reduce-only orders by default.
- Reduce-only blocks new positions but allows position-closing orders.

## Config Knobs and Defaults

API Health Guard:

| Env Var | Default | Purpose |
| --- | --- | --- |
| `RISK_API_ERROR_RATE_THRESHOLD` | `0.2` | Error rate threshold to trip the guard |
| `RISK_API_RATE_LIMIT_USAGE_THRESHOLD` | `0.9` | Rate limit usage threshold to trip the guard |

Graceful Degradation:

| Env Var | Default | Purpose |
| --- | --- | --- |
| `RISK_API_HEALTH_COOLDOWN_SECONDS` | `300` | Global pause duration after API health trip |
| `RISK_MARK_STALENESS_THRESHOLD_SECONDS` | `30` | Max mark age before symbol is considered stale |
| `RISK_MARK_STALENESS_COOLDOWN_SECONDS` | `120` | Per-symbol pause when mark data is stale |
| `RISK_MARK_STALENESS_ALLOW_REDUCE_ONLY` | `1` | Allow reduce-only during mark staleness |
| `RISK_SLIPPAGE_FAILURE_PAUSE_AFTER` | `3` | Failures before symbol pause |
| `RISK_SLIPPAGE_PAUSE_SECONDS` | `60` | Per-symbol pause duration for slippage |
| `RISK_VALIDATION_FAILURE_COOLDOWN_SECONDS` | `180` | Global pause after validation infra failure |
| `RISK_PREVIEW_FAILURE_DISABLE_AFTER` | `5` | Failures before preview auto-disable |
| `RISK_BROKER_OUTAGE_MAX_FAILURES` | `3` | Failures before global pause |
| `RISK_BROKER_OUTAGE_COOLDOWN_SECONDS` | `120` | Global pause duration for broker outage |

WebSocket Reconnection:

| Env Var | Default | Purpose |
| --- | --- | --- |
| `GPT_TRADER_WS_RECONNECT_BACKOFF_BASE` | `2.0` | Base delay in seconds for exponential backoff |
| `GPT_TRADER_WS_RECONNECT_BACKOFF_MAX` | `60.0` | Maximum delay cap (prevents unbounded growth) |
| `GPT_TRADER_WS_RECONNECT_BACKOFF_MULTIPLIER` | `2.0` | Exponential growth multiplier |
| `GPT_TRADER_WS_RECONNECT_JITTER_PCT` | `0.25` | Jitter ±25% to prevent thundering herd |
| `GPT_TRADER_WS_RECONNECT_MAX_ATTEMPTS` | `10` | Max attempts before triggering degradation (0=unlimited) |
| `GPT_TRADER_WS_RECONNECT_RESET_SECONDS` | `60.0` | Stable connection time to reset attempt counter |
| `GPT_TRADER_WS_RECONNECT_PAUSE_SECONDS` | `300` | Global pause duration when max attempts exceeded |

The reconnection algorithm uses exponential backoff with jitter:
- Delay = `base * (multiplier ^ attempt)`, capped at `max`
- Jitter randomizes ±`jitter_pct` to prevent synchronized reconnection storms
- Attempt counter resets after connection is stable for `reset_seconds`
- When max attempts exceeded, triggers `on_max_attempts_exceeded` callback for degradation

Preflight Diagnostics:

| Flag/Env | Default | Purpose |
| --- | --- | --- |
| `--warn-only` | off | Downgrade preflight errors to warnings |
| `GPT_TRADER_PREFLIGHT_WARN_ONLY` | `0` | Env alias for warn-only |
| `COINBASE_PREFLIGHT_SKIP_REMOTE` | unset | Skip remote checks (dev/offline) |
| `COINBASE_PREFLIGHT_FORCE_REMOTE` | unset | Force remote checks even on dev |

Health Monitoring:

| Env Var | Default | Purpose |
| --- | --- | --- |
| `GPT_TRADER_HEALTH_CHECK_INTERVAL` | `30.0` | Seconds between health check runner cycles |

Execution Resilience:

| Env Var | Default | Purpose |
| --- | --- | --- |
| `ORDER_SUBMISSION_RETRIES_ENABLED` | `1` | Enable broker submission retries |
| `BROKER_CALLS_USE_DEDICATED_EXECUTOR` | `1` | Run broker calls in a dedicated thread pool |

## Observability

Canonical reference for metrics, traces, structured logs, and alert query patterns:

- `docs/OBSERVABILITY.md`
- `docs/MONITORING_PLAYBOOK.md` (setup + incident workflows)

## Profiling & Memory

Lightweight profiling hooks for timing critical code paths and tracking memory usage.

### Profile Spans

Use `profile_span` context manager to time code blocks and emit to Prometheus histograms:

```python
from gpt_trader.monitoring.profiling import profile_span

with profile_span("fetch_positions") as sample:
    positions = await broker.list_positions()
# sample.duration_ms contains timing
```

All profile spans are recorded to the `gpt_trader_profile_duration_seconds` histogram with a `phase` label.

### Instrumented Hot Paths

| Phase | Location | Description |
| --- | --- | --- |
| `fetch_positions` | `_cycle_inner()` | Broker position fetch |
| `equity_computation` | `_cycle_inner()` | Balance fetch and equity calculation |
| `strategy_decision` | `_cycle_inner()` | Strategy decision logic |
| `order_placement` | `_cycle_inner()` | Order validation and submission |
| `pre_trade_validation` | `_validate_and_place_order()` | Full guard stack validation |

### Latency Histograms

Dedicated histograms for key operations:

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `gpt_trader_positions_fetch_seconds` | histogram | `result=ok\|error` | Time to fetch positions |
| `gpt_trader_equity_computation_seconds` | histogram | `result=ok\|error` | Time to calculate equity |
| `gpt_trader_profile_duration_seconds` | histogram | `phase` | General profiling spans |

### Memory Gauges

| Metric | Type | Description |
| --- | --- | --- |
| `gpt_trader_process_memory_mb` | gauge | Process RSS memory in MB |
| `gpt_trader_event_store_cache_size` | gauge | Events in memory cache |
| `gpt_trader_deque_cache_fill_ratio` | gauge | Cache fill ratio (0.0-1.0) |

Memory metrics are collected by `SystemMaintenanceService.report_system_status()` each trading cycle.

### Usage

```python
from gpt_trader.monitoring.profiling import profile_span, record_profile, ProfileSample

# Context manager (recommended)
with profile_span("custom_operation", {"key": "value"}):
    do_work()

# Manual timing
start = time.perf_counter()
do_work()
duration_ms = (time.perf_counter() - start) * 1000
record_profile("custom_operation", duration_ms)
```

## Performance Optimizations

### Batched Market Data Fetches

The trading engine uses `get_tickers()` to fetch ticker data for multiple symbols efficiently:

- **Advanced API mode**: Single `get_best_bid_ask()` call for all symbols, returns mid-price
- **Exchange API mode**: Falls back to sequential `get_ticker()` calls per symbol

The batch method is called once before the per-symbol loop, reducing API calls from N to 1 in Advanced mode. If a symbol is missing from the batch result (e.g., partial failure), it falls back to an individual `get_ticker()` call.

Implementation: `ProductService.get_tickers()` in `src/gpt_trader/features/brokerages/coinbase/rest/product_service.py`

### Concurrent Broker Calls

The strategy engine parallelizes independent broker operations:

- `_fetch_positions()` and `_audit_orders()` run concurrently via `asyncio.create_task()`
- Per-symbol ticker and candles fetches are bounded by `asyncio.Semaphore` (default 5 concurrent calls)
- Configurable via `config.max_concurrent_rest_calls` (defaults to 5)
- Optional dedicated executor via `config.broker_calls_use_dedicated_executor` (defaults to `True`)
- Order submission retries are optional via `config.order_submission_retries_enabled` / `ORDER_SUBMISSION_RETRIES_ENABLED` (defaults to `True`)

This reduces cycle latency by overlapping I/O-bound operations while preventing API rate limit exhaustion.

## Chaos Harness (Fault Injection)

The chaos harness is intended for deterministic tests that validate the
degradation responses without flaky network dependencies.

Key types and helpers (from `tests/support/chaos.py`):

- `FaultAction`: describes a fault (after_calls, times, raise_exc, return_value).
- `FaultPlan`: manages ordered faults with per-method call tracking.
- `ChaosBroker`: proxy that applies FaultPlan to a wrapped broker.
- Helpers: `fault_once`, `fault_after`, `fault_always`, `fault_sequence`.

Example: inject a broker outage after two balance reads.

```python
from tests.support.chaos import ChaosBroker, FaultPlan, fault_after
from gpt_trader.features.brokerages.mock import DeterministicBroker

plan = FaultPlan().add(
    "list_balances",
    fault_after(2, raise_exc=TimeoutError("broker read timeout")),
)
broker = ChaosBroker(DeterministicBroker(), plan)
```

Example: use a scenario preset.

```python
from tests.support.chaos import ChaosBroker, api_outage_scenario
from gpt_trader.features.brokerages.mock import DeterministicBroker

broker = ChaosBroker(DeterministicBroker(), api_outage_scenario())
```

Tip: avoid real delays in tests by passing a no-op `sleep_func` to `ChaosBroker` (for example,
`sleep_func=lambda _: None`). Prefer injected sleep over patching `time.sleep`.

## Readiness Evidence Checklist

This checklist produces evidence for the gates in
[Live Operations](production.md#live-gate-sequence) and the
[Direction](DIRECTION.md).

1. Run preflight against the relevant profiles:
   - `uv run python scripts/production_preflight.py --profile canary`
   - `uv run python scripts/production_preflight.py --profile prod`
2. Confirm reliability defaults in `.env` or exported overrides for RISK_* vars.
3. Run chaos tests:
   - `pytest tests/unit/support/test_chaos.py`
   - `pytest tests/unit/gpt_trader/features/live_trade/engines/test_strategy_engine_chaos.py`
4. Run a reduce-only canary session and archive guard and pause logs as evidence.
5. For each subsequent live profile run, archive the preflight report and guard
   events from that run.
6. Only use `--warn-only` during incident response and document the reason.
