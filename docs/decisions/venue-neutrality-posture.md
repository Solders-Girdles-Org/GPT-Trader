# Venue-neutrality posture — adapters at the edges, no speculative abstraction

---
status: accepted
date: 2026-07-04
deciders: rj
supersedes:
superseded-by:
---

## Context

The system is built against Coinbase spot today, and an expansion to a second
brokerage (Robinhood is the likely candidate) would bring product types the
current contracts have never carried: equities, options, and the account
mechanics that come with them. The question forced now is not "which venue" but
"what do we protect in the meantime" — whether to build a multi-venue
abstraction ahead of need, or to hold a cheaper line until the expansion is
concrete.

A 2026-07-04 review of where Coinbase actually touches the code found the
trade-idea spine already venue-neutral in the ways that matter:

- Approval, budget, autonomy, and audit denominate in percentages of attested
  equity; nothing in them is venue- or asset-specific.
- `TradeIdea.instrument` is an opaque string
  (`src/gpt_trader/features/trade_ideas/models.py`), and the snapshot contract
  (`src/gpt_trader/features/trade_ideas/snapshot.py`) carries granularity as a
  plain string.
- Export tickets are broker-neutral payloads with venue as an enum tag
  (`src/gpt_trader/features/trade_ideas/broker_payloads.py`).
- The Coinbase specifics sit in adapter positions: the recorder's snapshot
  source (`src/gpt_trader/features/recorder/snapshot_source.py`,
  `src/gpt_trader/features/recorder/snapshot_builder.py`) is a self-labeled
  Coinbase module, and the paper cycle runner
  (`src/gpt_trader/features/idea_execution/cycle.py`) takes an injected
  snapshot provider, so the Coinbase client stays in the CLI layer.

This shape is a direct result of
[adopt-five-role-composition](adopt-five-role-composition.md) and the recorder
extraction; it is worth protecting deliberately rather than by accident.

The same review found three crypto-shaped assumptions that a second venue would
expose, and that would fail silently rather than loudly:

1. **No market calendar.** `trading_day`
   (`src/gpt_trader/core/risk_units.py`) and the external scheduler cadence
   assume markets never close. Equities break this ambiently: the daily-loss
   ratchet's day boundary (UTC day vs. exchange session), snapshot builds
   against a closed market yielding stale-but-valid-looking candles, expiry
   sweeps and review-latency windows running through weekends.
2. **Instrument-as-string is thin.** Fine for `BTC-USD` or `AAPL`; an options
   contract (underlying/expiry/strike/right) can be crammed into a string, but
   budget checks that need product structure — naked-short detection, actual
   leverage — cannot reason about an opaque token. The
   `allow_naked_shorts`/`allow_futures_leverage` booleans are already product
   vocabulary creeping into the budget.
3. **The budget thinks in notional.** `max_open_notional_pct` works for spot;
   for options, premium paid and exposure controlled diverge (a defined-risk
   spread and a naked call at identical notional are different risks), and
   equities add buying power, settlement, and pattern-day-trading limits to
   what the accountant role must reconcile.

## Options

- **Option A — Hold a leak-watch line now; build venue work additively when a
  second venue is concrete (recommended).** No abstraction is built ahead of
  need. Instead, reviewers hold a standing constraint: venue idioms must not
  enter the spine contracts. When a second venue is committed, the work is
  enumerable and additive (see Consequences). Trade-off: the three known
  assumptions stay in place until then, and the leak-watch is a review
  discipline, not a CI gate.
- **Option B — Build a multi-venue abstraction now.** Design the instrument
  taxonomy, calendar service, and venue interface before a second venue
  exists. Trade-off: speculative abstractions designed against one concrete
  venue are reliably wrong in shape; this contradicts the
  [stabilize-before-closing-the-loop](stabilize-before-closing-the-loop.md)
  posture of not building second systems ahead of evidence.
- **Option C — Decide nothing until expansion.** No standing constraint.
  Trade-off: Coinbase idioms (granularity enum names, symbol-format
  assumptions, UTC-day semantics) keep leaking into spine contracts one PR at
  a time, and the eventual expansion becomes a rework instead of an addition.

## Decision

Accepted: Option A. No multi-venue abstraction is built while Coinbase is the
only venue. The spine's venue-neutrality is a standing review constraint: new
code must keep venue specifics in adapter positions (recorder sources, CLI
wiring, export-payload renderers) and must not add venue idioms — granularity
enum names, symbol-format assumptions, session/day-boundary semantics — to the
trade-idea, budget, or snapshot contracts.

**Leak-watch list** (the assumptions to keep from deepening, and the first
things to fix when expansion is concrete): market-calendar absence,
instrument-as-string, notional-denominated budget vocabulary.

## Consequences

- No code changes now; this record is the constraint reviewers cite. No
  follow-up issues are filed either — deferring the work *is* the decision,
  so there is no backlog to track.
- When a second venue (e.g., Robinhood) becomes concrete, open one scoping
  issue referencing this record. Its expected scope — recorded here so that
  issue starts additive, not as a rework — is: a trading-calendar service; an
  instrument taxonomy replacing the opaque string; a buying-power/defined-risk
  dimension in the budget vocabulary; a second recorder snapshot source; a
  `TicketVenue` entry and export-payload renderer; a per-venue paper broker
  for the execution lane.
- Product-vocabulary flags already in the budget (`allow_naked_shorts`,
  `allow_futures_leverage`) are grandfathered; new product flags should
  trigger the taxonomy discussion rather than accrete as booleans.

## Safety boundary

This record authorizes no broker or API call, no new venue integration, no
live execution, no money movement, and no autonomy change. It constrains
future code shape only.
