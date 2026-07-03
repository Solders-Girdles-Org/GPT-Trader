# Core Seams (Strategy / Execution / Data / Config)

---
status: draft
---

This document identifies the **canonical seams** in GPT-Trader.

A “seam” is a boundary where:
- multiple subsystems meet,
- we want stable types/interfaces,
- and we want to reduce cross-layer coupling.

The goal is to make it obvious (for humans and agents) **where to plug in** new behavior and **what not to import**.

## 1) Strategy seam

### What it is
The strategy seam is where market state becomes a **decision** (buy/sell/hold, sizing intent, etc.).

### Canonical modules
- **Domain types / contracts:** `src/gpt_trader/core/strategy.py` (strategy-related types)
- **Strategy execution engine (live):** `src/gpt_trader/features/live_trade/engines/strategy.py`
- **Strategy implementations:** `src/gpt_trader/features/live_trade/strategies/` (and related strategy slices)

### Inputs / outputs (high-level)
- **Inputs:** market data, account state, positions, risk configuration, feature flags
- **Outputs:** domain actions/intents consumed by execution (orders, cancels, risk actions)

### Notes
- Prefer **returning domain-level actions** (from `gpt_trader.core`) over invoking broker APIs directly.
- Avoid having strategies import concrete broker/persistence implementations.
- The approval-path bridge for Stage 1 is
  `src/gpt_trader/features/strategy_tools/trade_idea_adapter.py`: it maps
  supported strategy decisions into proposed trade ideas through
  `TradeIdeaService.propose()` only, and never submits orders.
- The live engine wires that bridge behind the default-off
  `strategy_signal_proposals_enabled` gate. When set, `TradingEngine`
  (`src/gpt_trader/features/live_trade/engines/strategy.py`,
  `_handle_decision` → `_propose_strategy_decision`) routes each decision into
  the proposal path and returns before any broker interaction — proposal-only,
  no order submission. When unset (the default), decisions flow to direct
  execution exactly as before. Operator guide:
  [Trade-Idea Interface Design Notes](../specs/TRADE_IDEA_INTERFACES_DESIGN_NOTES.md#live-strategy-signal-routing-default-off).

## 2) Execution seam

### What it is
Execution is responsible for turning a strategy decision into **broker interactions**, while enforcing guardrails.

### Canonical modules
- **Order submission orchestration / retries / telemetry:**
  - `src/gpt_trader/features/live_trade/execution/order_submission.py`
  - `src/gpt_trader/features/live_trade/execution/broker_executor.py`
- **Guards / enforcement / degrade modes:**
  - `src/gpt_trader/features/live_trade/execution/guard_manager.py`
- **Broker abstraction (port):** `src/gpt_trader/features/brokerages/core/protocols.py`
- **Broker factories/adapters (adapter layer):** `src/gpt_trader/features/brokerages/`

### Inputs / outputs (high-level)
- **Inputs:** strategy action(s), config, current broker state, risk checks
- **Outputs:** order ids/results, persisted order records/events, telemetry/notifications

### Notes
- Strategy code should not call the broker directly; execution is the choke point.
- Prefer adding new execution behavior by extending `order_submission` or `broker_executor` rather than sprinkling broker calls.

## 3) Data seam

### What it is
The data seam is responsible for providing:
- live market data
- historical data for research/backtesting
- reference/product metadata

### Canonical modules
- **Brokerage market data services (live adapters):** `src/gpt_trader/features/brokerages/coinbase/` (and other brokerages)
- **Shared data slice:** `src/gpt_trader/features/data/`
- **Backtesting engine (canonical):** `src/gpt_trader/backtesting/`

### Notes
- Treat `src/gpt_trader/backtesting/` as the canonical engine.
- Research adapters that duplicate backtesting logic should be treated as legacy until consolidated.

## 4) Config seam

### What it is
Config is the shared input surface that defines:
- profile selection
- bot runtime settings
- risk/strategy parameters

### Canonical modules
- **Config models + loaders:** `src/gpt_trader/app/config/`
  - especially `bot_config.py` and profile loading
- **Config constants/types:** `src/gpt_trader/config/`
- **DI policy:** `docs/DI_POLICY.md`

### Notes
- `app/config` is used across layers as a shared input surface.
- Avoid importing `app.container` or CLI modules from lower layers.
- The risk-appetite derivation seam (stage 2 of
  [canonical risk-limit vocabulary](../decisions/canonical-risk-limit-vocabulary.md),
  #1120) lives in `src/gpt_trader/app/risk_budget_seed.py` and is wired behind
  the default-on `risk_budget_runtime_seed_enabled` gate. When set (the
  default), the composition root (`src/gpt_trader/app/container.py`) resolves
  the active `RiskBudget` version once at startup: the runtime risk manager's
  `daily_loss_limit_pct` / `max_exposure_pct` are seeded from the budget
  (`src/gpt_trader/app/containers/risk_validation.py`, which records the
  seeded budget version in startup telemetry), `allow_futures_leverage=false`
  clamps the effective CFM leverage cap to 1x, and `allow_naked_shorts=false`
  forces shorts off. When unset, runtime limits come from `BotConfig.risk`
  exactly as before. The budget defaults — 10% daily loss / 100% exposure —
  are looser than the legacy runtime 5% / 80%; the loosened breaker band is
  pinned by the MOCK_BROKER regression in
  `tests/integration/test_risk_budget_seeded_breaker.py`.

---

## Legacy / overlaps (callouts)

- **Research vs canonical backtesting:** if a research module provides backtesting behavior that also exists under
  `src/gpt_trader/backtesting/`, prefer consolidating on the canonical engine.
- **Config coupling:** some config loaders import strategy implementations; treat this as existing coupling to be
  reduced over time (prefer registering strategies via explicit wiring).
- **Oversized module decomposition:** before splitting large strategy,
  execution, monitoring, backtesting, or risk modules, follow the
  modular-refactoring discipline in
  [Development Guidelines](../DEVELOPMENT_GUIDELINES.md#architectural-principles) —
  keep the public class as a facade, extract one low-risk seam at a time, and
  gate on behavior tests for the moved responsibility rather than line counts.

## Where to add new code (quick decision guide)

- New strategy logic or a new strategy implementation → `src/gpt_trader/features/live_trade/strategies/`
- New guard/retry/telemetry behavior for placing orders → `src/gpt_trader/features/live_trade/execution/`
- New broker integration (live/paper/mock) → `src/gpt_trader/features/brokerages/`
- New data ingestion / market data adapter → `src/gpt_trader/features/data/` or brokerages market-data service
- New config fields → `src/gpt_trader/app/config/` (+ update schemas/agent artifacts as required)
