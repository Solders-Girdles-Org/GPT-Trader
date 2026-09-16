# Agent Docs Index

---
status: current
---

Use this folder for AI-focused navigation aids. Facts live in the code and in
the durable docs; there are no generated inventories to refresh.

## Core References

- [Agent workflow (canonical)](../../AGENTS.md)
- [Codebase map](CODEBASE_MAP.md)
- [Recurring project review pipeline](project_review_pipeline.md)
- [Glossary](glossary.md)
- [CLI conventions](conventions.md)
- [Naming patterns config](../../config/agents/naming_patterns.yaml)
- [Naming scan tool](../../scripts/agents/naming_inventory.py)

## Where to read facts directly

| Fact | Read it from |
|------|--------------|
| Environment variables | `config/environments/.env.template` plus `rg -n "getenv\|environ" src/gpt_trader` (typed config under `src/gpt_trader/app/config/`) |
| Config and risk schemas | `src/gpt_trader/app/config/bot_config.py`, `src/gpt_trader/features/live_trade/risk/config.py` |
| Metrics | `src/gpt_trader/monitoring/metrics_collector.py` |
| Structured log events | `src/gpt_trader/logging/` |
| Guard stack | `src/gpt_trader/features/live_trade/execution/guards/` |
| Backtest entrypoints | `src/gpt_trader/backtesting/`, `scripts/analysis/backtest_runner.py` |
| Tests for a module | `uv run pytest tests/unit/<mirrored path>` (tests mirror `src/` paths) |

## Tooling

- [Tooling helpers](../../scripts/agents/README.md)
