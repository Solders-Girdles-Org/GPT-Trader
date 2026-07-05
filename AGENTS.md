# AGENTS.md — Start Here for AI Agents

This is the **first stop** for any AI coding agent (and a fine one for humans).
It routes; it does not restate policy. Each row below points at the one doc that
owns that fact — read that doc for detail, and change facts there, not here.

Two rules keep this repo from sprawling:

1. **State each fact once; link, don't copy.** The authority on where every kind
   of fact lives is [docs/INFORMATION_ARCHITECTURE.md](docs/INFORMATION_ARCHITECTURE.md).
2. **Opening a PR is not merging.** Merge is a separate, later, explicitly
   approved step (see [Merge discipline](#merge-discipline)).

## Where do I go?

| I need to… | Canonical home |
|------------|----------------|
| Decide **where a fact/doc should live** | [docs/INFORMATION_ARCHITECTURE.md](docs/INFORMATION_ARCHITECTURE.md) |
| Find **where code lives / where to change something** | [docs/agents/CODEBASE_MAP.md](docs/agents/CODEBASE_MAP.md) |
| Understand the **system design** (slices, order pipeline) | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Browse the **full doc index** | [docs/README.md](docs/README.md) |
| Know the **project direction, autonomy boundary, execution gates** | [docs/DIRECTION.md](docs/DIRECTION.md) |
| See **current shipped state** | [docs/STATUS.md](docs/STATUS.md) |
| Follow the **contribution workflow** (setup, PR checklist, test quality) | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Understand **local CI, the verification bundle, and the CI-lane contract** | [docs/DEVELOPMENT_GUIDELINES.md](docs/DEVELOPMENT_GUIDELINES.md) |
| Apply **naming standards + approved abbreviations** | [docs/naming.md](docs/naming.md), [docs/agents/glossary.md](docs/agents/glossary.md) |
| Use **dependency injection** (`ApplicationContainer`) | [docs/DI_POLICY.md](docs/DI_POLICY.md) |
| Write or run **tests** | [docs/testing.md](docs/testing.md) |
| Run the **agent review/scout pipeline** or handle review artifacts | [docs/agents/project_review_pipeline.md](docs/agents/project_review_pipeline.md) |
| Find **generated inventories/maps** (env vars, metrics, flows) | `var/agents/**` + [docs/agents/README.md](docs/agents/README.md) |

## Environment (one time)

Python **3.12**, package manager **uv**. Full setup and troubleshooting live in
[CONTRIBUTING.md](CONTRIBUTING.md); the short version:

```bash
uv sync --all-extras --dev
cp config/environments/.env.template .env   # set MOCK_BROKER=1 to run without credentials
```

## Everyday commands

The commands you reach for on almost every task (the verification command set
and CI contract are owned by
[docs/DEVELOPMENT_GUIDELINES.md](docs/DEVELOPMENT_GUIDELINES.md); the
contribution workflow by [CONTRIBUTING.md](CONTRIBUTING.md)):

```bash
uv run pytest tests/unit -n auto -q     # fast unit tests
uv run ruff check . --fix               # lint (auto-fix)
uv run black .                          # format
uv run mypy src/gpt_trader              # type check
uv run agent-naming                     # naming conventions
uv run local-ci                         # full local PR gate (make ci-required = alias)
uv run local-ci --profile quick         # faster loop (skips readiness, artifacts, optional suites)
```

## Before you open a PR

- Run `uv run local-ci` (lint/format, docs audits, type check, advisory
  agent-artifact freshness, test guardrails, unit/property/contract/integration
  tests). The blocking/advisory contract is
  owned by [docs/DEVELOPMENT_GUIDELINES.md](docs/DEVELOPMENT_GUIDELINES.md).
- If your change can affect generated `var/agents/**` context, run
  `uv run agent-regenerate` and commit the updated artifacts; confirm with
  `uv run agent-regenerate --verify`. The exact freshness/CI contract (which
  inputs count and where it blocks vs. warns) lives in
  [docs/DEVELOPMENT_GUIDELINES.md](docs/DEVELOPMENT_GUIDELINES.md) and the CI
  classifier.
- Fill out [.github/pull_request_template.md](.github/pull_request_template.md);
  link the issue/finding with `Closes #<n>` when there is one.

## Merge discipline

`main` is protected. Merging carries standing operator approval (2026-07-02):
no per-PR sign-off is needed once the readiness gate passes. Before merging:
re-read current-head review/reaction signals, resolve every review thread, and
confirm generated artifacts are fresh. **Green CI is not sufficient** — run
`uv run agent-pr-ready`, which reconciles real mergeability against green
checks, and merge only when it reports ready.

```bash
git switch -c <branch>
git push -u origin HEAD
gh pr create --fill
# Once agent-pr-ready reports ready and all threads are resolved, enqueue;
# the merge queue validates against latest main and merges when green:
gh pr merge --squash --auto
```

Standing approval covers PR merges only — live order submission and execution
enablement still require recorded human approval (see the trading-safety
boundary below and [docs/DIRECTION.md](docs/DIRECTION.md)).

Merge mechanics that repeatedly bite agents (`agent-pr-ready` detects all three):

- **Prefer independent PRs over stacks.** Branch auto-delete on merge can close
  a child PR whose base branch just vanished. Recovery: restore the deleted
  branch from the merge SHA, reopen the child, retarget it. If you must stack,
  merge base-first.
- **Merges go through the merge queue** (#1127, 2026-07-04). Strict up-to-date
  is off: the queue re-validates each entry against the latest `main` via a
  `merge_group` CI run, so green PRs no longer invalidate each other. GitHub
  reports `mergeStateStatus: BLOCKED` for direct merges even on ready PRs;
  `gh pr merge --squash --auto` enqueues instead of merging directly.
- **The protection contract is machine-checked.** `scripts/ci/check_branch_protection.py`
  owns the expected required checks/settings; drift between it and live GitHub
  settings surfaces as an `agent-pr-ready` warning.

## Trading-safety boundary

Existing live profiles and broker adapters are implementation assets, **not**
approval to automate. Live order submission requires recorded human approval plus
any scoped decision packet; verify venue/API/account capability before adding or
enabling an execution path. The authority is [docs/DIRECTION.md](docs/DIRECTION.md);
findings route through [docs/agents/project_review_pipeline.md](docs/agents/project_review_pipeline.md).

## Hosted-agent setup (Google Jules)

Paste this into the Jules "Initial Setup" window. It configures `.env` with safe
mock defaults, then runs the core unit suite:

```bash
set -euo pipefail

curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv python install 3.12
uv sync --all-extras --dev

test -f .env || cp config/environments/.env.template .env
uv run python -c "import re; from pathlib import Path; p=Path('.env'); t=p.read_text(); t=re.sub(r'^MOCK_BROKER=.*$','MOCK_BROKER=1',t,flags=re.M); t=re.sub(r'^DRY_RUN=.*$','DRY_RUN=1',t,flags=re.M); p.write_text(t)"

uv run pytest tests/unit -n auto -q
```

If you override env via Jules repo settings, use `MOCK_BROKER=1` and `DRY_RUN=1`
(and set `PYTHONWARNINGS=default`, not `1`, if you set it at all).
