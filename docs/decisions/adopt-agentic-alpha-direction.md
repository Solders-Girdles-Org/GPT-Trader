# Alpha layer builds toward agentic reasoning — TA demoted to benchmark and feature inputs

---
status: accepted
date: 2026-07-08
deciders: rj
supersedes:
superseded-by:
---

## Context

The M4→M5 arc built the measurement instrument and then used it on the
technical-analysis alpha layer. The verdict is quantified (#1212, #1241,
`scripts/ops/replay_evidence.sh`):

- The regime-aware overlay initially produced **byte-identical** idea sets to
  baseline (edge exactly 0.0000); M5 gave it real decision channels (exit
  plans #1242, measured entry policy #1243), after which its edge is nonzero
  but negative pooled (-0.08) and positive only where volatile regimes
  coincide with ideas.
- Tuning the long-only MA family is a measured dead end: a 24-config grid
  produced **0/24** configs positive on both evidence windows (#1246).
- The regime-switcher measured **-0.26** edge versus baseline.
- The only durable positive is **mean-reversion's relative edge, consistent
  on two non-overlapping 720-hour windows** (+0.103 / +0.155, 435 + 202
  resolved ideas) — relative to a benchmark whose own absolute expectancy
  was negative on one window.

Separately, the venue-expansion work (#1224, #1232) deliberately deferred
intraday equity data: the operating cadence is hourly cycle turns over
hourly/daily bars, and the recorded frequency ceiling is approval latency,
not the rails. TA's comparative advantage lives in fast, microstructure-
adjacent trading — exactly the regime this system has chosen not to occupy.
At the cadence we actually run, the comparative advantage belongs to
deliberative reasoning: interpreting events, synthesizing research, weighing
regime context, choosing not to trade.

Two structural facts make the question decidable now rather than later:

- The owner's standing goal
  ([accept-staged-autonomy-direction](accept-staged-autonomy-direction.md))
  is an autonomous trading *entity*. Today AI agents build and operate the
  loop from outside, but the decision content itself is produced by fixed
  indicator arithmetic. The one place AI does not participate is the one
  place alpha is supposed to come from.
- The proposer seam is generation-agnostic by design: the spine cares that a
  proposal arrives with evidence, passes the risk budget, and gets measured
  (scorecard, counterfactuals, `benchmark_edge`) — not how it was generated.
  A proposer that calls a reasoning model fits the same seam the TA
  proposers use (`src/gpt_trader/features/strategy_tools/`).

With the rails proven and the TA channels measured near-dead, the forced
choice is where the next unit of alpha investment goes.

## Options

- **Option A — Agentic-reasoning alpha channel; TA demoted to benchmark and
  features (accepted).** The next alpha workstream is an analyst-agent
  proposer: model calls inside the project (reasoning over the snapshot,
  portfolio state, and its own closeout history; later, bounded research
  tools) emitting proposals through the existing seam. TA receives no new
  channel investment; existing proposers survive as (1) the measured
  benchmark the agent must beat and (2) feature inputs the agent may
  consult. Trade-offs: model proposals are non-deterministic and cannot be
  honestly backtested (a model trained through the replay window
  "predicting" it is contamination), so the channel is **forward-only** —
  wall-clock paper evidence, never historical-replay verdicts; a new
  external dependency and per-call cost enter the loop; confidently-wrong
  output is contained by the unchanged budget/approval rails and by the same
  measurement discipline that condemned the TA channels.
- **Option B — Keep mining the TA family.** More indicators, ensembles,
  parameter search. Trade-off: this is the path M5 just measured — 0/24 on
  the tuning grid, dead or negative channels, one modest relative edge. The
  marginal return of another TA channel is now an empirical estimate, and it
  is small.
- **Option C — Buy intraday data and move TA to its home turf.** Trade-off:
  reverses the recorded granularity deferral (#1229/#1232), adds paid-feed
  cost and session complexity, contradicts the approval-latency ceiling, and
  enters the most crowded competitive arena retail-side. Nothing in the
  evidence recommends it.

## Decision

Accepted: Option A. The alpha layer builds toward agentic reasoning; the
spine, rails, and evidence machinery are unchanged and are the asset this
direction depends on.

Bundled owner call (resolving the open decision recorded on #1241):
**mean-reversion joins the Stage-2 cycle proposer set** as the standing
benchmark — the best-supported proposer accrues wall-clock `benchmark_edge`,
and the agent channel's bar is beating it, not beating zero.

Standing constraints that come with the direction:

1. **Forward-only evaluation for model-generated proposals.** No
   historical-replay verdicts for any channel whose decisions came from a
   trained model; evaluation is wall-clock paper evidence through the
   existing scorecard. Replay tournaments remain the tool for deterministic
   proposers only.
2. **Full evidence bundle on every model-generated idea.** Model identity,
   inputs, outputs, and tool traces are captured on the idea record so every
   decision is auditable and re-examinable even though it is not
   re-runnable.
3. **Measurement before adoption, unchanged.** The agent proposer enters
   default-off, accrues evidence against the benchmark pair, and earns cycle
   adoption the same way any channel does. Reasoning is a hypothesis, not an
   exemption.

## Consequences

- Scoping umbrella filed: **#1252** (analyst-agent proposer behind the
  proposer seam: skeleton + evidence-bundle contract, model transport with
  cost telemetry, context pack, forward-only evaluation wiring; external
  research tools deliberately split out as a later, separately-gated step
  because they open a prompt-injection surface into trade decisions).
- Mean-reversion cycle adoption lands as its own small operational PR via
  the operator-enable pattern (#1213 precedent): explicit, audited,
  revertible by config.
- No new TA channels are built; existing proposers are maintained as
  benchmarks and feature inputs. The "no new strategy brains" line in
  #1241's out-of-scope list was M5 scope, not standing policy; this record
  is the standing policy.
- A model-API credential (data-plane only) enters configuration alongside
  the market-data keys; per-turn cost and latency become recorded telemetry.

## Safety boundary

This record authorizes no live execution, no money movement, and no
autonomy-level change; docs/DIRECTION.md gates are untouched. Model API
calls are outbound reasoning/research only and carry no execution
authority; model-generated proposals pass the same unchanged budget,
approval, and audit rails as every other proposal, in the paper lane only.
The prompt-injection surface of external research tools is explicitly
deferred and gated behind its own decision.
