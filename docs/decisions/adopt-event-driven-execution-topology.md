# Adopt an event-driven execution topology — rails as kernel, not workflow

---
status: proposed
date: 2026-07-04
deciders: rj
supersedes:
superseded-by:
---

> While `status: proposed`, this is an open decision the owner has not yet made.
> See the [decisions README](README.md) for the lifecycle.

## Context

Stage 2 mechanisms are live: the hourly cycle proposes, auto-approves inside the
budget envelope, and paper-executes under the audited autonomy log
(`bounded_autonomy`). Reviewing that shape against the destination — an
autonomous entity that leverages what machines are good at (reaction time,
breadth, tirelessness, continuous self-measurement) — exposes a topology
problem, not a rails problem:

- **Stage 2 automated the approver instead of removing the approval-shaped
  bottleneck.** The pipeline is still queue → review → sweep → batch-execute,
  driven by a calendar (launchd hourly). Bounded autonomy seated a robot at the
  human's desk to work the same queue on the same clock.
- **Human review latency is baked into the idea ontology.** Eligibility
  requires a multi-hour/day horizon because an idea had to survive human review
  latency ([DIRECTION.md](../DIRECTION.md#gate-before-execution-paths)). That is
  a property of the `human_approved_execution` mode, not of a sound trade idea —
  yet it is enforced as if invariant. The frequency-headroom directive already
  recorded that the cadence ceiling is approval latency, not the rails.
- **The one bot-shaped component is routed through the slow spine.** The
  live_trade engine consumes streaming WebSocket data and decides per candle,
  but the sanctioned path slows it to queue-and-sweep cadence rather than
  bringing the rails' guarantees to the engine's cadence.
- **The batch harness is starting to ossify into architecture.** Streak
  evidence is being defined against the cycle manifest (a launchd artifact),
  and a rubric scorecard built against it would cement the batch shape.

What is *not* in question: the rails themselves. The append-only audit trail,
versioned budget envelope, audited autonomy ladder with automatic ratchet-down,
eligibility invariants, recorder, and closeout attribution are exactly the
substrate an autonomous system needs
([accept-staged-autonomy-direction](accept-staged-autonomy-direction.md),
[adopt-measured-outcome-rubric](adopt-measured-outcome-rubric.md)). The question
is whether they are exposed as a **workflow everything must thread through** or
a **kernel every execution path consults**.

This revises the posture of
[stabilize-before-closing-the-loop](stabilize-before-closing-the-loop.md)
(which correctly froze topology work until the loop closed — the loop is now
closed and operating) without reopening its "no second proposer brain" rule.

## Options

- **Option A — Adopt the event-driven topology (recommended).** Reclassify the
  rails as a runtime risk kernel; make the in-process, event-driven path the
  first-class execution lane; demote the queue/sweep/launchd shape to (1) the
  human-review client of the kernel and (2) scheduled chores. Detail below.
- **Option B — Keep the batch topology and tune it.** Shorten the cycle
  interval, widen the symbol set, keep queue-and-sweep as the only lane. Lower
  churn, but the latency floor stays calendar-shaped, the human-latency
  eligibility rule stays baked in, and every future capability inherits the
  batch assumption — the direct route to a cron-driven human workflow rather
  than a trading bot.

## The event-driven topology (Option A detail)

1. **Rails become a runtime risk kernel.** Budget envelope, autonomy state,
   eligibility invariants, and audit append are exposed as an in-process gate
   any execution path consults per decision. The ticket queue and approval
   sweep become one *client* of that kernel — the human-review client — not the
   spine. (The Stage 2 execution gate already re-checks autonomy at execution
   time; this generalizes that pattern.)
2. **The event-driven path is first-class.** The live_trade engine may
   propose → auto-approve → paper-execute in-process, per event, under the
   kernel: same audit records, same envelope, same ratchet — no queue latency,
   no cron heartbeat. launchd keeps the chores (reports, reconciliation,
   self-review), which is what schedulers are for.
3. **Eligibility splits into invariant vs mode-dependent.** Explicit
   entry/invalidation/exit/max-loss, reproducible data source, expiry —
   invariant at every autonomy level (blast-radius control). Multi-hour
   horizon / review-latency survivability — a constraint of
   `human_approved_execution` only; under `bounded_autonomy` the horizon floor
   comes from measured capability, not human latency.
4. **Portfolio risk runs continuously.** High-water-mark tracking,
   drawdown-from-peak, and exposure become running monitors rather than
   per-cycle checks — the bot-native implementation of "realized gains are not
   principal."
5. **Evidence accelerates via replay.** Recorded market data + the replay
   module generate calibration and edge evidence at machine speed; wall-clock
   windows are reserved for gates that genuinely need live conditions (live
   promotion). Rubric metrics are computed from the idea-level closeout/audit
   trail — never from the cycle manifest — so evidence stays valid across
   topologies.

## Decision

*Open — fill in when the owner decides.*

## Consequences

- Kernel extraction, the in-process engine lane, the eligibility split, the
  continuous portfolio monitors, and replay-accelerated evidence are sequenced
  as GitHub issues; this record holds the shape, not the backlog.
- The hourly launchd cycle continues unchanged during the transition as an
  evidence harness, explicitly labeled scaffolding.
- Any rubric scorecard built before or during the transition reads the
  idea-level audit/closeout trail, not `cycle/manifest.jsonl`.
- [adopt-measured-outcome-rubric](adopt-measured-outcome-rubric.md) numeric
  gates are unchanged; only the *source* of evidence generalizes. If replay
  evidence is later admitted toward any gate, that is a separate owner call
  recorded there.
- [stabilize-before-closing-the-loop](stabilize-before-closing-the-loop.md)
  remains accepted; its "bridge the existing bot, no second proposer brain"
  rule carries forward into the kernel design.

## Safety boundary

This decision authorizes no broker/API call, no live execution, no money
movement, and no autonomy change. The event-driven lane operates under the same
audited autonomy log, budget envelope, and ratchet as the batch lane, in paper
only. Live order submission remains gated by
[DIRECTION.md](../DIRECTION.md#gate-before-execution-paths) and recorded human
approval.
