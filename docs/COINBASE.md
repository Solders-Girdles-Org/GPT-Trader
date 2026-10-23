# Coinbase Integration

---
status: current
---

This repository implements Coinbase spot adapters by default. Adapter availability
is implementation state, not execution approval — venue/API/account capability
and the gates in [Live Operations](production.md) still apply before any live run.
Any Coinbase details that drift (endpoint catalogs, message schemas, rate limits)
should be verified against the **official Coinbase documentation** and the
**current implementation** in this repo.

## Source of Truth

- Code: `src/gpt_trader/features/brokerages/coinbase/`
- Configuration: `config/environments/.env.template` and `src/gpt_trader/app/config/bot_config.py`

## API Modes

- **Advanced Trade** (default): `COINBASE_API_MODE=advanced`
  - JWT/CDP keys, authenticated endpoints, portfolios, trading.
- **Exchange (sandbox)**: `COINBASE_API_MODE=exchange` + `COINBASE_SANDBOX=1`
  - Public sandbox endpoints only (no authenticated trading flows).

## Authentication (JWT-only)

- Preferred: `COINBASE_CREDENTIALS_FILE=/path/to/cdp_key.json`
- Or: `COINBASE_CDP_API_KEY` + `COINBASE_CDP_PRIVATE_KEY`
- Implementation: `src/gpt_trader/features/brokerages/coinbase/auth.py`

## Derivatives Capability Gates

These flags expose adapter code paths.
A live derivatives run still requires venue/account verification and the gates in
[Live Operations](production.md).

- **CFM futures (US)**: `TRADING_MODES=cfm` + `CFM_ENABLED=1` (requires an approved US futures account). This is the only supported derivatives venue.
- **INTX perps**: removed; see [decision record](decisions/intx-default-derivatives-venue.md) and [Deprecations](DEPRECATIONS.md). `COINBASE_ENABLE_INTX_PERPS` is a deprecated alias: a truthy value warns and substitutes for `CFM_ENABLED=1` only (config validation still requires `TRADING_MODES` to include `cfm`); falsey/unset values are ignored. Note the alias enables CFM only in `BotConfig.from_env()` — preflight/readiness checks read `CFM_ENABLED` directly and are not satisfied by the alias, so operators must still set `CFM_ENABLED=1` explicitly.

## Common Troubleshooting

- **Zero balances**: verify portfolio scoping + permissions, and confirm you are calling Advanced Trade endpoints with a CDP key.
- **“Sandbox doesn’t trade”**: Advanced Trade has no authenticated sandbox; use `MOCK_BROKER=1` for deterministic testing.

## References

- Internal integration notes: `src/gpt_trader/features/brokerages/coinbase/README.md`
- Official docs: https://docs.cdp.coinbase.com/
