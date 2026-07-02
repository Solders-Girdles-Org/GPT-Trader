# GPT-Trader Runtime Architecture Refactor Packet - 2026-06-29

---
status: current
---

## Scope

Bounded behavior-preserving architecture pass over the live-trade runtime engine.
No live broker/API calls, canary/prod operation, production preflight, money
movement, order submission, or account-capability check were run.

## Baseline

| Surface | Evidence |
| --- | --- |
| Branch | `codex/refactor-runtime-architecture-seams` |
| Base | `origin/main` at `46b58d8c` |
| Preserved local state | The canonical `main` checkout was clean but diverged (`ahead 1, behind 10`) before branching; this pass did not mutate that local `main` branch |
| Primary pressure point | `TradingEngine` still owned broker order audit and order reconciliation state after prior pure helper extractions |

## Refactor

| Boundary | Change |
| --- | --- |
| Order reconciliation | Added `OrderReconciliationService` in `src/gpt_trader/features/live_trade/engines/order_reconciliation.py` to own pending-order refresh, submit-id normalization, unknown bot-owned order recovery, drift tracking, drift cancellation, reduce-only escalation, and drift notifications |
| Order audit | Added `OrderAuditService` in `src/gpt_trader/features/live_trade/engines/order_audit.py` to own broker open-order loading, status-reporter order normalization, unfilled-order alert state, and periodic account metrics refresh |
| Trading engine | Reduced `TradingEngine` by delegating `_audit_orders`, `_reconcile_open_orders`, `_recover_unknown_bot_orders`, `_refresh_missing_persisted_orders`, `_handle_order_reconciliation_drift`, and `_record_unfilled_order_alerts` to focused collaborators while preserving existing private method names for current tests and call sites |

## Design Notes

- The services use provider callbacks for `_broker_calls`, `_orders_store`, and
  cycle count because current tests and runtime paths can replace those engine
  attributes after construction.
- The live order submission guard stack was intentionally left in
  `TradingEngine`; this pass only moved broker order audit/reconciliation
  orchestration.
- Existing pure broker-order mapping helpers remain in
  `order_record_mapping.py`; the new services reuse them instead of duplicating
  payload parsing.

## Verification Receipts

| Command | Result |
| --- | --- |
| `uv run ruff check src/gpt_trader/features/live_trade/engines/order_audit.py src/gpt_trader/features/live_trade/engines/order_reconciliation.py src/gpt_trader/features/live_trade/engines/strategy.py tests/unit/gpt_trader/features/live_trade/engines/test_strategy_engine_reconciliation.py tests/unit/gpt_trader/features/live_trade/engines/test_strategy_engine_health.py` | Passed |
| `uv run black --check src/gpt_trader/features/live_trade/engines/order_audit.py src/gpt_trader/features/live_trade/engines/order_reconciliation.py src/gpt_trader/features/live_trade/engines/strategy.py` | Passed |
| `uv run pytest tests/unit/gpt_trader/features/live_trade/engines/test_strategy_engine_reconciliation.py tests/unit/gpt_trader/features/live_trade/engines/test_strategy_engine_health.py::test_unfilled_order_alert_emitted_once tests/unit/gpt_trader/features/live_trade/engines/test_order_record_mapping.py -q` | 38 passed |
| `uv run mypy src/gpt_trader/features/live_trade/engines` | Passed, 15 source files |
| `uv run pytest tests/unit/gpt_trader/features/live_trade/engines -q` | 234 passed |
| `git diff --check` | Passed |
| `make ci-required` | Passed; included lint, docs audit, `mypy src`, `agent-regenerate --verify`, TUI CSS check, test guardrails, and `7320` unit tests with snapshots excluded |

## Deferred Work

- WebSocket health monitoring remains embedded in `TradingEngine`; it is a
  candidate for a later focused pass.
- The live order submission guard stack remains embedded because it is
  behavior-critical and deserves a separate, test-heavy refactor if moved.
