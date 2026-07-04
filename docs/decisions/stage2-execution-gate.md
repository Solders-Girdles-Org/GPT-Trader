# Stage 2 paper execution gate for system-approved ideas

---
status: accepted
date: 2026-07-03
deciders: RJ
supersedes:
superseded-by:
---

## Context

[stage2-auto-approval-workflow](stage2-auto-approval-workflow.md) accepted a
narrow system-approval exception inside the budget envelope, but deliberately
left execution human-only. System approvals from `ideas approve --auto-sweep`
were audited `approved` records, yet the paper cycle skipped them before they
could produce paper fills, closeouts, or attribution evidence.

The Stage 2 rubric needs that loop to close in paper before any operational
promotion can be claimed: approve, execute, fill, close out, attribute, and
measure. The existing enforcement points are the right ones to gate:

- `PaperIdeaExecutor.resolve_approved_idea()` refuses non-human approvals.
- `PaperCycleRunner._execute_approved_ideas()` records a per-turn skip for
  non-human approvals before the executor is called.

## Options

- **Option A -- Double-gated paper execution for auto-sweep approvals
  (recommended).** Admit only the system approval actor written by the
  auto-approval sweep (`auto-approval-sweep`) when both independent gates pass:
  `GPT_TRADER_IDEAS_AUTO_EXECUTION` is explicitly enabled and the audited
  autonomy log resolves to `bounded_autonomy` at the execution decision
  boundary. Human-approved ideas remain admitted exactly as before. AI, venue,
  and other system approval actors remain refused.
- **Option B -- Keep paper execution human-only.** Safest and simplest, but it
  leaves auto-approved ideas as dead-end records, so Stage 2 outcomes cannot be
  measured over the auto-approved flow.
- **Option C -- Let any system approval execute in bounded autonomy.** Fewer
  checks, but too broad: the only accepted system writer today is the audited
  auto-approval sweep.

## Decision

Accepted: Option A. System-approved ideas may enter the paper execution lane
only when:

- the latest approval event is `ActorType.SYSTEM` from
  `auto-approval-sweep`;
- the operator has enabled `GPT_TRADER_IDEAS_AUTO_EXECUTION`; and
- `TradeIdeaService.resolve_execution_autonomy()` resolves to
  `bounded_autonomy`, applying the same daily-loss ratchet used by
  `auto_approve_sweep()`.

Either gate off preserves the previous typed executor refusal and scheduled
cycle skip. Human-approved execution is unchanged in every gate state.

## Consequences

- `PaperIdeaExecutor` remains the single admission point for the paper lane.
  It records submission evidence for gated system executions: the execution
  flag, autonomy version/mode/source, and approval actor. Human-approved
  submissions retain their existing audit shape.
- `PaperCycleRunner` re-checks the gate during the execution leg. If the
  daily-loss ratchet lowers the mode before or during a turn, remaining
  system-approved ideas are skipped; human-approved ideas are unaffected.
- `GPT_TRADER_IDEAS_AUTO_EXECUTION` is environment-only and defaults off. There
  is no CLI argument override and no scheduler change.
- The lane remains paper-only. `PAPER_BROKER_TYPES` is unchanged, and this
  decision authorizes no live broker/API calls, live order submission, money
  movement, or Stage 3 budget renegotiation.
- Operationally enabling the flag remains an operator act gated on measured
  outcomes from [adopt-measured-outcome-rubric](adopt-measured-outcome-rubric.md).
  Shipping the mechanism does not claim the Stage 1 -> 2 promotion gates are met.

## Safety boundary

This decision scopes a default-off paper execution mechanism only. Live order
submission still requires recorded human approval plus a current decision packet
or runbook naming the lane, constraints, verification boundary, and rollback or
kill-switch expectations.
