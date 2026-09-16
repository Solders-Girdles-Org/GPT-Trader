# Feature Flags Reference

---
status: current
---

This page is intentionally thin. Feature flags and configuration drift quickly, so canonical references live in code and generated inventories.

## Canonical References

- Operator defaults (minimal): [Environment template](../config/environments/.env.template)
- Every env var the code reads: `rg -n "getenv|environ" src/gpt_trader`
- Config and risk schemas: `src/gpt_trader/app/config/bot_config.py`, `src/gpt_trader/features/live_trade/risk/config.py`

## Precedence

Highest → lowest:

1. CLI arguments
2. Profile settings (`--profile ...`)
3. Environment variables (including `RISK_*` / `HEALTH_*` prefixes)
4. Dataclass defaults

Implementation entrypoints:

- `src/gpt_trader/app/config/bot_config.py`
- `src/gpt_trader/features/live_trade/risk/config.py`
