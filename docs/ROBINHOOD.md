# Robinhood integration

---
status: current
---

The accepted capability boundary is
[real-account read/preview capability](decisions/real-account-read-preview-capability.md).
Robinhood is currently an observation and non-binding review provider only; it
is not an execution broker and is not registered with `BROKER`,
`BrokerProtocol`, or `ReadOnlyBroker`.

## Robinhood Crypto

The Crypto adapter is documented in
`src/gpt_trader/features/brokerages/robinhood/crypto/README.md`. It authenticates to the
official v2 API with Ed25519 and exposes only typed account, holdings,
order-history, trading-pair, quote, and estimated-price reads. The transport is
structurally `GET`-only, fixed to the canonical Robinhood host and exact route
allowlist, rejects redirects and pagination drift, and binds every account row
to `ROBINHOOD_CRYPTO_EXPECTED_ACCOUNT_NUMBER`.

Required values stay outside tracked files:

- `ROBINHOOD_CRYPTO_API_KEY`
- `ROBINHOOD_CRYPTO_PRIVATE_KEY` (base64-encoded 32-byte Ed25519 private key)
- `ROBINHOOD_CRYPTO_EXPECTED_ACCOUNT_NUMBER`

The provider credential may retain trade authority. That residual credential
risk is accepted only for this observation capability; application dispatch
remains `GET`-only and no execution approval follows from configuring it.

## Explicit non-mutation smoke

The live smoke is never automatic. After separately validating the credential,
expected account, and an estimate quantity accepted for the selected pair, run:

```bash
ROBINHOOD_CRYPTO_REAL_READ_PREVIEW_SMOKE=1 \
ROBINHOOD_CRYPTO_PREVIEW_INSTRUMENT=BTC-USD \
ROBINHOOD_CRYPTO_PREVIEW_QUANTITY=<valid-quantity> \
uv run pytest tests/real_api/test_robinhood_crypto_read_preview_non_mutation.py -q
```

It dispatches only authenticated reads and one non-binding estimated-price
`GET`, then verifies stable balances, holds, positions, and order evidence.

The Agentic MCP read/review adapter remains a separate Phase 2 slice. It must
attest exact tool names and schemas and expose no generic tool-call surface.
