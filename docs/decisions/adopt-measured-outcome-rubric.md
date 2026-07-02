# Adopt a measured-outcome operating rubric

---
status: accepted
date: 2026-07-02
deciders: rj
supersedes:
superseded-by:
---

> **Accepted.** This carries the former `OPERATING_RUBRIC_V2.md` draft.
> [DIRECTION.md](../DIRECTION.md) adopts the graduation model below. Numbers
> marked **(tune)** are proposed starting values that encode risk appetite —
> they are the owner's to set and remain open to tuning without reopening this
> decision. Nothing here authorizes live execution; the boundary stays
> `human_approved_execution`.

## Context

The original operating rubric did three jobs at once — stated a philosophy,
listed capabilities, and tried to gate scope creep — and they tangled. Its
"evidence" column meant three incompatible things (*a file exists* / *a metric* /
*demonstrated once*), so "Stage 0 complete" could be claimed because code
existed, even though the capability was not wired into a working loop. It modeled
features, not the operating loop, so "the loop is not connected to real data or
real intelligence" could not surface as a failed criterion.

## Options

- **A — Adopt the measured-outcome model (recommended).** Separate the three
  jobs and fix the evidence problem (detail below).
- **B — Keep the original capability/evidence rubric.** Lower churn, but retains
  the conflated-evidence flaw.

## Decision

**Option A — adopt the measured-outcome model.** Accepted 2026-07-02 by rj.

## The measured-outcome model (Option A detail)

**Three separated parts.** *Charter* (philosophy + invariants — see
[DIRECTION.md](../DIRECTION.md)); *the operating loop* (the object the gates
measure); *graduation gates* (one consistent evidence type).

**Evidence — one type only:** a demonstrated, measured outcome over a defined
observation window, recorded in the audit/closeout trail. Never a file pointer,
never "exercised once." What is *built* belongs in [STATUS.md](../STATUS.md); a
criterion that cannot be expressed as a measured outcome is an invariant or a
build task, not a gate.

**Observation window (tune):** rolling 60 days *and* ≥ 200 closed ideas,
whichever is larger.

**The operating loop the gates grade:**

```
market snapshot → propose → eligibility/policy filter → review (human or auto)
   → approve → execute (paper/live) → record fill → attribute at closeout
   → refresh track record → (feeds the next turn)
```

Loop health (a red blocks promotion regardless of stage): proposals flowing when
the market is open; review-latency p95 within SLA; attribution coverage = 100%
of closed ideas; audit-integrity verification passes; no step starved.

**Metric vocabulary:** eligibility pass rate; attribution coverage; risk
calibration (losers whose realized loss ≤ recorded max-loss estimate); expectancy
(avg R); benchmark edge vs the deterministic baseline; max drawdown-from-peak;
track-record depth.

**Stage 1 → 2 promotion gates (tune)** — earn auto-approval within budget:
track-record depth ≥ 200 closed paper ideas over ≥ 60 days; eligibility pass rate
≥ 90%; attribution coverage = 100%; risk calibration ≥ 95%; expectancy > 0 **and**
benchmark edge > 0; max drawdown-from-peak ≤ the budget's configured limit;
kill-switch drill verified; daily-loss breaker demonstrated in paper.

**Stage 2 → 3 promotion gates (tune)** — earn self-direction: ≥ 90 days bounded
autonomy with zero budget breaches needing human rescue; HWM protection proven on
≥ 1 real peak-drawdown event; budget renegotiation exercised ≥ once;
profit-taking exercised ≥ 3 times; calibration + skill sustained.

**Down-ladder (every stage with autonomy).** The current grant is *automatically
suspended via an audited event* on: drawdown-from-peak over the configured limit
(de-risk + back to human approval); risk calibration below floor (suspend
auto-approval); audit-integrity or kill-switch-drill failure (immediate drop to
manual-only); data staleness / venue degradation (pause until cleared).
Restoration is **not** automatic — re-meet the gate over a fresh window plus a
recorded human decision. This is the operational form of "earned, recorded,
reversible" and of the "triple-then-zero is the worst outcome" philosophy.

## Consequences

- DIRECTION's graduation section references this model; per-stage numeric
  thresholds become owner-set values recorded here.
- Stage-completion claims move from "code exists" to "outcome measured over the
  window," closing the gap that let a disconnected loop read as "done."

## Safety boundary

A rubric is a measurement standard, not an execution authorization. Accepting it
does not enable auto-approval, live submission, or any autonomy expansion.
