# Operator web console — a thin adapter over the trade-idea spine

---
status: accepted
date: 2026-07-02
deciders: rj
supersedes:
superseded-by:
---

## Context

The [TUI removal](remove-tui-subsystem.md) left the CLI as the sole human
interface and recorded the shape of any successor: *"a fresh thin adapter over
the core library / CLI, not a revival."* Two things have changed since that
removal that force the choice now:

- **The Stage-1 paper loop is closed and scheduled** (PRs #1149, #1151). Cycle
  turns now produce proposals, fills, and closeouts ambiently — while no
  operator is at a terminal. A CLI serves the operator who is already looking;
  it has no answer for work that accumulates while nobody is.
- **Owner review latency is the loop's throughput bound.** The
  [direction](../DIRECTION.md) requires every Stage-1 idea to be
  human-approved, and its eligibility gate requires ideas to survive human
  review latency. The rails are not the ceiling; the time an idea waits for a
  decision is. An interface that shortens and informs that decision is on the
  Stage 1 → Stage 2 critical path, because the
  [measured-outcome rubric](adopt-measured-outcome-rubric.md) needs a track
  record of reviewed ideas to grade promotion.

The architecture has already paid for this surface. The direction's invariant —
every operator action is an identity-stamped library call with CLI / MCP as
thin adapters — means a console adds presentation, not machinery:
`TradeIdeaService` (`src/gpt_trader/features/trade_ideas/service.py`) already
exposes queue status, approval context, budget headroom, versioned records,
audit queries, and closeout attribution. And the
[five-role composition](adopt-five-role-composition.md) holds the
approval/policy role **unchanged** during convergence, so a console built on
that spine does not fight the realignment. The converse also bounds the scope:
a console must not render `src/gpt_trader/features/live_trade/` internals —
that side is being dismantled, and observing in-memory engine state is one of
the failure modes that sank the TUI.

Owner constraint, stated 2026-07-02: the interface exists for **orchestrating
and managing agents**. It must never grow a manual order-entry surface.

One framing shapes the whole design: per-trade approval is Stage-1
scaffolding, not the product. At Stage 2 the budget envelope auto-approves and
only out-of-envelope ideas queue for review. The console must therefore be
designed for the review queue's own obsolescence — the durable core is the
envelope console (budget levers, ratchet history, exception queue,
after-the-fact review), and the queue must instrument itself (approval
latency, agreement rate per proposer) because that instrumentation *is* the
Stage-2 graduation evidence.

## Options

- **Option A — Local web console over `TradeIdeaService`.** A server-rendered
  FastAPI app in a new web-adapter package (a peer of `src/gpt_trader/cli/`),
  bound to 127.0.0.1, actor identity from config.
  Screens map to the five roles: review queue + idea detail
  (approval/policy), accountant view (equity, high-water mark, budget vs
  usage), agent activity (proposer/executor turns), audit and autonomy state.
  Renders durable artifacts — stores, snapshots, the audit log — never
  engine memory. Trade-off: a new package and dependency tree
  (fastapi/uvicorn/jinja2) to maintain; mitigated by no JS build step and
  pytest-only testing, avoiding the TUI's bespoke asset pipeline.
- **Option B — Richer CLI only.** No new surface, but a CLI cannot be a
  durable ambient interface: no persistent queue view, no at-a-glance budget
  state, and review latency stays bound to an operator already sitting at a
  terminal.
- **Option C — MCP-only adapter.** Fits the thin-adapter invariant and enables
  conversational operation, but puts a dialogue layer between the owner and a
  queue that needs fast scanning and one-click decisions. Complementary, not
  competing: an MCP adapter over the same service can be added later without
  revisiting this decision.
- **Option D — Defer until five-role convergence completes.** Avoids building
  against moving parts — but the console touches only the role the
  convergence decision holds stable, and deferral leaves approval latency,
  the Stage-2 ceiling, unaddressed during exactly the window the track record
  should accumulate.

## Decision

Accepted: Option A — a local, server-rendered web console as a thin adapter
over `TradeIdeaService`, built in three phases tracked in issue
[#1152](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1152): review
queue + idea detail first, accountant + agent activity second, the envelope
console third. Queue self-instrumentation (approval latency, agreement rate)
ships in phase 1, not later — it is the evidence a Stage-2 promotion case
will cite.

This decision does not change any approval requirement. In particular,
auto-approval of paper-lane ideas is **not** decided here; if proposed later,
it is a separate decision record that should draw its evidence from this
console's agreement-rate instrumentation.

## Consequences

- A new web-adapter package under the `gpt_trader` namespace (a peer of
  `src/gpt_trader/cli/`); server-rendered templates, no JS build step,
  local-bind only. Every mutation
  is an existing identity-stamped `TradeIdeaService` call; the console
  introduces no new state stores.
- Structural enforcement of the owner constraint: the package must not import
  from `src/gpt_trader/features/live_trade/`, and no code path constructs an
  order. The existing import-boundary guard is extended to cover it.
- New runtime dependencies (fastapi, uvicorn, jinja2) enter the lock.
- Approval via the console carries exactly the weight of `gpt-trader ideas
  approve` — same service call, same audit record, same actor stamping.
- At Stage 2 the review queue becomes the exception queue for out-of-envelope
  ideas; the envelope console becomes the primary operator seat. This is a
  re-weighting of existing screens, not a rebuild.
- Follow-up work is tracked in
  [#1152](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1152), phased
  as separate reviewed PRs.

## Safety boundary

This decision authorizes no broker/API call, no live execution, no money
movement, and no autonomy change. The console is presentation over existing
identity-stamped service calls; it adds no execution lane, and any live order
submission still requires the gates and recorded human approval in
[docs/DIRECTION.md](../DIRECTION.md). A manual order-entry surface is
explicitly and permanently out of scope for this interface.
