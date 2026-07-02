# Stop committing high-churn generated agent inventories

---
status: proposed
date: 2026-07-02
deciders: rj
supersedes:
superseded-by:
---

## Context

Generated `var/agents/**` inventories are derived truth, but the committed
subset still dominates repo churn: 103 of 181 commits since 2026-06-01 touched
`var/agents`, and the 2026-07-01 gitignore pass
([canonical_sources.md](../agents/canonical_sources.md)) barely moved the rate
(19 of 35 commits since). The file-touch breakdown since 2026-07-01 is
`testing/**` 41, `reasoning/*.md` 26, `configuration` 10 — the `testing`
inventory regenerates whenever tests change, which is nearly every PR.

The cost compounds with the merge gate: strict up-to-date branch protection
means every merge to `main` invalidates other green PRs, and each rebase of a
PR that touches these inputs requires another `agent-regenerate` cycle.
Generated-file merge conflicts follow. Committing derived truth is a cache, and
the freshness gate (`agent-regenerate --verify`, the Agent Artifacts Freshness
CI lane, the scheduled refresh workflow) is the invalidation machinery that
cache demands.

The repo already has a precedent: `testing/test_inventory.json`,
`testing/source_test_map.json`, and `reasoning/*.{json,dot}` are gitignored,
registered as `optional_files` in `var/agents/index.json`, and regenerated on
demand — executed 2026-07-01 with owner approval.

## Options

- **Option A — Extend the `optional_files` precedent to the remaining
  high-churn groups.** Gitignore `var/agents/testing/index.json` and
  `testing/markers.json` (and evaluate `reasoning/*.md`); consumers run
  `uv run agent-regenerate` on demand. This includes the validator contract:
  `scripts/agents/agent_artifacts.py` currently hard-requires
  `testing/index.json` with a positive `total_tests`
  (`validate` fails on a fresh checkout if the file is absent), so its
  per-resource expectations must become absent-tolerant for `optional_files`
  while still validating content when the files are present. Trade-off: hosted
  or read-only agents lose pre-baked context in a fresh checkout and must
  regenerate (the scheduled refresh package still ships them).
- **Option B — Keep committing, but decouple from the PR loop.** Keep the trees
  committed, drop them from `agent-regenerate --verify`'s default surface, and
  let only the scheduled refresh workflow reconcile them via its automation
  branch. Trade-off: committed copies go intentionally stale between refreshes,
  which reintroduces exactly the drift the freshness gate was built to prevent.
- **Option C — Status quo.** Keep committing and freshness-gating everything.
  Trade-off: the measured ~55% commit-churn tax and rebase/regenerate loops
  continue.

## Decision

_Open. Recommendation: Option A — it follows the repo's own "prefer derived
over authored" rule and the already-approved 2026-07-01 precedent, and it is
the only option that removes the churn rather than hiding it._

## Consequences

Fill in when accepted. Expected shape for Option A: gitignore + `optional_files`
registration for the affected groups, absent-tolerant validation in
`agent_artifacts.py` for those groups, freshness gate shrinks to the remaining
committed set, and the measured share of commits touching `var/agents` drops
from ~55% to under 15%. Follow-up work is tracked in issue #1130.

## Safety boundary

This decision authorizes no broker/API call, live execution, money movement, or
autonomy change. It affects only generated development-context artifacts.
