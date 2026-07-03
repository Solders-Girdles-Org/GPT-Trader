# Persistent audited autonomy-level state

---
status: proposed
date: 2026-07-03
deciders: RJ
supersedes:
superseded-by:
---

## Context

The accepted five-role composition
([adopt-five-role-composition](adopt-five-role-composition.md)) names
"persistent audited autonomy-level state" as a convergence step, and the
accepted direction ([DIRECTION](../DIRECTION.md)) requires that **every grant
of autonomy is earned, recorded, and reversible**, with autonomy ratcheting
**down automatically** on a breach — audited, not silent.

Today the autonomy level satisfies none of that:

- `AutonomyMode` exists as an enum
  (`src/gpt_trader/features/trade_ideas/models.py`: `research_only`,
  `human_approved_execution`, `bounded_autonomy`) and `ApprovalPolicy`
  enforces per-mode rules (`src/gpt_trader/features/trade_ideas/policy.py`),
  but the active mode is whatever the constructor default says in code.
- Nothing persists the mode, no CLI surface reads or changes it, and a mode
  change is an unaudited code edit — invisible to the append-only audit trail
  that records every other state change in the workflow.
- The risk budget already has the shape the direction demands: `RiskBudgetLog`
  (`src/gpt_trader/features/trade_ideas/budget.py`) is versioned, append-only,
  and every version carries the actor and rationale that produced it.

Why decide now: Stage 2 auto-approval
([#1039](https://github.com/Solders-Girdles/GPT-Trader/issues/1039)) is
blocked on an explicit autonomy-mode gate, and its acceptance criteria assume
a mode that can be verified rather than asserted. The automatic ratchet-down
needs a well-defined "breach" to consume, which is exactly what the risk
unification ([#1120](https://github.com/Solders-Girdles/GPT-Trader/issues/1120),
in flight) provides: one appetite vocabulary and one trading-day boundary.
The operator console direction
([adopt-operator-web-console](adopt-operator-web-console.md)) will also need
a truthful read surface for "what may the system do right now, and why".

## Options

- **Option A — Append-only autonomy log beside the budget log (recommended).**
  A new versioned, append-only `autonomy_state.jsonl` in the trade-ideas root,
  mirroring the `RiskBudgetLog` pattern: each entry records the mode, the
  actor type and id, the rationale, and (for automatic ratchets) the breach
  evidence that triggered it. `TradeIdeaService` resolves the current mode at
  construction and hands it to `ApprovalPolicy`; changes flow through the same
  audited service path as everything else. Raising the level requires a human
  actor; lowering is open to system actors so the breach ratchet can act
  without a human in the loop. Trade-off: one more versioned log to operate,
  and mode resolution must fail closed when the log is unreadable.
- **Option B — Autonomy mode as configuration (profile / BotConfig / env).**
  Cheapest to wire, but a config change is silent and unversioned — it
  violates the recorded-and-reversible property, and it collides with the
  accepted rule that a profile name is never execution approval
  ([prod-canary-profile-meaning](prod-canary-profile-meaning.md), hardening
  tracked in [#1122](https://github.com/Solders-Girdles/GPT-Trader/issues/1122)).
- **Option C — Fold the mode into `RiskBudget` versions.** Reuses the existing
  log and its integrity checks, but couples risk appetite with authority: a
  budget tweak and an autonomy grant become the same event type, muddying both
  audit stories. It also blocks the Stage 3 meta-envelope shape, where agents
  may renegotiate budgets inside owner limits but must never touch their own
  autonomy level.

## Decision

Open — awaiting owner decision. Option A is recommended: it is the only shape
that makes the autonomy level as auditable as the budget it gates, and it
keeps authority and appetite as separately-audited concerns.

## Consequences

If Option A is accepted:

- New `AutonomyStateLog` in `features/trade_ideas`: append-only JSONL beside
  `risk_budget.jsonl`, versioned entries with mode, actor, rationale, and
  optional trigger evidence. Integrity rules match the budget log (strict
  version sequencing; append is the only write).
- `TradeIdeaService` resolves the active mode from the log and constructs
  `ApprovalPolicy` with it. Fail-closed resolution: an **absent** log means
  the seeded default `human_approved_execution` (today's behavior, no AI
  submission); an **unreadable or integrity-broken** log resolves to
  `research_only` and surfaces the error.
- Transition rules: raising the level requires `ActorType.HUMAN` with a
  rationale; lowering is permitted to any actor. The automatic ratchet-down
  appends the breach evidence it acted on. Every transition is also an
  audited workflow event.
- Ratchet triggers are defined against the unified risk vocabulary from
  [#1120](https://github.com/Solders-Girdles/GPT-Trader/issues/1120) (one
  appetite source, one trading-day boundary) so "breach" is a single
  well-defined event, not two dialects. The trigger set ships with the
  implementation issue, not this record.
- CLI gains a read surface (current mode + history) and a change command that
  goes through the audited service path. The kill switch stays orthogonal:
  it halts execution immediately; the autonomy log records level changes. A
  kill-switch trip may be a ratchet trigger, but the mechanisms stay separate.
- Entering `bounded_autonomy` remains gated by [DIRECTION](../DIRECTION.md):
  this record changes where the level is *recorded*, not what the level *is*.
  Together with the budget gates, this unblocks
  [#1039](https://github.com/Solders-Girdles/GPT-Trader/issues/1039).
- Implementation is filed as a follow-up issue once this record is accepted.

## Safety boundary

This decision authorizes no broker/API call, no live execution, no money
movement, and **no autonomy change**. It decides how the autonomy level is
persisted and audited; the level itself stays `human_approved_execution`
until the DIRECTION gates and a recorded human approval say otherwise.
