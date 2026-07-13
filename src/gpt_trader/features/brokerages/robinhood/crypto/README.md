# Robinhood Crypto read and estimate adapter

This package implements the Robinhood Crypto portion of the accepted
[real-account read/preview capability](../../../../../../docs/decisions/real-account-read-preview-capability.md).
It is not an execution broker.

The public surface is limited to exact-account observation, existing-order
history, trading-pair and best-bid/ask reads, and non-binding estimated prices.
The transport is fixed to `https://trading.robinhood.com`, signs only `GET`
requests with Ed25519, rejects redirects and target drift, and denies every
unspecified path or query before dispatch. Nothing in this package is registered
with `BROKER`, `BrokerProtocol`, or `ReadOnlyBroker`.

Required runtime values stay outside tracked files:

- `ROBINHOOD_CRYPTO_API_KEY`
- `ROBINHOOD_CRYPTO_PRIVATE_KEY` (base64-encoded 32-byte Ed25519 private key)
- `ROBINHOOD_CRYPTO_EXPECTED_ACCOUNT_NUMBER`

The credential may retain provider-side trade authority, as documented in the
accepted decision. Application dispatch remains structurally GET-only; this is
not execution approval.

The live non-mutation smoke is explicit and opt-in. It requires the credentials
and expected identity above plus an operator-selected valid estimate quantity:

```bash
ROBINHOOD_CRYPTO_REAL_READ_PREVIEW_SMOKE=1 \
ROBINHOOD_CRYPTO_PREVIEW_INSTRUMENT=BTC-USD \
ROBINHOOD_CRYPTO_PREVIEW_QUANTITY=<valid-quantity> \
uv run pytest tests/real_api/test_robinhood_crypto_read_preview_non_mutation.py -q
```

The smoke performs only authenticated reads and one estimated-price `GET`, then
requires stable balances, holds, positions, and order evidence before and after.
It is never part of automatic CI.
