# Deprecation Tracker

---
status: current
---

This document tracks deprecated modules, shims, and their planned removal dates.

## Active Deprecations

All active deprecations must be listed here with an owner and a removal date.
CI enforces this registry for new deprecation shims.

| Deprecated | Replacement | Location | Owner | Removal Date | Notes |
|------------|-------------|----------|-------|--------------|-------|
| `COINBASE_ENABLE_INTX_PERPS` env var | `CFM_ENABLED` / `TRADING_MODES` | `src/gpt_trader/app/config/bot_config.py` | Core Config | v4.0 | INTX perpetuals removed. Still honored as an alias for enabling CFM derivatives, emitting a `DeprecationWarning`. Also read by preflight INTX checks, which are removed in the INTX Tier-2 prune. See `docs/decisions/intx-default-derivatives-venue.md`. |

## Legacy Inventory (Status)

Classification of legacy shims/fallbacks and compatibility keepers.

| Item | Location | Owner | Status | Notes |
|------|----------|-------|--------|-------|
| `BotConfig.from_dict` legacy profile-style YAML mapping | `src/gpt_trader/app/config/bot_config.py` | Core Config | deprecate | Emits `DeprecationWarning`; target removal in v4.0. |

### Configuration (Remove after v4.0)

No active configuration deprecations.

### Legacy Backward Compatibility (Keep indefinitely)

These are kept for external consumer compatibility and are not scheduled for removal:

| Item | Location | Owner | Purpose |
|------|----------|-------|---------|
| Legacy order payload builders | `src/gpt_trader/features/brokerages/coinbase/rest/base.py` | Brokerage | `_build_order_payload()`, `_execute_order_payload()` |

## General Removal Checklist

Before removing any deprecated item:

1. Search for internal usage: `grep -r "deprecated_name" src/ tests/`
2. Check dynamic imports: Review `importlib` and entry points
3. Update this registry with owner + removal date
4. Update docs that reference the deprecated path
5. Record the migration in the **Recently Removed** table below (git history and
   GitHub releases carry the version-level narrative)

## Recently Removed

| Item | Removed In | Migration Path |
|------|------------|----------------|
| `agent-risk` CLI (`scripts/agents/query_risk_config.py`) | Unreleased | Removed as a dead agent tool (owner-approved 2026-07-01). Read risk configuration via `var/agents/schemas/risk_config_schema.json` (`uv run agent-regenerate --only schemas`) or `gpt-trader` config commands. |
| `agent-dedupe-triage` CLI (`scripts/agents/dedupe_triage.py`) | Unreleased | Removed as a dead agent tool (owner-approved 2026-07-01). Edit `tests/_triage/dedupe_triage.yaml` directly; it remains the hand-editable triage record consumed by `uv run agent-dedupe`. |
| Hybrid strategy framework (`features/live_trade/strategies/hybrid/`: `HybridStrategyBase`, `HybridStrategyConfig`, `HybridDecision`, hybrid `Action` enum) | Unreleased | Dead parallel surface — never reachable from `factory.py`/config. Use the strategy registry in `features/live_trade/factory.py`. |
| `OrderRouter` / `OrderResult` (`features/live_trade/execution/router.py`) | Unreleased | Never instantiated in production. External submissions go through `TradingEngine.submit_order()`. |
| `monitoring/guards/` package (`RuntimeGuardManager`, builtin guards, alert handlers) | Unreleased | Dead parallel of the canonical runtime guard stack in `features/live_trade/execution/guards/` (managed by `execution/guard_manager.py`). |
| `monitoring/domain/perps/` margin/liquidation math (`MarginStateMonitor`, `LiquidationMonitor`) | Unreleased | Test-only surface. Live liquidation protection is `features/live_trade/execution/guards/liquidation_buffer.py`. |
| `StatefulStrategy` ABC (`strategies/stateful.py`) | Unreleased | Test-only. Use `StatefulStrategyBase` in `strategies/base.py`. |
| `StatefulBaselineStrategy` / `StatefulPerpsStrategy` (`strategies/baseline/stateful.py`) | Unreleased | Test-only; never wired into `factory.py`. Use `BaselinePerpsStrategy`. |
| `AsyncRetry` / `async_retry` (`utilities/async_tools/retry.py`) | Unreleased | Zero non-test users. Broker IO retry lives in `execution/broker_executor.py` (`RetryPolicy`, `execute_with_retry`). |
| `retry_on_error` decorator (`errors/error_patterns.py`) | Unreleased | Zero production callers. Call `ErrorHandler.with_retry` directly if retry-with-recovery is needed. |
| Legacy credential env vars (`COINBASE_API_KEY_NAME` / `COINBASE_PRIVATE_KEY`) | Unreleased | Use `COINBASE_CDP_API_KEY` + `COINBASE_CDP_PRIVATE_KEY` or `COINBASE_CREDENTIALS_FILE`. |
| `get_auth()` env-based auth factory | Unreleased | Use `resolve_coinbase_credentials()` + `SimpleAuth`. |
| `BotConfig` flat compat accessors (`short_ma`, `long_ma`, etc.) | Unreleased | Use nested config: `config.strategy.*` and `config.risk.*`. |
| `gpt_trader.logging.orchestration_helpers` | Unreleased | Use `gpt_trader.logging.runtime_helpers`. |
| `StatusReporter.update_interval` + `StatusReporter.get_status_dict()` | Unreleased | Use `file_write_interval` + `get_status()`. |
| `get_failure_tracker()` fallback without container | Unreleased | Set application container; use `container.validation_failure_tracker`. |
| `Alert.id` / `Alert.timestamp` aliases | Unreleased | Use `alert_id` / `created_at`. |
| `CoinbaseRestServiceBase` alias | Unreleased | Use `CoinbaseRestServiceCore`. |
| `daily_loss_limit` in profile schema | Unreleased | Use `daily_loss_limit_pct`. |
| `RiskConfig.daily_loss_limit` (absolute dollars) | Unreleased | Use `RiskConfig.daily_loss_limit_pct` / `RISK_DAILY_LOSS_LIMIT_PCT`. |
| Perps strategy compat aliases (`short_ma`/`long_ma` props, `StrategyConfig`) | Unreleased | Use `PerpsStrategyConfig` and `short_ma_period`/`long_ma_period`. |
| CLI fallback for unknown profile YAML | Unreleased | Use a `Profile` enum value or `--config`. |
| Legacy risk templates | Unreleased | Removed from tree; use git history for reference. |
| Module-level `health_state` | Unreleased | Use `ApplicationContainer.health_state` and pass `HealthState` explicitly to health helpers. |
| Module-level `secrets_manager` helpers | Unreleased | Use `ApplicationContainer.secrets_manager` or instantiate `SecretsManager`. |
| Data module singletons (`store_data`/`fetch_data` functions) | Unreleased | Use `DataService` and its instance methods. |
| Yahoo data source stub (`DataSource.YAHOO`, `download_from_yahoo`) | Unreleased | Use `DataSource.COINBASE` and `download_from_coinbase`. |
| `EventStore.events` list + `EventStore.path` JSONL alias | Unreleased | Use `EventStore.list_events()` for reads and `EventStore.root` for the storage root. |
| TUI subsystem (`src/gpt_trader/tui/`, `gpt-trader tui` command, `--tui`/`--demo` run flags, `scripts/build_tui_css.py`, TUI CI jobs, `textual` dependency) | Unreleased | Removed; use `gpt-trader run` for the bot and `gpt-trader ideas …` for trade-idea review. See `docs/decisions/remove-tui-subsystem.md`. |
| `gpt-trader treasury convert\|move` CLI command group (`cli/commands/treasury.py`) | Unreleased | Removed; it was never functional (no `account_manager` was ever wired, so every invocation failed). No migration path. See `docs/decisions/remove-unwired-account-manager-and-strategy-lab.md`. |
| `CoinbaseAccountManager` (`features/brokerages/coinbase/account_manager.py`) | Unreleased | Removed as never-constructed dead code. Wire a new account-snapshot service from a fresh spec if needed. See `docs/decisions/remove-unwired-account-manager-and-strategy-lab.md`. |
| `AccountTelemetryService` (`features/live_trade/telemetry/account.py`) + `AccountManagerProtocol` + `TradingBot.account_manager`/`account_telemetry` attributes | Unreleased | Removed as unreachable (container never provided the services). `gpt-trader account snapshot` still reports telemetry unavailable, unchanged. See `docs/decisions/remove-unwired-account-manager-and-strategy-lab.md`. |
| `features/strategy_dev/lab/` + `features/strategy_dev/monitor/` subpackages (`Experiment*`, `ParameterGrid`, `PerformanceMonitor`, `MetricsAggregator`, `AlertManager`, …) | Unreleased | Removed as unwired dead code; `strategy_dev/config/` (used by `gpt-trader strategy`) remains. See `docs/decisions/remove-unwired-account-manager-and-strategy-lab.md`. |
| TUI legacy preferences fallback (`config/tui_preferences.json`) | Unreleased | Use runtime preferences path or `GPT_TRADER_TUI_PREFERENCES_PATH` |
| TUI legacy status CSS aliases (`good`/`bad`/`risk-status-*`) | Unreleased | Use `status-ok`, `status-warning`, `status-critical` |
| TUI validation `ValidationError` alias | Unreleased | Use `FieldValidationError` |
| TUI legacy guard shapes (`active_guards`) | Unreleased | Use `RiskStatus.guards` and `StatusReporter.update_risk(guards=...)`. |
| `COINBASE_ENABLE_DERIVATIVES` env var alias | Unreleased | Use `CFM_ENABLED` / `TRADING_MODES` (was `COINBASE_ENABLE_INTX_PERPS`, now also deprecated). |
| INTX derivatives venue types (`coinbase_derivatives_type` = `intx_perps` / `perpetuals`) | Unreleased | Use `us_futures` (CFM). Validation now rejects the INTX values with a migration error. |
| INTX dead modules (`intx_portfolio_service`, `derivatives_discovery`, `derivatives_products`) | Unreleased | Removed as unimported dead code; rebuild CFM discovery cleanly if/when CFM activates. |
| `config/tui_preferences.json` file | Unreleased | Removed from tree (fallback logic already removed; see prior TUI legacy preferences row); no readers remained. |
| `scripts/ops/canary_process.py` TUI/demo token filter (`_is_tui_or_demo`) | Unreleased | Removed; `--tui`/`--demo` run flags no longer exist and the exact `--profile` match already excludes non-target profiles (e.g. `--profile demo`). |
| `scripts/analysis/perps_bot_hot_path_benchmark.py` | Unreleased | Removed; one-off before/after benchmark for a long-landed `TradingBot.update_marks` optimization. |
| Coinbase REST legacy position dict fallback | v4.0 | Require `PositionStateStore` injection |
| `PERPS_FORCE_MOCK` env var | v4.0 | Use `MOCK_BROKER` |
| `SYMBOLS` env var | v4.0 | Use `TRADING_SYMBOLS` |
| `POSITION_FRACTION` env var | v4.0 | Use `RISK_MAX_POSITION_PCT_PER_SYMBOL` |
| `OrderRouter.execute()` | v4.0 | `OrderRouter` has since been removed entirely; use `TradingEngine.submit_order()` |
| `gpt_trader.orchestration` package | DI migration | Use canonical paths: `app.*`, `features.live_trade.*` |
| `orchestration.configuration.bot_config` | DI migration | `gpt_trader.app.config.BotConfig` |
| `orchestration.configuration.risk.model` | DI migration | `gpt_trader.features.live_trade.risk.config.RiskConfig` |
| `features.live_trade.execution.LiveExecutionEngine` | v4.0 | TradingEngine guard stack (`_validate_and_place_order` live loop; `submit_order` external) |
| `orchestration.execution.*` | DI migration | `gpt_trader.features.live_trade.execution.*` |
| `orchestration.symbols` | DI migration | `gpt_trader.features.live_trade.symbols` |

### DI Migration Removal Summary (Phase 13)

The entire `src/gpt_trader/orchestration/` package was removed during the DI migration. Migration phases:

- **Phase 9-10**: Converted orchestration modules to pure re-export shims
- **Phase 11**: Moved tests to canonical locations, created deprecation test bucket
- **Phase 12**: Updated scripts/ci tooling, extended CI guards
- **Phase 13**: Hard removal of orchestration package and shim tests
- **Phase 14**: Post-removal cleanup (pytest marker, test hygiene allowlist, inventory generator)

---
