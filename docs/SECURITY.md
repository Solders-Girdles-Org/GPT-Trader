# Security Documentation

---
status: current
---

This document outlines the security controls and operational guidance for GPT-Trader.

## Security Overview

- Credentials and authentication live under `src/gpt_trader/features/brokerages/coinbase/`
  (`auth.py`, `credentials.py`) and use JWT-based CDP keys.
- Robinhood Crypto observation credentials live under
  `src/gpt_trader/features/brokerages/robinhood/crypto/README.md` and use Ed25519. The
  adapter is structurally GET-only even though the provider credential may
  retain trade authority.
- Secrets management is handled by `src/gpt_trader/security/secrets_manager.py`, which
  supports HashiCorp Vault and an encrypted local file fallback at
  `path_registry.USER_SECRETS_DIR` (`~/.gpt_trader/secrets` by default).
- Runtime validation is centralized in `src/gpt_trader/security/security_validator.py` and
  is enforced by the TradingEngine guard stack during live order submission.
- IP allowlisting is enforced by `src/gpt_trader/security/ip_allowlist_enforcer.py`.
- Structured JSON logs redact sensitive keys via `src/gpt_trader/logging/json_formatter.py`.

## Required Environment Variables

### Coinbase credentials (JWT only)

Advanced Trade / CDP JWT:

- `COINBASE_CREDENTIALS_FILE` (JSON file with `name` + `privateKey`), or
- `COINBASE_CDP_API_KEY` + `COINBASE_CDP_PRIVATE_KEY`, or
- `COINBASE_PROD_CDP_API_KEY` + `COINBASE_PROD_CDP_PRIVATE_KEY`

Legacy keys (Exchange keys and the retired `COINBASE_API_KEY_NAME` /
`COINBASE_PRIVATE_KEY` env vars) are not accepted; use CDP JWT credentials above.

### Robinhood Crypto observation credentials

- `ROBINHOOD_CRYPTO_API_KEY`
- `ROBINHOOD_CRYPTO_PRIVATE_KEY` (base64-encoded 32-byte Ed25519 private key)
- `ROBINHOOD_CRYPTO_EXPECTED_ACCOUNT_NUMBER` (non-secret expected identity)

These values enable only the typed observation and non-binding estimate adapter
documented in [Robinhood Integration](ROBINHOOD.md). They do not register an
execution broker or authorize order placement/cancellation.

### Secrets and encryption

- `GPT_TRADER_ENCRYPTION_KEY` (required outside development if using SecretsManager)
- `VAULT_ADDR` / `VAULT_TOKEN` (required to enable Vault storage)
- `GPT_TRADER_ALLOW_FILE_SECRET_FALLBACK=1` (explicit non-development opt-in
  for encrypted file storage when Vault is unavailable or unauthenticated)

`VAULT_ADDR` defaults to `http://localhost:8200` only for development-style
environments. Outside development, set `VAULT_ADDR` explicitly to an `https://`
Vault endpoint and provide a valid `VAULT_TOKEN`; the secrets manager fails
closed instead of downgrading to local encrypted files. Encrypted file storage
remains available for local development and for intentional non-development
fallback when `GPT_TRADER_ALLOW_FILE_SECRET_FALLBACK=1` is set with a durable
`GPT_TRADER_ENCRYPTION_KEY`.

### IP allowlist (optional, recommended for production)

- `IP_ALLOWLIST_ENABLED=1`
- `IP_ALLOWLIST_<SERVICE>=ip1,ip2,cidr1` (example: `IP_ALLOWLIST_COINBASE_INTX`)

## Preflight Security Checks

Run preflight to gather security evidence (credential validity, key permissions,
API connectivity). Preflight produces evidence; it is not authorization to run a
live profile — see [Live Operations](production.md) for the full gate sequence.

```bash
uv run python scripts/production_preflight.py --profile canary
```

Preflight checks live in `src/gpt_trader/preflight/checks/` and verify JWT generation,
permissions, and portfolio readiness. Use `--warn-only` or
`GPT_TRADER_PREFLIGHT_WARN_ONLY=1` to downgrade failures to warnings for diagnostic
runs only — a warn-only run does not satisfy the live readiness gate.

### Event store redaction scan

Preflight scans `runtime_data/<profile>/events.db` for unredacted secrets and fails if any
payload includes sensitive keys, PEM markers, or bearer tokens. It reports only event_id,
event_type, and key path (no values). Tune scan scope with:

- `GPT_TRADER_EVENT_STORE_REDACTION_MIN_EVENT_ID`
- `GPT_TRADER_EVENT_STORE_REDACTION_SCAN_ALL=1`
- `GPT_TRADER_EVENT_STORE_REDACTION_MAX_ROWS`
- `GPT_TRADER_PREFLIGHT_WARN_ONLY=1`

## Sensitive Data Handling

- JSON logs redact keys like `api_key`, `private_key`, `token`, and `password`.
- Runtime data persists to repo-local paths such as `runtime_data/<profile>/`
  (SQLite `events.db`, `orders.db`) and `runtime_data/optimize/`. Avoid writing
  secrets into repo-local runtime artifacts, events, or logs.

## Best Practices

- Use read-only keys for research/backtesting.
- Enable IP allowlisting in production and rotate credentials regularly.
- Store secrets in a vault or CI secret manager; keep `.env*` files out of version control.
