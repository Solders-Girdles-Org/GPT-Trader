# Risk Integration Guide (gpt_trader)

---
status: current
---

GPT-Trader layers risk controls across configuration, pre-trade validation,
runtime guards, and the `LiveRiskManager`. Treat these controls as readiness
gates for each product lane, not as blanket approval for live automation. This
guide highlights the active components and how they work together.

## Configuration Sources

- **Profiles + CLI/env overrides**: `BotConfig` is assembled in
  `gpt_trader/cli/services.py` from profile YAML, CLI flags, and env variables.
  The nested `risk` section is `BotRiskConfig` (position sizing, leverage, daily loss).
- **Risk manager model**: `RiskConfig` lives in
  `features/live_trade/risk/config.py`. `RiskValidationContainer` adapts
  `BotRiskConfig` + reduce-only mode into `RiskConfig` when constructing
  `LiveRiskManager`.
- **Preflight validation**: `preflight/checks/risk.py` validates
  `RiskConfig.from_env()` (env-only). Keep env and profile risk settings aligned
  for canary/prod.
- **Risk env vars**: `RiskConfig.from_env()` reads `RISK_*` and `CFM_*` env vars
  (see `config/environments/.env.template`).
- **RISK_CONFIG_PATH (reserved)**: Stored on `BotConfig` but not wired into the runtime loader.

## Execution Path

1. `gpt_trader/cli/commands/run.py` builds `BotConfig` via
   `gpt_trader.cli.services.build_config_from_args()`.
2. `ApplicationContainer` (`gpt_trader/app/container.py`) wires broker, risk
   manager, event store, and telemetry services.
3. `TradingBot` (`features/live_trade/bot.py`) builds a `CoordinatorContext` and
   instantiates `TradingEngine` (`features/live_trade/engines/strategy.py`).
4. Live loop orders go through `TradingEngine._validate_and_place_order()`.
   External callers use `TradingEngine.submit_order()`; both delegate to
   `OrderSubmitter` → `BrokerExecutor` → `broker.place_order()`.
5. Runtime guard sweeps are coordinated by
   `features/live_trade/execution/guard_manager.py` with guards in
   `features/live_trade/execution/guards/`.

## Guard Highlights (Runtime)

| Guard | Module | Purpose |
|-------|--------|---------|
| Daily loss | `execution/guards/daily_loss.py` | Halts trading when cumulative loss exceeds the configured limit. |
| Mark staleness | `execution/guards/mark_staleness.py` | Ensures mark prices are fresh before trading. |
| Liquidation buffer | `execution/guards/liquidation_buffer.py` | Enforces liquidation buffer thresholds. |
| Volatility | `execution/guards/volatility.py` | Blocks trading during volatility spikes. |
| API health | `execution/guards/api_health.py` | Trips on sustained API errors or rate-limit usage. |
| Risk telemetry | `execution/guards/risk_metrics.py` | Emits guard metrics into the telemetry pipeline. |

Guard failures raise `GuardError` / `RiskGuard*Error` from
`features/live_trade/guard_errors.py` and trigger degradation handlers.

Pre-trade validation lives in `features/live_trade/execution/validation.py`
(`OrderValidator`) and `security/security_validator.py` (`SecurityValidator`).

## Position Sizing & Exposure

- Strategy-level sizing uses `PerpsStrategyConfig.position_fraction` in
  `features/live_trade/strategies/baseline/strategy.py`.
- Bot-level risk sizing uses `BotRiskConfig.position_fraction` and is adapted to
  `RiskConfig.max_position_pct_per_symbol` in `app/containers/risk_validation.py`.
- Equity and exposure calculations for guards are handled by
  `features/live_trade/engines/equity_calculator.py`.
- Research/backtesting sizing helpers live in
  `features/intelligence/sizing/position_sizer.py`.

## Telemetry

- Guard and validation metrics flow through `src/gpt_trader/monitoring/metrics_collector.py` and
  `features/live_trade/execution/guards/risk_metrics.py`.
- Metrics snapshots are persisted to `runtime_data/<profile>/metrics.json`.
- EventStore persists to SQLite at `runtime_data/<profile>/events.db`.

## Extension Points

- Provide a custom risk manager by subclassing `ApplicationContainer` or
  swapping the `RiskValidationContainer` implementation.
- Call `container.reset_risk_manager()` before `create_bot()` to reload the risk
  manager after config changes.
- Add runtime guards under `features/live_trade/execution/guards/` and register
  them in `features/live_trade/execution/guard_manager.py`.
- Add pre-trade checks by extending `OrderValidator` in
  `features/live_trade/execution/validation.py`.

## Testing

- Runtime guard coverage: `tests/unit/gpt_trader/features/live_trade/execution/` (see `test_guard_manager_*.py` and `test_*_guard.py`).
- Guard error behavior: `tests/unit/gpt_trader/features/live_trade/test_guard_errors.py`.
- Preflight risk validation: `src/gpt_trader/preflight/checks/risk.py`.
