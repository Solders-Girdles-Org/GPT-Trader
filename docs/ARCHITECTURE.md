# System Architecture

## ⚠️ Architecture Transition (Complete)

**Important Note for Developers and Agents:**

This project completed a major architectural migration from a legacy "Orchestration" pattern (monolithic builders) to a modern **Dependency Injection** pattern using `ApplicationContainer`.

- **Legacy Core:** removed during the DI migration (no longer present)
- **Modern Core:** `src/gpt_trader/app/` (Composition Root) and `src/gpt_trader/features/` (Vertical Slices)
- **Migration status:** complete (legacy orchestration removed; DI container is canonical)

While this document describes the **current architecture**, you may still encounter legacy references in documentation. Always prefer the patterns defined in `src/gpt_trader/app` and `src/gpt_trader/features`.

---
status: current
---

## Current State

GPT-Trader is a Coinbase Advanced Trade oriented trading system with implemented **spot** and **CFM futures** paths. INTX perpetuals were removed (see [decision record](decisions/intx-default-derivatives-venue.md) and [Deprecations](DEPRECATIONS.md)); CFM/`us_futures` is the only supported derivatives venue. Treat spot and CFM as implementation capabilities, not as blanket approval for live automation.

For broader migration, the canonical planning gate is [Direction](DIRECTION.md). New AI-assisted execution work should start from the current approval-gated execution phase (`human_approved_execution` compatibility label), broker-neutral trade records, explicit risk budgets, `decision-needed` packets for unresolved live-control choices, and verified venue/API/account capability.

> 📘 **Trust reminder:** Confirm key details against current source + generated inventories (`var/agents/**`) before acting on them.

## Trading Capabilities Matrix

| Environment | Products | Authentication | API Version | WebSocket | Use Case |
|------------|----------|----------------|-------------|-----------|----------|
| **Spot** | Spot (BTC-USD, ETH-USD, ...) | JWT (CDP key) | Advanced v3 | Real-time | Implemented; requires profile/readiness gate before live use |
| **CFM** | US Futures (BTC, ETH, SOL, etc.) | CDP (JWT) | Advanced v3 | Real-time | Implemented/gated; verify account and risk constraints |
| **INTX** | Removed (was: perpetuals, international) | — | — | — | Removed; see [decision record](decisions/intx-default-derivatives-venue.md). `COINBASE_ENABLE_INTX_PERPS` is a deprecated alias (a truthy value warns and substitutes for `CFM_ENABLED=1` only; `TRADING_MODES` must still include `cfm`; falsey/unset values are ignored) ([Deprecations](DEPRECATIONS.md)) |
| **Paper** | All products | — | — | — | Simulated via `PERPS_PAPER=1` |

### Derivatives Access Summary
- **CFM (Coinbase Financial Markets)**: US-regulated futures with expiration dates. Endpoints: `cfm_balance_summary`, `cfm_positions`, `cfm_sweeps`, margin settings. The only supported derivatives venue (`coinbase_derivatives_type=us_futures`).
- **INTX (International Exchange)**: Removed. Selecting `intx_perps`/`perpetuals` is a validation error; see [decision record](decisions/intx-default-derivatives-venue.md) and [Deprecations](DEPRECATIONS.md).

## Component Architecture

### Vertical Slice Design

The system is organized into vertical feature slices under `src/gpt_trader/features/`. Production-critical slices (e.g., `live_trade`, `brokerages`, `intelligence`) ship with full test coverage.

```
src/gpt_trader/features/
├── brokerages/          # Exchange integrations
├── data/                # Data acquisition helpers
├── idea_execution/      # Paper-only execution lane for APPROVED trade ideas
├── intelligence/        # Strategy intelligence (sizing, regime)
│   └── sizing/          # Kelly criterion position sizing
├── live_trade/          # Production trading engine
├── optimize/            # Parameter optimisation experiments
├── recorder/            # Market-data recording (price ticks; five-role recorder arm)
├── strategy_dev/        # Strategy config profiles, registry, and config-diff helpers
├── strategy_tools/      # Shared helpers for strategy slices
└── trade_ideas/         # Broker-neutral trade-idea records, workflow, audit log
```

Additional cross-cutting packages now live at the top level. These intentionally span feature slices:

```
src/gpt_trader/
├── backtesting/         # Canonical backtesting framework
├── config/              # Path registry, constants, and shared configuration
├── core/                # Core domain primitives (account, market, trading types)
├── errors/              # Centralized error hierarchy providing consistent exception types
├── logging/             # Structured logging setup, JSON formatter, correlation context
├── monitoring/          # Runtime guards, configuration guardian, system logger, tracing
├── persistence/         # Event/order stores and persistence utilities
├── preflight/           # Production preflight verification and startup checks
├── security/            # Security primitives: input sanitization, secrets management
├── utilities/           # Shared helpers (async, datetime, quantization, logging facade)
└── validation/          # Declarative validators and decorators
```

Import boundaries are enforced in CI (Test Guardrails, required) by
`scripts/ci/check_import_boundaries.py`, which checks three rule families:
lower layers must never import the entrypoint layers (CLI/preflight) or the
DI container; `gpt_trader.monitoring` must not import feature slices at
runtime (TYPE_CHECKING-only imports allowed; the existing debt edges are
frozen in an allowlist); and slice-to-slice imports inside
`gpt_trader.features` — including the frozen dependency set of
`features/trade_ideas` (core + errors only) — are ratcheted to an explicit
allowlist (`CROSS_SLICE_ALLOWED_EDGES`) that records today's actual topology.
New edges require an architecture rationale; shrinking the allowlists is
always welcome.

> **Note:** `src/gpt_trader/backtesting/` is the canonical backtesting framework.

### High-Level Flow

```
CLI (gpt-trader) → Config (BotConfig) → ApplicationContainer → TradingBot →
Risk Guards → Coinbase Brokerage Adapter → Metrics + Telemetry
```

### Capability Map

For practical “where do I change X?” guidance, see `docs/DEVELOPMENT_GUIDELINES.md`.
Key capabilities documented:
- Configuration + Feature Flags (with [FEATURE_FLAGS.md](FEATURE_FLAGS.md) reference)
- Trading Decisioning, Pre-trade Validation, Order Execution
- Runtime Guards vs Pre-trade Guards
- Risk & Degradation, Streaming, Observability

### Entry Point & Service Wiring

- `uv run gpt-trader` invokes `gpt_trader.cli:main`, producing a `BotConfig` from
  CLI arguments and environment overrides.
- `ApplicationContainer` (`gpt_trader/app/container.py`) is the **canonical composition root**.
  It lazily initializes all services (broker, risk manager, event store, etc.) and wires
  them into `TradingBot` via `container.create_bot()`.
- `TradingBot` receives services directly from the container—no intermediate registry.

> **Note:** The legacy `gpt_trader/orchestration/` package was removed during the DI migration.
> Use `gpt_trader/app/bootstrap.py` and `ApplicationContainer` for all new code.

#### Container Sub-Containers

`ApplicationContainer` delegates to specialized sub-containers:

| Sub-Container | Services |
|---------------|----------|
| `ConfigContainer` | Config controller, profile loader |
| `ObservabilityContainer` | Notifications, health state, secrets manager |
| `PersistenceContainer` | Event store, orders store, runtime paths |
| `BrokerageContainer` | Broker, market data service, product catalog |
| `RiskValidationContainer` | Risk manager, validation failure tracker |

#### Container Requirement (Testing)

Service locators such as `get_failure_tracker()` always require an application
container. Tests should set up `ApplicationContainer` via fixtures or explicit
registration before calling container-resolved helpers.

#### Removed Modules (DI migration)

The `gpt_trader.orchestration` package was removed during the DI migration. Use the canonical paths:

| Removed Path | Canonical Path |
|--------------|----------------|
| `gpt_trader.orchestration.execution.degradation` | `gpt_trader.features.live_trade.degradation` |
| `gpt_trader.orchestration.configuration.risk.model` | `gpt_trader.features.live_trade.risk.config` |
| `gpt_trader.orchestration.configuration.bot_config` | `gpt_trader.app.config` |

`LiveExecutionEngine` was removed in v4.0. Use the TradingEngine guard stack (`_validate_and_place_order` in the live loop; `submit_order` for external callers).

**Order Execution:** Use the TradingEngine guard stack (`_validate_and_place_order` in the live loop; `submit_order` for external callers).

> **Historical deprecation tracker:** See [DEPRECATIONS.md](DEPRECATIONS.md) for migration history.

### Core Subsystems

| Module | Purpose |
|--------|---------|
| `gpt_trader/features/live_trade` | Main control loop, position tracking, and order routing |
| `gpt_trader/features/live_trade/risk/` | Risk management subpackage: position sizing, pre-trade validation, runtime monitoring, state management |
| `gpt_trader/features/live_trade/execution/` | Execution subpackage: guards, validation, order submission, state collection |
| `gpt_trader/app/` | Application bootstrap, DI container, and configuration management |
| `gpt_trader/features/brokerages/coinbase` | REST/WS integration for Coinbase Advanced Trade spot markets |
| `gpt_trader/features/brokerages/coinbase/client/` | Modular client package with mixins (accounts, orders, portfolio, market data) |
| `gpt_trader/features/brokerages/coinbase/rest/` | REST service layer: orders, portfolio, products, P&L calculation |
| `gpt_trader/features/intelligence/sizing` | Kelly-style sizing with regime awareness |
| `gpt_trader/monitoring` | Runtime guard orchestration, alert dispatch, system metrics |
| `gpt_trader/validation` | Predicate-based validators and input decorators |

#### Engine Context Pattern (Current)

Live trading uses a simplified engine pattern centered on `CoordinatorContext`.

**Key components**

- `CoordinatorContext` (`src/gpt_trader/features/live_trade/engines/base.py`) - snapshot of broker, risk, stores, and notifications
- `TradingEngine` (`src/gpt_trader/features/live_trade/engines/strategy.py`) - main trading loop and guard stack
- `TradingEngine._process_symbol` + `Strategy.decide` (`src/gpt_trader/features/live_trade/engines/strategy.py`, `src/gpt_trader/features/live_trade/strategies/base.py`) - per-symbol decision path
- Telemetry helpers (`src/gpt_trader/features/live_trade/engines/telemetry_health.py`, `telemetry_streaming.py`) - WS health and streaming
- Runtime coordinator (`src/gpt_trader/features/live_trade/engines/runtime/coordinator.py`) - owns explicit lifecycle sequencing for startup, shutdown, task cleanup, health/heartbeat orchestration points, policy checkpoints, stop conditions, and error escalation

**Lifecycle flow**

1. `TradingBot` builds a `CoordinatorContext` and instantiates `TradingEngine`.
2. `TradingEngine.start_background_tasks()` builds a `RuntimeLifecyclePlan` and delegates startup to `RuntimeEngine`.
3. `RuntimeEngine` validates required context/dependencies, checks stop conditions, runs startup hooks, starts tracked background tasks, reaches the runtime guard/policy checkpoint, and starts health, heartbeat, status, maintenance, streaming, and watchdog orchestration points.
4. `TradingEngine.shutdown()` delegates shutdown to `RuntimeEngine`, which runs bounded shutdown hooks, cancels tracked tasks, records graceful-shutdown failure diagnostics when cleanup times out, and only then lets `TradingEngine` move to a stopped or error state.

`RuntimeEngine` is a lifecycle boundary only. Strategy decisions still flow through `TradingEngine._process_symbol()` and `Strategy.decide()`, and order behavior remains inside the existing guard stack (`_validate_and_place_order()` -> `OrderSubmitter` -> `BrokerExecutor`).

Legacy orchestration facades were removed during the DI migration. Use `features/live_trade/` and `app/` paths.

#### Coinbase Client Package

The previous monolithic `client.py` was replaced with a composable package (`client/__init__.py`
plus mixins). Each mixin owns a REST surface (accounts, orders, market data, portfolio), while the
base class centralises retry, throttling, and auth wiring. Scripts and slices now import through
`gpt_trader.features.brokerages.coinbase.client import CoinbaseClient` to ensure consistent
initialisation.

A parallel REST service layer (`rest/`) provides higher-level operations:
- `rest/base.py` - Base REST service with shared utilities
- `rest/orders.py` - Order management operations
- `rest/portfolio.py` - Portfolio operations and conversions
- `rest/products.py` - Product catalog and specifications
- `rest/pnl.py` - P&L calculation and reconciliation

This two-layer design separates low-level HTTP concerns (client/) from business logic (rest/).

#### Risk Management Framework

The `features/live_trade/risk/` package defines the live risk manager and config:

- `risk/config.py` - `RiskConfig` dataclass for risk limits and thresholds (env-driven).
- `risk/manager/__init__.py` - `LiveRiskManager` handling leverage, daily loss, exposure, reduce-only.
- `risk/protocols.py` - protocol interfaces for DI and testing.

Runtime guards are orchestrated by `features/live_trade/execution/guard_manager.py` with
guard implementations under `features/live_trade/execution/guards/`. Pre-trade
validation lives in `features/live_trade/execution/validation.py` and
`security/security_validator.py`.

#### Monitoring & Validation Framework

- **Validators** (`gpt_trader/validation`): the base `Validator` now accepts inline predicates and
  optional value coercion, enabling concise one-off validations while keeping legacy subclasses.
- **Runtime guards** (`gpt_trader/features/live_trade/execution/guard_manager.py`): coordinates
  per-cycle guard checks using implementations under `features/live_trade/execution/guards/`.
- **Guard alert dispatcher** (`gpt_trader/features/live_trade/guard_errors.py`): wraps the lightweight
  alert manager to emit guard failures without depending on the retired alert stack. Rebuild
  multi-channel routing as needed for email, Slack, or PagerDuty integrations.
- **Risk metrics aggregation** (`gpt_trader/features/live_trade/execution/guards/risk_metrics.py`):
  runtime guard snapshots feed into the monitoring stack for dashboards and analytics.

#### Strategy Decision Path

Strategy decisions are evaluated inside `TradingEngine._process_symbol()` using the
strategy’s `decide()` implementation with recent marks and optional candle context.

#### Signal Ensemble Architecture

The system now uses a **Signal Ensemble** approach to combine multiple trading signals.

- **Components**:
    - `Signal`: Individual trading signal (e.g., RSI, MACD).
    - `Ensemble`: Collection of signals with weights.
    - `Voter`: Logic to combine signal outputs into a final decision.
- **Location**: `src/gpt_trader/features/live_trade/strategies/ensemble.py`
- **Status**: Implemented and wired via the strategy factory + `TradingEngine`.

### Derivatives Capability Gates

These are capability gates over implementation surfaces; they are not approval to
trade derivatives. A live derivatives run still requires venue/account
verification and the gates in [Live Operations](production.md).

**CFM Futures** (US-regulated): Adapter available for approved US futures accounts. Enable via `TRADING_MODES=cfm` (or `spot,cfm`) and `CFM_ENABLED=1`; uses CDP authentication and CFM endpoints (`cfm_balance_summary`, `cfm_positions`, etc.). This is the only supported derivatives venue.

**INTX Perpetuals**: Removed; see [decision record](decisions/intx-default-derivatives-venue.md) and [Deprecations](DEPRECATIONS.md). `COINBASE_ENABLE_INTX_PERPS` is retained only as a deprecated alias: a truthy value warns and substitutes for `CFM_ENABLED=1` only (config validation still requires `TRADING_MODES` to include `cfm`); falsey/unset values are ignored. `-PERP`-suffixed symbols are coerced to their spot equivalents.

### Feature Slice Reference


#### Position Sizing (`features/intelligence/sizing/`)
- **Purpose:** Regime-aware position sizing combining Kelly Criterion, ATR-based volatility scaling, and risk budgeting.
- **Usage:**
    ```python
    from gpt_trader.features.intelligence.sizing import (
        PositionSizer,
        PositionSizingConfig,
        SizingResult,
    )

    config = PositionSizingConfig(...)
    sizer = PositionSizer(config)
    result: SizingResult = sizer.calculate_size(...)
    ```
- **Integration hooks:** Built to ingest market-regime detectors from `intelligence/regime/` without cross-slice imports.

### Key Design Principles

1. **Slice Isolation**: Production slices limit cross-dependencies; experimental ones stay sandboxed.
2. **Token Awareness**: Documentation highlights slice entry points so agents can load only what they need.
3. **Type Safety**: Shared interfaces defined in `features/brokerages/core/protocols.py`.
4. **Environment Separation**: GPT-Trader normalizes symbols to spot by default; retired `-PERP`-suffixed (INTX) symbols are coerced to their spot equivalents (see [Deprecations](DEPRECATIONS.md)).

### Order Execution Pipeline

The order execution pipeline ensures reliable order submission with proper ID tracking, telemetry, and error classification.

**Flow Diagram**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TradingEngine._cycle()                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TradingEngine._validate_and_place_order()                │
│                                                                             │
│  1. Degradation gate (pause + reduce-only allowance)                        │
│  2. Position sizing                                                         │
│  3. Reduce-only request gate (reject if no position)                        │
│  4. Security validation (hard limits)                                       │
│  5. Reduce-only mode clamp + check                                          │
│  6. Mark staleness gate (allow reduce-only if configured)                   │
│  7. OrderValidator: exchange rules → pre-trade validation                   │
│     slippage guard → order preview                                          │
│  8. Delegates to OrderSubmitter                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       OrderSubmitter.submit_order()                         │
│                                                                             │
│  • Generates stable client_order_id (or uses provided)                      │
│  • Sets correlation context for tracing                                     │
│  • Delegates to BrokerExecutor                                              │
│  • Tracks order in open_orders list                                         │
│  • Records telemetry (success/rejection with classified reason)             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     BrokerExecutor._execute_broker_order()                  │
│                                                                             │
│  • Calls broker.place_order(client_id=submit_id, ...)                       │
│  • Passes client_order_id to broker for idempotency                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Broker (Coinbase/Mock)                             │
│                                                                             │
│  • Executes order via REST API                                              │
│  • Returns Order object with broker-assigned order_id                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Implementation Details**

| Component | Location | Responsibility |
|-----------|----------|----------------|
| `TradingEngine` | `features/live_trade/engines/strategy.py` | Guard stack, validation, position tracking |
| `OrderSubmitter` | `features/live_trade/execution/order_submission.py` | ID generation, telemetry, open order tracking |
| `BrokerExecutor` | `features/live_trade/execution/broker_executor.py` | Broker API abstraction |
| `OrderEventRecorder` | `features/live_trade/execution/order_event_recorder.py` | Event store persistence |

**client_order_id Flow**

1. **Generation**: `OrderSubmitter._generate_submit_id()` creates `{bot_id}_{uuid}` format
2. **Propagation**: Passed to `broker.place_order(client_id=...)` for broker-side idempotency
3. **Tracking**: Used in logs, metrics, and event store for end-to-end tracing
4. **Retry Safety**: Callers can pass explicit `client_order_id` to ensure retries don't create duplicates

**Error Classification**

Rejection reasons are normalized via `normalize_rejection_reason()` to stable codes
(see `docs/OBSERVABILITY.md` for the canonical list). These codes feed into metrics
labels and event payloads for consistent telemetry.

### Live Trade Infrastructure

The live trade layer provides coordinated control across trading operations through modules in `features/live_trade/` and `app/`.

**Configuration & Symbol Management:**
- `app/config/` - Profile-aware configuration (`BotConfig`, profiles, validation)
- `app/config/controller.py` - Dynamic configuration management and hot-reloading
- `features/live_trade/symbols.py` - Symbol normalization, derivatives gating, and defaults

**Execution Coordination:**
- `features/live_trade/engines/strategy.py` - Main trading loop and guard stack
- `features/live_trade/execution/` subpackage:
  - `guard_manager.py` - Runtime guard management
  - `validation.py` - Pre-trade validation
  - `order_submission.py` - Order submission and recording
  - `state_collection.py` - Account state collection
  - `broker_executor.py` - Broker execution abstraction

**Strategy & Runtime Coordination:**
- `features/live_trade/strategies/` - Strategy implementations and shared protocols
- `features/live_trade/engines/strategy.py` - Symbol processing, decisioning, and order routing

**Services & Telemetry:**
- `features/live_trade/telemetry/account.py` - Account metrics tracking and periodic snapshots
- `src/gpt_trader/monitoring/system/metrics.py` - System metrics collection
- `src/gpt_trader/monitoring/system/positions.py` - Position monitoring

**Infrastructure:**
- `features/live_trade/bot.py` - TradingBot facade coordinating all components
- `features/brokerages/mock/deterministic.py` - Deterministic broker for testing
- `features/brokerages/paper/hybrid.py` - Paper trading broker implementation
- `app/runtime/paths.py` - Runtime path resolution
- `app/bootstrap.py` - Bot creation helpers (`build_bot`, `bot_from_profile`)

### Runtime Profile Registry

`src/gpt_trader/app/config/profile_loader.py` hoists the runtime profile registry that ties every `Profile` enum to:

- a shared `ProfileSchema` that seeds both runtime and preflight configuration flows,
- env-var defaults/operators expectations (`env_defaults`) so downstream automation can validate staging vs. production behavior, and
- metadata such as `preflight_supported` and `preflight_default` so readiness tooling knows which profile to hydrate.

`ProfileLoader` and `build_profile_config` are the concrete entrypoints maintainers should follow when touching profile loading or registry responsibilities.

Supported profile sources:

- `config/profiles/<profile>.yaml` (YAML-first loading via `ProfileLoader.load`)
- `_PROFILE_DEFAULTS` hardcoded fallbacks in the same module when a profile YAML file is missing
- CLI overrides baked into `src/gpt_trader/cli/services.py` via `build_config_from_args()` (e.g., `--profile`, `--dry-run`, `--symbols`, `--config`)
- Environment variables surfaced by `BotConfig.from_env()` (e.g., `DRY_RUN`, `RISK_*`, `TRADING_SYMBOLS`, `MOCK_BROKER`)
- Runtime artifacts under `runtime_data/<profile>/…` that the readiness gate consumes (see [Readiness pillars](READINESS.md#readiness-pillars-must-have))

Override order followed when building a `BotConfig`:

| Priority | Source | Mechanism |
| --- | --- | --- |
| 1 | CLI arguments | `build_config_from_args()` overrides everything else with flags such as `--dry-run`, `--symbols`, and `--time-in-force` after a profile/config file loads. |
| 2 | Config file (`--config`) | Loading `--config path/to.yaml` short-circuits profile lookup and shapes the base config before CLI tweaks. |
| 3 | Profile loader | `ProfileLoader.load()` reads `config/profiles/<profile>.yaml` (YAML first) or `_PROFILE_DEFAULTS`, then funnels the schema through `build_profile_config()`. |
| 4 | Environment variables | `BotConfig.from_env()` seeds fields such as `DRY_RUN`, `RISK_*`, `STATUS_INTERVAL`, and `MOCK_BROKER` before any profile materializes. |
| 5 | Built-in defaults | The dataclasses inside `ProfileSchema`/`ProfileRegistryEntry` ensure deterministic values when no other source supplies a field. |

The readiness gate and CLI preflight scripts consume the same registry so that `PREFLIGHT_PROFILE`, `READINESS_REPORT_DIR`, and `runtime_data/<profile>` artifacts stay aligned with whatever the CLI is currently driving.

### Runtime Artifact Ownership

Default runtime path ownership lives in `src/gpt_trader/config/path_registry.py`.
Use that module before adding new filesystem defaults.

- Repo-local runtime artifacts belong under `runtime_data/`. Profile-specific
  data uses `runtime_data/<profile>/...`; optimization run results use
  `runtime_data/optimize/...` through `path_registry.OPTIMIZATION_RUNS_DIR`.
- Repo-local generated or developer artifacts belong under `var/`, including
  logs, status files, coverage output, and generated `var/agents/**` references.
- User-global secret material is the explicit exception. Encrypted file fallback
  secrets remain under `~/.gpt_trader/secrets` through
  `path_registry.USER_SECRETS_DIR`; do not mix secrets into repo-local runtime
  directories unless a migration plan covers permissions and compatibility.
- Tests and temporary tooling should inject paths such as
  `OptimizationStorage(base_dir=...)` or `SecretsManager(secrets_dir=...)`
  instead of relying on the real user home.

## Implementation Status

This section describes which surfaces are implemented in the codebase.
Implemented does not mean approved to run live — live execution still follows
the gates in [Live Operations](production.md) and the
[Direction](DIRECTION.md).

### ✅ Implemented Surfaces
- Coinbase spot adapters via Advanced Trade (REST/WebSocket); dev profile defaults to the deterministic broker stub and can be pointed at live APIs with `SPOT_FORCE_LIVE=1`
- Order placement/management through the `TradingEngine` guard stack (`_validate_and_place_order` -> `OrderSubmitter` -> `BrokerExecutor`)
- Account telemetry snapshots and cycle metrics persisted for monitoring
- Runtime safety rails: daily loss guard, liquidation buffer enforcement, mark staleness detection, volatility circuit breaker, correlation checks
- Active test suite (`uv run pytest --collect-only` to verify)

### ⚠️ Partially Implemented / Capability-Gated
- CFM futures adapter: code paths compile and tests run; account access and approval are required before live execution. INTX perpetuals were removed (see [decision record](decisions/intx-default-derivatives-venue.md)); CFM/`us_futures` is the only derivatives venue
- Advanced WebSocket user-event handling: baseline support exists; enrichment/backfill still in progress
- Durable restart state (OrdersStore/EventStore) needs hardening before live reliance

### ❌ Not Yet Implemented
- Funding rate accrual in deterministic broker stub
- Order modification/amend flows beyond cancel
- Partial fill handling in mock (market fills remain immediate)

## Trading Profiles

The snippets below are illustrative config snapshots, not approval. Live
profiles (`canary`, `prod`) only run after the gates in
[Live Operations](production.md) are satisfied with a recorded human approval
decision. An approved runbook may scope the command lane and checks, but it
does not replace the approval event required by the current runtime policy.

### Development Profile
```yaml
profile: dev
broker: mock
positions: tiny (0.001 BTC)
logging: verbose
fills: deterministic
universe: [BTC, ETH, SOL, XRP, LTC, ADA, DOGE, BCH, AVAX, LINK]
```

### Canary Profile
```yaml
profile: canary
broker: coinbase
positions: 0.01 BTC max
daily_loss_limit_pct: 1%
trading_window: 14:00-15:00 UTC
circuit_breakers: multiple
```

### Production Profile
```yaml
profile: prod
broker: coinbase
positions: full sizing
risk_limits: production
monitoring: real-time
```

## Risk Management

### Pre-Trade Validation
- Position size limits
- Margin requirement checks
- Impact cost validation (<50bps)
- Reduce-only mode enforcement

> Leverage caps are configured via `RiskConfig.max_leverage` and optional day/night per-symbol caps
> (`RiskConfig.day_leverage_max_per_symbol`, `RiskConfig.night_leverage_max_per_symbol`).

### Runtime Guards
- Daily loss limits
- Error rate monitoring (>50% triggers shutdown)
- Stale data detection (30s timeout)
- Drawdown protection (10% max)
- Generic guard evaluation with configurable comparison operators (`gt`, `lt`, `abs_gt`, etc.),
  warning thresholds, and contextual messaging. Guard instrumentation raises structured
  `RiskGuard*Error` exceptions: recoverable failures emit `risk.guards.<name>.recoverable_failures`
  counters and log warnings, while critical failures escalate to reduce-only mode and emit
  `risk.guards.<name>.critical_failures`.

### Circuit Breakers
- Consecutive loss protection
- Volatility spike detection
- Liquidity monitoring
- API error thresholds

### Resilience & Retry Policy

The execution pipeline uses a configurable `RetryPolicy` for transient failures:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_attempts` | 3 | Maximum broker call attempts |
| `base_delay` | 0.5s | Initial backoff delay |
| `max_delay` | 30.0s | Maximum backoff delay |
| `exponential_base` | 2.0 | Backoff multiplier |

**Configuration:**
- Code: `BrokerExecutor(retry_policy=RetryPolicy(...))`
- Location: `features/live_trade/execution/broker_executor.py`

**Retry Classification:**
- **Retryable**: `TimeoutError`, `ConnectionError`, rate limits (429)
- **Non-retryable**: `ValueError` (rejections), `InvalidOrder`, insufficient funds

The `client_order_id` is preserved across retries for broker-side idempotency.

### Degradation Lifecycle

The `DegradationState` manages trading restrictions with monotonic progression:

```
NORMAL → REDUCE_ONLY → PAUSED → HALTED
```

**Pause Types:**

| Method | Scope | Use Case |
|--------|-------|----------|
| `pause_all(seconds)` | Global | System-wide issues |
| `pause_symbol(symbol, seconds)` | Per-symbol | Symbol-specific problems |

**Key Behaviors:**
- Pauses are time-bounded and auto-expire
- `allow_reduce_only=True` permits position-closing orders during pause
- Symbol pauses expire independently
- Guard failures can trigger automatic pause via `record_broker_failure()`

**Location:** `features/live_trade/degradation.py`

## Performance & Observability

- **Cycle Metrics**: persisted to `runtime_data/<profile>/metrics.json` and `runtime_data/<profile>/events.db`,
  exposed via the Prometheus exporter (`scripts/monitoring/export_metrics.py`) which reads events.db first,
  falling back to metrics.json/events.jsonl when needed. The live risk manager emits snapshot events
  consumed by dashboards and the monitoring stack.
- **System Monitoring**: `src/gpt_trader/monitoring/system/` provides resource telemetry collectors used by
  the runtime guard manager and dashboards.
- **System Footprint**: bot process typically <50 MB RSS with sub-100 ms WebSocket latency in spot
  mode.
- **Test Discovery**: `uv run pytest --collect-only`

## Verification Path

1. **Regression Suite**: `uv run pytest -q`
2. **Smoke Test**: `uv run gpt-trader run --profile dev --dev-fast`
3. **Validation**: `uv run python scripts/production_preflight.py --profile dev --warn-only`

## Dependencies

### Core Requirements
- Python 3.12+
- uv (package manager)
- Coinbase Advanced Trade API (for perpetuals)
- CDP API keys (for production)

### Key Libraries
- `requests` + `pyjwt` - Coinbase REST client + ES256 JWT auth
- `pandas` - Data manipulation
- `websocket-client` - Real-time data (optional; live-trade extra)
- `aiohttp` - Webhook notifications + async utilities (optional; live-trade extra)
- `pydantic` - Data validation

## Roadmap

Roadmap tracking lives in GitHub Issues/PRs. Historical planning notes live in git history.
