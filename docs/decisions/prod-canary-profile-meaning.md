# Meaning of the prod and canary profiles under the approval ladder

---
status: accepted
date: 2026-07-02
deciders: rj
supersedes:
superseded-by:
---

## Context

Trading profiles are config snapshots, not execution approval. Under the
staged-autonomy direction
([accept-staged-autonomy-direction](accept-staged-autonomy-direction.md)),
enabling a live profile does not grant authority to submit orders — the gates in
[DIRECTION.md](../DIRECTION.md) and recorded human approval do. The `prod` and
`canary` profiles still carry historical "live-operation asset" framing that can
read as if selecting them were sufficient to operate live.

## Options

- **A — Keep `prod` / `canary` as live-operation assets**, explicitly gated by
  readiness evidence + recorded approval. Update docs/tests so the profile name
  is never treated as approval.
- **B — Redefine them as labels under the approval ladder** (e.g. capped
  validation lanes that only exist within a recorded approval envelope), retiring
  the standalone "live profile" concept.

## Decision

**Option A — `prod` / `canary` remain live-operation assets, explicitly gated
by readiness evidence + recorded approval.** Accepted 2026-07-02 by rj. The
profile name is configuration, never approval; the gates in
[DIRECTION.md](../DIRECTION.md) and recorded human approval remain the only
authorization path.

## Consequences

`ProfileLoader` semantics are unchanged. Docs and tests are hardened so no
profile value is ever treated as execution approval — tracked in
[#1122](https://github.com/Solders-Girdles/GPT-Trader/issues/1122).

## Safety boundary

No execution authorized. This is a config-semantics and policy decision.
