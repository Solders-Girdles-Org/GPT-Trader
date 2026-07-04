# Canonical Sources: Agent Doctrine & Generated-Artifact Scope

---
status: current
---

Makes the [information-architecture rule](../INFORMATION_ARCHITECTURE.md) — each
fact lives once; generated inventories are derived truth — operational for two
things future agents inherit: **agent doctrine** (the rules a run relies on) and
the committed **`var/agents/**` generated context**.

## 1. Doctrine homing audit

The local `.claude/CLAUDE.md` is **gitignored** (`.gitignore:93`) and therefore a
per-checkout editor convenience mirror, not a source of truth. Every canonical
rule it restates already has a tracked home; no canonical-only rule remains
stranded there. Keep it as a mirror — do **not** point tracked docs *at* it.

| Canonical rule | Tracked home (owner) |
|----------------|----------------------|
| CLI human output (`✓/✗`, exit codes) + `CliResponse`/`CliErrorCode` envelope | [conventions.md](conventions.md) (added in #1074) |
| Trade-idea CLI worked example | [TRADE_IDEA_CLI_SPEC.md](../specs/TRADE_IDEA_CLI_SPEC.md) (surface-specific) |
| `monkeypatch` over `patch()`, mocking local imports, `CliResponse` test asserts | [testing.md](../testing.md), [CONTRIBUTING.md](../../CONTRIBUTING.md) |
| Banned abbreviations / approved terms | [naming.md](../naming.md), [glossary.md](glossary.md) |
| Merge discipline, quality gate, artifact-freshness step | [AGENTS.md](../../AGENTS.md) |

**Owner split (general vs. surface-specific):** [conventions.md](conventions.md)
owns the *general* CLI response/output contract. Command-family specs (e.g.
[TRADE_IDEA_CLI_SPEC.md](../specs/TRADE_IDEA_CLI_SPEC.md)) are *self-canonical*
for their own surface and should reference `conventions.md` rather than restate
the envelope.

## 2. `var/agents/**` generated-artifact scope

### How it is produced and gated

- **Produced by** [`regenerate_all.py`](../../scripts/agents/regenerate_all.py)
  (`uv run agent-regenerate`), one generator per resource group.
- **Validated by** [`agent_artifacts.py`](../../scripts/agents/agent_artifacts.py)
  (`uv run agent-artifacts validate` / `package` / `verify-package`), which
  reads every resource listed in [`var/agents/index.json`](../../var/agents/index.json).
- **Freshness-checked in CI** by the *Agent Artifacts Freshness* job in
  [`ci.yml`](../../.github/workflows/ci.yml) (runs `agent-regenerate --verify`).
  A stale tree **fails the build on `push` to `main`/`develop`**; on pull requests
  it is **advisory** (a warning that does not block the merge). The scheduled
  refresh does not commit to `main` — it publishes an
  `automation/agent-artifacts-refresh` branch that must be opened/merged as a PR to
  reconcile `main`.
- **Packaged/published** by the scheduled
  [`agent-artifacts-refresh.yml`](../../.github/workflows/agent-artifacts-refresh.yml).

Because all ten groups are registered resources in `index.json`, none can be
dropped from the committed tree without also updating the index, the validator
contract, and the freshness gate.

### Verdict per resource group

| Resource (generator) | Committed files | Verdict |
|----------------------|-----------------|---------|
| `schemas`, `models`, `logging`, `observability`, `configuration`, `validation`, `broker`, `health` | small JSON/MD inventories | **Keep committed.** Derived truth read as agent context and validated/packaged; low churn. |
| `testing` (`generate_test_inventory.py`) | none | **Fully regenerate-on-demand.** All four inventories (`index.json`, `markers.json`, `test_inventory.json`, `source_test_map.json`) are gitignored `optional_files` regenerated via `uv run agent-regenerate`; decision accepted 2026-07-04 in [stop-committing-high-churn-agent-inventories](../decisions/stop-committing-high-churn-agent-inventories.md) (#1130). |
| `reasoning` (`generate_reasoning_artifacts.py`) | 8 curated `.md` maps | **`.md` committed; `.json`/`.dot` machine forms gitignored.** The `.md` summaries are curated in [reasoning_artifacts.md](reasoning_artifacts.md); the machine forms regenerate on nearly any `src/**` change, so they are regenerate-on-demand `optional_files`. |

### Churn verdict — executed 2026-07-01 (owner-approved)

The advisory/generated-only treatment deferred by the original audit (#1086)
was executed with explicit owner approval on 2026-07-01. The
`reasoning/*.{json,dot}` machine forms and `testing/source_test_map.json` now
mirror the `test_inventory.json` precedent:

- **gitignored** and regenerated on demand (`uv run agent-regenerate`);
- registered as `optional_files` in `var/agents/index.json`, so
  `agent-artifacts validate`/`package` accept their absence in a fresh checkout
  but still ship them in the refresh package when present;
- excluded from the freshness gate by rule (`agent-regenerate --verify` drops
  gitignored outputs from both sides of the diff, per #1072).

The curated `reasoning/*.md` summaries remain committed and freshness-gated.
