# Agent Tooling Helpers

Two entrypoints, wired through `src/gpt_trader/agents/cli.py` and the
`[project.scripts]` table in `pyproject.toml`.

```bash
uv run agent-naming                  # naming scan (non-strict)
uv run agent-naming --strict --quiet # the pre-commit `naming-check` hook
uv run agent-pr-ready --format markdown
```

`agent-naming` dispatches to `scripts/agents/naming_inventory.py`; defaults are
loaded from `config/agents/naming_patterns.yaml`. Strict naming enforcement is
wired through the local pre-commit `naming-check` hook; GitHub CI does not run
a direct naming scan step.

`agent-pr-ready` dispatches to `scripts/agents/pr_readiness.py` and reconciles
a PR's real mergeability (required checks, merge state, unresolved review
threads, branch-protection drift) against "CI is green".

## Reference Docs

- `AGENTS.md`
- `docs/agents/README.md`
