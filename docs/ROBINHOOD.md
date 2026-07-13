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

## Robinhood Agentic Trading

Install the isolated optional client with `uv sync --extra robinhood-agentic`.
Set the non-secret expected identity:

- `ROBINHOOD_AGENTIC_EXPECTED_ACCOUNT_NUMBER`

OAuth tokens and dynamic-client metadata are stored in the operating-system
credential store. The first command-scoped connection opens Robinhood OAuth;
subsequent connections refresh through the official MCP SDK without placing
session material in tracked files.

The adapter is fixed to `https://agent.robinhood.com/mcp/trading`. At connection
time it paginates `tools/list` and attests the exact live-captured input/output
schema fingerprints for only `get_accounts`, `get_portfolio`,
`review_equity_order`, and `review_option_order`. The server currently advertises
separate place, cancel, watchlist, and scan mutation tools; none has a dispatch
route in the public gateway or facade. There is no generic `call_tool` method.

Account reads require exactly one Agentic-accessible account and bind it to the
configured expected account number before portfolio or review dispatch. Equity
reviews return provider-neutral non-binding preview evidence plus the exact
provider order-check, quote, and market-data disclosure evidence. Single-leg
option reviews retain a provider-specific immutable record rather than forcing
the option shape into the equity request contract.

## Explicit Agentic non-mutation smoke

The live smoke is never automatic. It performs account/portfolio reads and the
two accepted review simulations, then verifies the account observation is
stable. It never invokes a place, cancel, or mutation tool:

```bash
ROBINHOOD_AGENTIC_REAL_READ_REVIEW_SMOKE=1 \
ROBINHOOD_AGENTIC_EXPECTED_ACCOUNT_NUMBER=<accepted-account> \
ROBINHOOD_AGENTIC_EQUITY_SYMBOL=<symbol> \
ROBINHOOD_AGENTIC_EQUITY_LIMIT_PRICE=<price> \
ROBINHOOD_AGENTIC_OPTION_ID=<option-instrument-uuid> \
ROBINHOOD_AGENTIC_OPTION_LIMIT_PRICE=<price> \
uv run --extra robinhood-agentic pytest \
  tests/real_api/test_robinhood_agentic_read_review_non_mutation.py -q
```
