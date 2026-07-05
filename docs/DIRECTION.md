---
status: current
---

# Direction — Where GPT-Trader Is Going

The single home for **destination and gates**: what the system is becoming, the
ladder to get there, and what must be true before each rung. For *where we are
now* see [STATUS.md](STATUS.md); for *what to build next* see the GitHub issue
tracker; for *the decisions behind this direction* see
[decisions/](decisions/README.md). This doc states direction once — it does not
restate current state or hold a backlog.

## Destination

An **autonomous trading entity** (`bounded_autonomy`): a bot that observes
markets, does its own research, and manages funds inside machine-enforced limits.
Scope is **Coinbase only (spot + CFM futures)**. The formal scope, boundary, and
exclusions are recorded in
[accept-staged-autonomy-direction](decisions/accept-staged-autonomy-direction.md).

## Charter

### Risk philosophy (owner-accepted)

1. **Principal is fully at risk.** The bot is funded with a lump sum the owner is
   prepared to lose entirely. Timidity that prevents the system from ever
   demonstrating skill is the bigger failure; seeded budgets are aggressive.
2. **Realized gains are not principal.** Tripling the portfolio and riding it
   back to zero is the *worst* outcome — worse than losing the original stake.
   Drawdown-from-peak matters more than drawdown-from-principal; high-water-mark
   tracking, profit-taking, and rebalancing are first-class capabilities.
3. **Limits bound system failure, not the agent's judgment.** Budget levers cap
   the blast radius of bugs, bad data, venue outages, and compromised
   credentials. They are renegotiated by the agents themselves through the
   audited budget workflow — handed over as track record accumulates, never
   silently removed.
4. **Every grant of autonomy is earned, recorded, and reversible.** Autonomy
   expansions ride the same append-only audit trail as trades.

### Non-negotiable invariants (true at every stage)

- Append-only audit trail for every state change, budget change, and autonomy grant.
- A reachable kill switch once anything can submit orders.
- No secrets or credentials in records, logs, or reports.
- Blast-radius caps always exist — they may be wide, but never absent.
- Autonomy ratchets **down automatically** on a breach (audited, not silent) and
  **up only by re-earning** the gate plus a recorded human decision.
- Every operator action is a library call with identity stamping; CLI / MCP
  are thin adapters over it. Tooling serves the owner, developing agents, and
  operating agents equally.

## The ladder

Staged autonomy. Each stage is entered by capability (the loop works) and graded
for promotion by measured track record.

| Stage | What it is | Autonomy boundary |
|-------|------------|-------------------|
| **0 — Rails** | Broker-neutral record, approval-gated workflow, audit, eligibility/policy, versioned budget, operator controls | AI proposes; humans do everything else |
| **1 — Human-approved loop** | propose → review → paper-execute → attribute → report, fed by real market data | AI never submits; every idea is human-approved |
| **2 — Bounded autonomy** | Auto-approval inside the budget envelope; HWM protection; profit-taking; kill switch; runtime daily-loss breaker | AI submits **only inside hard limits**; outside, it queues for review |
| **3 — Self-directed entity** | Capital allocation across strategies; self-adjusted budgets inside an owner meta-envelope; scheduled self-review; owner reporting cadence | Self-directed within owner-set meta-limits |

**Hard v1 rule:** AI does not submit live orders. Any API execution lane requires
an explicit approval event **and** a decision packet or runbook naming the lane,
its constraints, the verification boundary, and rollback/kill-switch
expectations.

The autonomy modes in full:

| Mode | AI may produce | AI may submit orders |
|------|----------------|----------------------|
| `research_only` | Research notes, theses, watchlists | No |
| `human_approved_execution` *(current)* | Broker-ready tickets + risk records | No — explicit approval required first |
| `bounded_autonomy` *(destination)* | Tickets inside pre-approved envelopes | Yes — only inside hard limits |

## Graduation

A stage is *entered* when its loop is closed and healthy; it is *promoted* when a
measured track record clears the gate. The graduation model — evidence as a
**measured outcome over a window** (never "code exists"), with numeric promotion
gates and an automatic down-ladder — is defined in the accepted
[adopt-measured-outcome-rubric](decisions/adopt-measured-outcome-rubric.md).
Build progress is tracked in [STATUS.md](STATUS.md) and the issue tracker;
graduation is a separate, measured question.

## Gate before execution paths

The Stage 0 rails are shipped (`src/gpt_trader/features/trade_ideas/`), so the
trade-idea record, append-only audit log, approval workflow, eligibility gate,
and versioned risk budget exist in code — that *is* their specification. What
remains gated before any non-manual execution lane opens:

- **Strategy eligibility.** Eligibility splits into two constraint classes
  ([adopt-event-driven-execution-topology](decisions/adopt-event-driven-execution-topology.md)).
  *Invariant, at every autonomy level:* explicit entry/invalidation/exit/max-loss,
  explainable from recorded data, sizeable before entry, with an expiry. Missing
  invalidation, missing max-loss, no reproducible data source, or a need for
  continuous babysitting are automatic rejections. *Mode-dependent:* surviving
  human review latency (multi-hour/day horizon) applies only under
  `human_approved_execution`; under `bounded_autonomy` the horizon floor comes
  from measured capability, not human latency. Rejection reasons record which
  class failed so track-record evidence can distinguish an unsound idea from
  one that is merely too fast for a human review loop.
- **Numeric risk budget.** Max loss per idea, max daily loss, max open notional
  by product type, max concurrent approved-but-unexecuted tickets, max review
  latency, and whether sizing is advisory or hard-capped must be chosen values.
- **Official venue/API/account capability review — pending.** No doc here
  authorizes account, venue, product, or API capability. A fresh review gates any
  non-manual Coinbase (or other) execution.

Open questions that gate parts of this are tracked as `proposed` decisions in
[docs/decisions](decisions/README.md). Profile meaning is resolved: `prod` and
`canary` remain live-operation assets, explicitly gated — the profile name is
never approval
([prod-canary-profile-meaning](decisions/prod-canary-profile-meaning.md)).
The derivatives default is resolved: the
[intx-default-derivatives-venue](decisions/intx-default-derivatives-venue.md)
decision is accepted.

## What this direction does not authorize

Stating a destination is not approval to operate. Nothing here authorizes real
broker/API calls, live trading commands, production preflight, canary operations,
money movement, or order submission. Those require the gates above and recorded
human approval. Paper/live readiness mechanics live in the operational
[Readiness Checklist](READINESS.md) and [Live Operations](production.md).
