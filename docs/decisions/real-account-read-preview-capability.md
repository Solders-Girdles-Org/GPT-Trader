# Real-account capability — authenticated reads and non-binding previews only

---
status: accepted
date: 2026-07-12
deciders: rj
supersedes:
superseded-by:
---

## Context

GPT-Trader needs authenticated account evidence before any real execution lane can
be evaluated. Coinbase already has an observation profile, while Robinhood is the
concrete second venue identified by
[venue-neutrality-posture](venue-neutrality-posture.md) and issue #1224. The
existing direction requires a fresh official venue/API/account capability review
before real broker surfaces are used.

The official interfaces do not form one interchangeable brokerage API:

- Coinbase Advanced Trade exposes authenticated account reads and an order-preview
  endpoint.
- Robinhood Crypto Trading API exposes authenticated crypto reads and a GET
  estimated-price endpoint. Its published documentation does not establish a
  provider-enforced read-only API-key scope.
- Robinhood Agentic Trading exposes account reads plus equity and options review
  simulations through a hosted MCP server. The same server advertises separate
  place/cancel tools, and trading is confined to a dedicated Agentic account.
- No official conventional Robinhood REST brokerage API for equities/options was
  found. Unofficial mobile/private endpoints are not an acceptable substitute.

The owner selected authenticated reads plus previews as the first milestone,
requested Coinbase capability discovery across all products visible to the key,
and included Robinhood crypto, equities, and options. The owner also accepted the
residual authority of a Robinhood Crypto credential and selected an unfunded
Agentic account for equities/options.

Official references:

- [Coinbase Advanced Trade REST API](https://docs.cdp.coinbase.com/coinbase-business/advanced-trade-apis/rest-api)
- [Coinbase Preview Orders](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/preview-orders)
- [Robinhood Crypto Trading API](https://docs.robinhood.com/)
- [Robinhood Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
- [Robinhood: Trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)

## Options

- **Option A — Connect official read and preview surfaces behind exact
  allowlists (accepted).** Use least-privilege credentials where the provider
  supports them, bind expected account identity before every account operation,
  and expose only typed reads and demonstrably non-binding previews. Keep
  Robinhood out of the execution broker protocol.
- **Option B — Install trade-capable clients and rely on runtime policy.** This
  would simplify future execution work but unnecessarily places mutating methods
  in the current process and makes a configuration error consequential.
- **Option C — Use unofficial Robinhood endpoints for a uniform adapter.** This
  would broaden apparent coverage at the cost of an unstable, unaudited contract
  and account-lockout/terms risk.

## Decision

Accepted: Option A.

This record is the fresh venue/API/account capability review contemplated by the
scope exception in
[accept-staged-autonomy-direction](accept-staged-autonomy-direction.md). It amends
that record's venue exclusion for **authenticated observation only**; it does not
supersede the staged-autonomy decision or expand its Coinbase-only execution
destination.

The authorized remote operations are limited to:

1. **Coinbase Advanced Trade** — authenticated account, portfolio, permission,
   product, market-data, order-history, and position reads; plus the documented
   order-preview endpoint. The observation lane requires a portfolio-scoped
   view-only credential with no trading or transfer authority and exact
   expected-versus-observed identity binding.
2. **Robinhood Crypto Trading API** — authenticated GET operations for accounts,
   holdings, existing orders, trading pairs, quotes, and estimated price. The
   adapter must be structurally GET-only. The estimated-price response is an
   estimate, not order validation or a reserved quote.
3. **Robinhood Agentic Trading MCP** — the documented account/portfolio, equity,
   and options read tools plus `review_equity_order` and
   `review_option_order`. Tool names and schemas are attested at connection time;
   no generic tool-call method is exposed. The dedicated Agentic account remains
   unfunded.

Every unspecified operation is denied. In particular, this decision prohibits
order placement, cancellation, editing, transfers, deposits, withdrawals,
conversion commits, margin-setting changes, option exercise, watchlist/scan
mutations, and money movement.

Robinhood Crypto's credential-level residual risk is accepted for this scoped
connection: the provider credential may retain order authority even though the
application exposes and dispatches GET operations only. This acceptance is not
execution approval.

## Consequences

- Account observation and non-binding preview capabilities remain separate from
  the execution-oriented `BrokerProtocol` and `BROKER` selection.
- The provider-attested read is exposed as `account observe`; the existing
  `account snapshot` command retains its accepted runtime-telemetry contract.
- Venue-specific authentication, schemas, and transports stay in brokerage
  adapters and CLI wiring. No generic multi-broker execution router is created.
- Expected Coinbase portfolio/account identifiers and Robinhood account
  identifiers must be configured and matched before balances, positions, or
  previews are returned.
- Options use a structured observation shape rather than being forced into the
  existing spot/futures position model. Provider-neutral spot/futures/equity
  observations use signed quantity when direction matters: positive is long and
  negative is short.
- Provider schema or capability drift fails closed and is reported as unavailable;
  it is never worked around with unofficial endpoints.
- Coinbase CFM positions whose product metadata identifies a non-crypto future
  fail closed until the provider-neutral asset taxonomy can represent their
  underlying class without guessing.
- Credentials and OAuth/session material remain outside tracked files, logs,
  reports, issues, and pull requests.
- Real read/preview smoke tests are explicit, secret-gated, and verify stable
  order IDs, quantities, and hold/reservation fields before and after the call.

## Safety boundary

This record authorizes authenticated reads and the named non-binding
preview/estimate/review operations only. It authorizes no live order submission,
order cancellation, account funding, money movement, Robinhood execution adapter,
or autonomy change. Any such expansion requires a new accepted decision and
recorded human approval under [DIRECTION.md](../DIRECTION.md).
