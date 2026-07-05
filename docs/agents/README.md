# Agent Docs Index

---
status: current
---

Use this folder for AI-focused navigation aids and generated inventories.

## Core References

- [Agent workflow (canonical)](../../AGENTS.md)
- [Codebase map](CODEBASE_MAP.md)
- [Reasoning artifacts](reasoning_artifacts.md)
- [Recurring project review pipeline](project_review_pipeline.md)
- Scratch logs: [Project regrounding 2026-06-28](../../var/agents/scratch_logs/project_regrounding_20260628.md), [runtime architecture refactor 2026-06-29](../../var/agents/scratch_logs/runtime_architecture_refactor_20260629.md)
- [Glossary](glossary.md)
- [CLI conventions](conventions.md)
- [Environment variables](../../var/agents/configuration/environment_variables.md)
- [Metrics catalog](../../var/agents/observability/metrics_catalog.md)
- [Naming patterns config](../../config/agents/naming_patterns.yaml)
- [Naming scan tool](../../scripts/agents/naming_inventory.py)
- Testing inventories: `index.json`, `markers.json`, `test_inventory.json`,
  `source_test_map.json` (all gitignored; regenerate with
  `uv run agent-regenerate --only testing`)

## Tooling

- [Tooling helpers](../../scripts/agents/README.md)

## Generated context (`var/agents/**`)

`var/agents/**` is derived truth: generated inventories agents read as
context, never hand-edited. The scope contract:

- **Produced by** [`regenerate_all.py`](../../scripts/agents/regenerate_all.py)
  (`uv run agent-regenerate`), one generator per resource group registered in
  [`var/agents/index.json`](../../var/agents/index.json).
- **Committed vs regenerate-on-demand:** the small, low-churn inventories
  (`schemas`, `models`, `logging`, `observability`, `configuration`,
  `validation`, `broker`, `health`) and the curated `reasoning/*.md` summaries
  are committed. High-churn machine forms are gitignored `optional_files`
  regenerated on demand: the entire `testing/` group
  ([decision](../decisions/stop-committing-high-churn-agent-inventories.md),
  #1130) and the `reasoning/*.{json,dot}` machine forms.
- **Validated by** [`agent_artifacts.py`](../../scripts/agents/agent_artifacts.py)
  (`uv run agent-artifacts validate` / `package` / `verify-package`).
  `optional_files` may be absent on a fresh checkout but are validated when
  present and shipped in the refresh package after regeneration.
- **Freshness-checked** by the *Agent Artifacts Freshness* CI lane
  (`agent-regenerate --verify`): blocking on pushes to `main`/`develop`,
  advisory on pull requests. Gitignored outputs are excluded from the
  comparison by rule, so the gate covers only the committed set.
- **Packaged/published** by the scheduled
  [`agent-artifacts-refresh.yml`](../../.github/workflows/agent-artifacts-refresh.yml)
  workflow, which regenerates, validates, uploads a packaged bundle, and
  publishes changed generated files to the
  `automation/agent-artifacts-refresh` branch (it never commits to `main`
  directly).

Regenerate locally with:

```bash
uv run agent-regenerate
uv run agent-regenerate --only testing
```
