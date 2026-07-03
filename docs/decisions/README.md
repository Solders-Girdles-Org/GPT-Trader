# Decisions

---
status: current
---

This directory is the **single home for durable decisions** — product, scope,
architecture, and policy choices that should outlive chat logs, PR receipts, and
local branch state. One decision per file. See
[Information Architecture](../INFORMATION_ARCHITECTURE.md) for how this fits the
rest of the docs.

If a choice is worth remembering or is still waiting on the owner, it goes here —
not in STATUS, not in a roadmap "Needs Decision" table, not in a framework
"Accepted Direction" section. Those scattered homes were the drift source this
directory replaces.

## One file, one `status`, whole lifecycle

Each decision is one markdown file named with a slug
(`docs/decisions/intx-default-derivatives-venue.md`). The `status` frontmatter
field carries its entire lifecycle:

| `status` | Meaning |
|----------|---------|
| `proposed` | An **open decision**: evidence and options are gathered; the owner has not chosen. This is how a pending decision is tracked — query `status: proposed` to list everything awaiting a call. |
| `accepted` | Decided and in force. The "why" is durable here. |
| `rejected` | Considered and declined; kept so it is not relitigated. |
| `superseded` | Replaced by a later decision; set `superseded-by`. |
| `deprecated` | No longer relevant but retained for history. |

The same mechanism covers both "we need to decide X" and "we decided X" — there
is no separate open-questions list to maintain.

## Frontmatter schema

```yaml
---
status: proposed          # proposed | accepted | rejected | superseded | deprecated
date: 2026-06-28          # ISO date the record was created or last decided
deciders: rj              # who owns / owned the call
supersedes:               # slug of an ADR this replaces (optional)
superseded-by:            # slug of the ADR that replaced this (set when superseded)
---
```

A record needs a top-level `# <title>` heading plus the frontmatter; only
`status` and `date` are required frontmatter keys. A file missing the title or
`status` is rejected by the index generator (it does not silently disappear).
Copy [`_template.md`](_template.md) to start one.

## How to use it

1. **Open a decision.** Copy the template to `docs/decisions/<slug>.md`, set
   `status: proposed`, and fill in Context, Options, and the evidence that lets
   the owner choose. A `decision-needed` GitHub issue may link to it, but the
   durable packet lives here.
2. **Decide.** Flip `status` to `accepted` (or `rejected`), record the choice and
   its consequences. Keep it; don't delete rejected options.
3. **Supersede.** When a later decision overrides an earlier one, add a new file,
   set the old one's `status: superseded` and `superseded-by: <new-slug>`, and
   the new one's `supersedes: <old-slug>`.

The index below is **generated** from each file's frontmatter by
`scripts/maintenance/generate_decision_index.py` — do not hand-edit between the
markers. Regenerating cannot drift from the files.

<!-- BEGIN GENERATED DECISION INDEX -->
| Date | Decision | Status |
|------|----------|--------|
| 2026-07-03 | [MIT license for the public repository](adopt-mit-license.md) | accepted |
| 2026-07-03 | [Persistent audited autonomy-level state](persistent-autonomy-state.md) | accepted |
| 2026-07-02 | [Account snapshot command — wire a real provider or remove it](account-snapshot-wire-or-remove.md) | accepted |
| 2026-07-02 | [Adopt a measured-outcome operating rubric](adopt-measured-outcome-rubric.md) | accepted |
| 2026-07-02 | [Canonical risk-limit vocabulary — budget vs runtime limits](canonical-risk-limit-vocabulary.md) | accepted |
| 2026-07-02 | [Event JSONL: accepted fallback or import-only historical data](event-jsonl-compatibility.md) | accepted |
| 2026-07-02 | [Five-role runtime composition — trade-idea spine, recorder and executor as separate arms](adopt-five-role-composition.md) | accepted |
| 2026-07-02 | [Meaning of the prod and canary profiles under the approval ladder](prod-canary-profile-meaning.md) | accepted |
| 2026-07-02 | [Operator web console — a thin adapter over the trade-idea spine](adopt-operator-web-console.md) | accepted |
| 2026-07-02 | [Stop committing high-churn generated agent inventories](stop-committing-high-churn-agent-inventories.md) | proposed |
| 2026-07-01 | [Remove the unwired account manager and strategy-dev lab](remove-unwired-account-manager-and-strategy-lab.md) | accepted |
| 2026-06-30 | [Remove the TUI subsystem](remove-tui-subsystem.md) | accepted |
| 2026-06-28 | [INTX default derivatives venue](intx-default-derivatives-venue.md) | accepted |
| 2026-06-27 | [Stabilize and reconcile before closing the Stage 1 loop](stabilize-before-closing-the-loop.md) | accepted |
| 2026-06-22 | [Trade-ideas CLI is the active discovery lane](trade-ideas-cli-discovery-lane.md) | accepted |
| 2026-06-11 | [Accept the staged-autonomy direction (human-approved execution → bounded autonomy)](accept-staged-autonomy-direction.md) | accepted |
<!-- END GENERATED DECISION INDEX -->
