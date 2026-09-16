# Observability Reference

---
status: current
---

This page is intentionally thin. Metric names/labels and event schemas live in the code; read them there rather than from a copied table.

## Where the catalogs live

- Metrics: `src/gpt_trader/monitoring/metrics_collector.py` (`rg -n "metric" src/gpt_trader/monitoring`)
- Structured logging events and JSON schema: `src/gpt_trader/logging/` (`json_formatter.py`, `runtime_helpers.py`)

## Key Entrypoints

- Metrics collection: `src/gpt_trader/monitoring/metrics_collector.py`
- Metrics export: `scripts/monitoring/export_metrics.py`
- Tracing helpers: `src/gpt_trader/monitoring/tracing.py`

## Heartbeat Metrics

- `gpt_trader_ws_last_heartbeat_age_seconds` *(gauge)* — Age of the most recent WebSocket heartbeat at the time of the `StatusReporter` update, reported in **seconds**. When no timestamp is available the gauge stays at `0`.
- `gpt_trader_ws_heartbeat_lag_seconds` *(histogram)* — Distribution of observed heartbeat lag as it appears in the `/status` payload (seconds). Samples are recorded only when a valid heartbeat timestamp exists; bucket boundaries are fixed at 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, and 300.0 seconds.

## Health-Check Extension Contract

Health checks are registered in the health check runner and surfaced by the
health server's `/health` response. Registration entrypoints live in
`src/gpt_trader/monitoring/health_checks.py` (`HealthCheckRunner._health_check_registry`)
with the runner instantiated in
`src/gpt_trader/features/live_trade/engines/strategy.py`.

**Registration flow**
1. Add a check function in `src/gpt_trader/monitoring/health_checks.py` that returns
   `HealthCheckResult(healthy=..., details=...)`.
2. Register it in `HealthCheckRunner._health_check_registry` with a
   `HealthCheckDescriptor(name=..., mode="blocking"|"fast", run=...)`.
3. Ensure required dependencies are plumbed into `HealthCheckRunner` (broker,
   degradation state, risk manager, market data service).

**Expected result shape**
- `HealthState.add_check` stores results as:
  ```json
  {
    "checks": {
      "<name>": {
        "status": "pass" | "fail",
        "details": { "...": "..." }
      }
    }
  }
  ```
- Include `details["severity"]` as `warning` or `critical` when a failure should
  degrade or fail `/health`.
  - The health server appends a `performance` summary under `checks.performance`.
  - `signals` is returned as a top-level object alongside `checks` (not nested under it).

**Extension checklist**
- Pick a unique check `name` and add the check function in
  `src/gpt_trader/monitoring/health_checks.py`.
- Return `(healthy, details)` with `details["severity"]` when failing.
- Register in `HealthCheckRunner._health_check_registry` with the correct `mode`.
- Confirm the `/health` payload renders as expected (see [Monitoring Playbook](MONITORING_PLAYBOOK.md)).

## Dashboards & Runbooks

See [Monitoring Playbook](MONITORING_PLAYBOOK.md) and [Alert Runbooks](RUNBOOKS.md).
