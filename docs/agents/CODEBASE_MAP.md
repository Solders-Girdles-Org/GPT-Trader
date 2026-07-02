# Codebase Map (Agent Quick Reference)

---
status: current
---

Use this as a “where do I start?” index when you already know what you’re trying to change.

## Quick Navigation

| Task | Start Here | Key Files / Notes |
|------|------------|-------------------|
| Run the bot (CLI) | `src/gpt_trader/cli/__init__.py` | Commands live in `src/gpt_trader/cli/commands/` (start with `run.py`); config build + container wiring in `src/gpt_trader/cli/services.py` |
| Wire services / composition root | `src/gpt_trader/app/container.py` | `ApplicationContainer` is the canonical DI entry point |
| Configure profiles / BotConfig | `src/gpt_trader/app/config/bot_config.py` | `ProfileLoader` in `src/gpt_trader/app/config/profile_loader.py`; YAML profiles in `config/profiles/` |
| Spot/CFM symbols + gating | `src/gpt_trader/features/live_trade/symbols.py` | `trading_modes`, `derivatives_enabled`, `cfm_enabled`, allowlists, `CFM_SYMBOL_MAPPING` |
| Add/modify a trading strategy | `src/gpt_trader/features/live_trade/factory.py` | Strategies in `src/gpt_trader/features/live_trade/strategies/` (baseline in `baseline/`) |
| Change the trading loop | `src/gpt_trader/features/live_trade/engines/strategy.py` | Runs `create_strategy()` and executes decisions each cycle |
| Modify live execution (guarded engine) | `src/gpt_trader/features/live_trade/engines/strategy.py` | Live loop uses `TradingEngine._validate_and_place_order()`; `submit_order()` is external entrypoint |
| Change risk rules | `src/gpt_trader/features/live_trade/risk/manager/__init__.py` | Also see runtime guards in `src/gpt_trader/features/live_trade/execution/guards/` |
| Add monitoring / alerts | `src/gpt_trader/monitoring/` | Heartbeat/status wiring in `src/gpt_trader/features/live_trade/engines/strategy.py` |
| Backtesting / simulation | `src/gpt_trader/backtesting/` | Entrypoints vary; start with `var/agents/reasoning/backtest_entrypoints_map.md` |
| Security & secrets | `src/gpt_trader/security/` | Reference: `docs/SECURITY.md` |

## Golden Path

- Use `ApplicationContainer` (`src/gpt_trader/app/container.py`) for all dependency wiring.
- Use `build_bot()` or `bot_from_profile()` from `bootstrap.py` for simple bot creation.
- If you need to understand historical architecture decisions, use git history (PRs/commits) plus `docs/ARCHITECTURE.md`.

## Useful Commands

- `uv run gpt-trader --help`
- `uv run agent-map` (dependency map tooling)
- `uv run agent-tests` (test selection helpers)
- `rg -n "symbol" src/gpt_trader/features/live_trade` (fast codebase search)
