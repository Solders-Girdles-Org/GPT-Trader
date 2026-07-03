# Stage 2 auto-approval inside the budget envelope

---
status: accepted
date: 2026-07-03
deciders: RJ
supersedes:
superseded-by:
---

## Context

The accepted rubric ([adopt-measured-outcome-rubric](adopt-measured-outcome-rubric.md))
names Stage 2 as "auto-approval within budget", and
[#1039](https://github.com/Solders-Girdles/GPT-Trader/issues/1039) specifies the
workflow: ideas that pass **every** approval-policy check auto-approve with a
system actor; ideas with any violation stay `proposed` for human review; both
paths are audited.

`ApprovalPolicy` (`src/gpt_trader/features/trade_ideas/policy.py`) has carried a
placeholder since the mode enum landed: in `bounded_autonomy`, non-human
approval is refused "until a strategy envelope, kill-switch evidence, and audit
evidence are modeled **or a later decision packet scopes a narrower
exception**". This record is that decision packet. The gates the placeholder
waited on now exist:

- Budget gates are explicit and enforced at approval time — per-idea max loss,
  projected daily loss, open-notional cap, concurrency, review latency
  ([#1036](https://github.com/Solders-Girdles/GPT-Trader/issues/1036),
  [#1120](https://github.com/Solders-Girdles/GPT-Trader/issues/1120)).
- The autonomy level is persistent, audited, and human-raised-only, with an
  automatic breach ratchet
  ([persistent-autonomy-state](persistent-autonomy-state.md),
  [#1170](https://github.com/Solders-Girdles/GPT-Trader/issues/1170)); that
  record states it "unblocks #1039" together with the budget gates.
- Paper fills reconcile to the audit trail, so envelope exposure is measured,
  not asserted ([#1035](https://github.com/Solders-Girdles/GPT-Trader/issues/1035)).

## Options

- **Option A — Narrow system-actor exception behind two independent gates
  (recommended).** In `bounded_autonomy`, `ActorType.SYSTEM` may approve an
  idea **only** when `approval_violations()` returns empty — the same checks a
  human approval must pass, nothing waived. The only writing path is an
  explicit sweep (`ideas approve --auto-sweep`) that is itself gated by a
  feature flag defaulting off (`GPT_TRADER_IDEAS_AUTO_APPROVAL`). AI and venue
  actors remain refused. Trade-off: two switches to reason about (flag + mode),
  but each failure stays loud and either switch alone keeps auto-approval off.
- **Option B — Wait for full strategy envelopes.** Keeps the placeholder until
  per-strategy envelopes are modeled. Safest on paper, but it duplicates what
  the budget envelope already enforces at approval time and leaves Stage 2
  unimplementable against the accepted rubric.
- **Option C — Auto-approve inline at proposal time.** Fewer moving parts (no
  sweep), but it fuses proposing and approving into one event, weakening the
  two-step audit story and making the flag gate ambient instead of an explicit
  operator trigger.

## Decision

Accepted: Option A. Auto-approval is a narrow, doubly-gated exception: the
audited autonomy log must resolve to `bounded_autonomy` (human-raised, breach
ratchet active) **and** the operator must have enabled the default-off feature
flag. A system approval passes the identical violation checks as a human
approval and is distinguishable in the audit log by system actor, sweep actor
id, reason prefix, and envelope evidence.

## Consequences

- `ApprovalPolicy`: in `bounded_autonomy`, human and system actors may approve
  (subject to all checks); AI and venue actors are refused. The non-human
  budget-change placeholder is untouched — budget renegotiation stays
  human-only pending the Stage 3 meta-envelope.
- `TradeIdeaService.auto_approve_sweep()`: refuses loudly (policy violation,
  not a silent no-op) when the flag is off or the resolved mode is not
  `bounded_autonomy`; re-resolves the mode per decision so the daily-loss
  ratchet can halt a sweep mid-pass; approves violation-free `proposed` ideas
  oldest-review-first; returns every skipped idea with its violations.
- CLI: `gpt-trader ideas approve --auto-sweep` is the only trigger. No
  scheduler ships with this decision.
- The scheduled Stage 1 paper cycle remains human-approval-only. System
  approvals created by this sweep are audited `approved` records, but the
  Stage 1 cycle skips them until a separate Stage 2 execution gate is accepted
  and implemented.
- Enabling the flag in operation remains gated by the rubric's Stage 1 → 2
  promotion gates (track-record depth, eligibility pass rate, attribution
  coverage, risk calibration, expectancy, kill-switch drill, daily-loss breaker
  demonstrated) — measured outcomes recorded in the audit/closeout trail, per
  [adopt-measured-outcome-rubric](adopt-measured-outcome-rubric.md). This
  record ships the mechanism; it does not claim the gates are met.

## Safety boundary

This decision authorizes no broker/API call, no live execution, and no money
movement. It does not change the autonomy level: the seeded default stays
`human_approved_execution`, raising it still requires an audited human action,
and the feature flag defaults off. Order submission remains out of scope
([#1039](https://github.com/Solders-Girdles/GPT-Trader/issues/1039) non-goals).
