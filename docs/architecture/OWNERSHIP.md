# Module Ownership (Subsystem Map)

---
status: draft
---

This document maps **subsystems → directories → “how to test”**.

It exists to make changes safer and to help agents route work to the right place.

## Quick rules
- Prefer changes that stay within a single subsystem.
- If you must cross subsystems, name the seam explicitly (see `docs/architecture/SEAMS.md`).
- When adding a new module, update this map if it introduces a new “place to look”.

## Subsystem map

### App / composition / runtime
- **Dirs:** `src/gpt_trader/app/`
- **What lives here:** DI container, config wiring, runtime paths, composition root
- **When you touch this:** run unit tests + type check
  - `uv run pytest tests/unit -q`
  - `uv run mypy src`

### CLI
- **Dirs:** `src/gpt_trader/cli/`
- **What lives here:** CLI parsing, command wiring, operator entrypoints
- **When you touch this:** run unit tests (CLI + core)
  - `uv run pytest tests/unit -q`

### Live trading engine
- **Dirs:** `src/gpt_trader/features/live_trade/`
- **What lives here:** live loop, engines, execution pipeline, risk, reconciliation
- **When you touch this:** run unit + (optionally) targeted chaos/engine tests
  - `uv run pytest tests/unit/gpt_trader/features/live_trade -q`

### Execution / order placement
- **Dirs:** `src/gpt_trader/features/live_trade/execution/`
- **What lives here:** order submission, broker executor, guard manager, telemetry hooks
- **When you touch this:** run execution-related unit tests
  - `uv run pytest tests/unit/gpt_trader/features/live_trade/execution -q`

### Brokerages (adapter layer)
- **Dirs:** `src/gpt_trader/features/brokerages/`
- **What lives here:** exchange adapters (Coinbase), paper broker, mock broker
- **When you touch this:** run brokerage unit tests
  - `uv run pytest tests/unit/gpt_trader/features/brokerages -q`

### Backtesting engine (canonical)
- **Dirs:** `src/gpt_trader/backtesting/`
- **What lives here:** canonical backtesting engine
- **When you touch this:** run backtesting unit tests
  - `uv run pytest tests/unit -q`

### Optimization
- **Dirs:**
  - `src/gpt_trader/features/optimize/`
  - `src/gpt_trader/features/strategy_tools/`
- **What lives here:** optimization pipelines and shared strategy helpers
- **When you touch this:** run unit tests
  - `uv run pytest tests/unit -q`

### Persistence / storage
- **Dirs:** `src/gpt_trader/persistence/`
- **What lives here:** sqlite stores, event store, order persistence
- **When you touch this:** run persistence unit tests
  - `uv run pytest tests/unit/gpt_trader/persistence -q`

### Observability / monitoring
- **Dirs:**
  - `src/gpt_trader/monitoring/` (metrics, health, alerts, tracing)
  - `src/gpt_trader/logging/`
- **What lives here:** metrics, traces, logging, health
- **When you touch this:** run unit tests
  - `uv run pytest tests/unit -q`

### Security
- **Dirs:** `src/gpt_trader/security/`
- **What lives here:** validation, sanitization, auth-related helpers
- **When you touch this:** run unit tests + any security-specific suites
  - `uv run pytest tests/unit -q`

### Preflight
- **Dirs:** `src/gpt_trader/preflight/` and `scripts/production_preflight.py`
- **What lives here:** environment readiness validation
- **When you touch this:** run preflight tests + unit tests
  - `uv run pytest tests/unit -q`

## “When you touch X, run Y” (matrix)

| Change type | Minimum checks |
|------------|-----------------|
| docs-only | `uv run python scripts/maintenance/docs_link_audit.py` |
| config schema / env var docs | `uv run pytest tests/unit/gpt_trader/app -q`; `uv run python scripts/maintenance/docs_currency_scan.py --fail-on missing,stale` |
| execution / order placement | `uv run pytest tests/unit/gpt_trader/features/live_trade/execution -q` |
| broker adapters | `uv run pytest tests/unit/gpt_trader/features/brokerages -q` |
