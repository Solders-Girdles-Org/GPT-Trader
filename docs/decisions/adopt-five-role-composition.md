# Five-role runtime composition — trade-idea spine, recorder and executor as separate arms

---
status: proposed
date: 2026-07-02
deciders: rj
supersedes:
superseded-by:
---

## Context

The 2026-07-02 as-built review confirmed what
[stabilize-before-closing-the-loop](stabilize-before-closing-the-loop.md)
first recorded: the repository contains two trading systems. The live engine
(`src/gpt_trader/features/live_trade/`) is a self-contained trader — it owns
market-data ingestion (inline REST fetches, WS streaming as engine lifecycle
steps, `PriceTickStore`), decisioning, and order submission. The trade-idea
workflow (`src/gpt_trader/features/trade_ideas/`) is the system the accepted
[staged-autonomy direction](accept-staged-autonomy-direction.md) specifies —
audited records, human approval, versioned budget — and it already treats
market data as a recorded artifact (`MarketSnapshot` files consumed by
proposers and replay). The two connect only through the default-off,
whole-engine, spot-only proposal gate (`execution.strategy_signal_proposals`,
PR #1090).

Convergence needs a stated target shape, or each closing step will be designed
against a different implicit one. Three properties force the shape:

1. **Observation must outlive execution.** The direction's autonomy ladder
   ratchets down to `research_only`; a bot whose data collection stops when its
   engine stops cannot occupy that mode. Today streaming is an engine
   startup/shutdown step.
2. **Eligibility requires "explainable from recorded data."** Deciders must
   consume recorded snapshots, not in-memory state; the trade-idea lane
   already works this way, the live engine does not.
3. **Execution consumes data; it must not own ingestion.** The executor needs
   freshness-annotated marks for staleness/slippage guards — a read
   dependency, not ownership.

Related accepted decisions this proposal builds on (not re-decides):
[canonical-risk-limit-vocabulary](canonical-risk-limit-vocabulary.md) (budget
is canonical, runtime derives; #1120) and
[stabilize-before-closing-the-loop](stabilize-before-closing-the-loop.md)
(bridge the existing bot's intelligence; no second proposer brain). The Stage 1
rails lifecycle is CI-gated as of PR #1142 (`make stage1-smoke`).

## Options

- **Option A — Five in-process roles around the trade-idea spine.** The target
  composition is: **recorder** (WS/REST ingestion, tick/candle persistence,
  snapshot building; runs regardless of execution state; designed behind its
  own interface so a separate `record` entrypoint stays a deployment choice),
  **proposers** (the existing strategy/signal library converged onto the
  `Proposer` snapshot contract, per stabilize-before-closing-the-loop),
  **approval/policy** (the trade-idea workflow, unchanged),
  **executor** (the guard stack + submission seams extracted from
  `TradingEngine`, consuming APPROVED ideas only, paper first), and
  **accountant** (positions, equity, high-water-mark/peak tracking, envelope
  enforcement — where the accepted risk-vocabulary derivation lands).
  Convergence is staged extraction along existing seams; the live engine's
  direct decide→submit path retires only after proposer parity. Trade-off:
  several deliberate refactors and a period where old and new paths coexist
  behind gates.
- **Option B — One arm: the engine becomes the workflow's execution *and*
  market-data provider.** Cheapest wiring (the engine already has both), but
  it violates properties 1 and 3: halting execution still blinds observation,
  and `research_only` remains unreachable as a mode.
- **Option C — Keep two systems; deepen the bridge.** Extend the proposal gate
  and add an execution bridge back, leaving ownership as-is. No structural
  work, but every misalignment from the as-built review (two data views, two
  audit stories, engine-owned ingestion) persists and each bridge extension
  hardens them.
- **Option D — Big-bang rebuild against the target architecture.** Rejected by
  standing practice: the recovery posture after the 2026-07-01 rehab audit is
  verify→delete→fix, no rewrite; a parallel build is how the two-system state
  arose.

## Decision

_Open. Recommendation: Option A — it is the only shape that satisfies all
three forcing properties, it subsumes rather than contradicts the accepted
risk-vocabulary and no-second-brain decisions, and it converts convergence
into individually shippable extractions instead of a rewrite._

## Consequences

Fill in when accepted. Expected shape for Option A:

- Sequencing (each step ships alone, loop stays green under the Stage 1 rails
  smoke): paper executor for APPROVED ideas as a **new component** (not more
  `TradingEngine`); recorder extraction (success test: execution halted,
  recording continues); strategies converge onto the `Proposer` snapshot
  contract; risk unification per #1120 lands in the accountant; persistent,
  audited autonomy-level state consumed by all roles.
- Roles are in-process components with clear ownership — no daemons/IPC; only
  the recorder boundary is kept process-capable.
- `features/live_trade` shrinks as seams move out; its direct decide→submit
  path is retired (or explicitly fenced as legacy) after proposer parity.
- Follow-up work is filed as GitHub issues per step; none is authorized to
  start by this record alone.

## Safety boundary

This decision authorizes no broker/API call, no live execution, no money
movement, and no autonomy change. The executor role it names is paper-only at
introduction, with live brokers structurally unreachable from that lane — any
live execution lane still requires the gates and recorded human approval in
[docs/DIRECTION.md](../DIRECTION.md).
