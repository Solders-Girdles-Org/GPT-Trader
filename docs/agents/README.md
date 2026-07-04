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
- [Canonical sources: doctrine & generated-artifact scope](canonical_sources.md)
- [Environment variables](../../var/agents/configuration/environment_variables.md)
- [Metrics catalog](../../var/agents/observability/metrics_catalog.md)
- [Naming patterns config](../../config/agents/naming_patterns.yaml)
- [Naming scan tool](../../scripts/agents/naming_inventory.py)
- Testing inventories: `index.json`, `markers.json`, `test_inventory.json`,
  `source_test_map.json` (all gitignored; regenerate with
  `uv run agent-regenerate --only testing`)

## Tooling

- [Tooling helpers](../../scripts/agents/README.md)

## Regeneration

Most inventories are generated via:

```bash
uv run agent-regenerate
uv run agent-regenerate --only testing
```

Validate the generated tree and its upload package before publishing:

```bash
uv run agent-artifacts validate
uv run agent-artifacts package
uv run agent-artifacts verify-package
```

The scheduled **Agent Artifacts Refresh** workflow regenerates `var/agents/**`,
validates the expected files and content, uploads a packaged artifact bundle,
downloads that bundle in a second job, and publishes changed generated files to
the `automation/agent-artifacts-refresh` branch.
