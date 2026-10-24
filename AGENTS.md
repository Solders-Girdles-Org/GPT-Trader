# AGENTS.md — Start here

Each fact has one owner below. Link to it rather than copying it.

## Read first

| Need | Owner |
| --- | --- |
| Destination, autonomy ladder, execution gates | [docs/DIRECTION.md](docs/DIRECTION.md) |
| Durable decisions, made and open | [docs/decisions/](docs/decisions/README.md) |
| Deprecations and compatibility commitments | [docs/DEPRECATIONS.md](docs/DEPRECATIONS.md) |
| How the system is built, where evidence lives | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Shipped state, as source pointers | [docs/STATUS.md](docs/STATUS.md) |
| Setup, local CI, PR flow, where to change things | [docs/DEVELOPMENT_GUIDELINES.md](docs/DEVELOPMENT_GUIDELINES.md) |
| Where a fact or doc belongs | [docs/INFORMATION_ARCHITECTURE.md](docs/INFORMATION_ARCHITECTURE.md) |
| Naming standard | [docs/naming.md](docs/naming.md) |
| Full doc index | [docs/README.md](docs/README.md) |

Inventories (env vars, metrics, events, schemas) are read from the code with
`rg -n` under `src/gpt_trader/`. Tests mirror source paths under `tests/unit/`.

## Everyday commands

Python 3.12 and `uv`. Use the setup in
[development guidelines](docs/DEVELOPMENT_GUIDELINES.md); `MOCK_BROKER=1` needs no
credentials.

```bash
uv run pytest tests/unit -n auto -q      # unit tests
uv run ruff check . --fix && uv run ruff format .
uv run mypy src/gpt_trader
uv run agent-naming                      # naming standard (also a pre-commit hook)
uv run local-ci                          # the PR gate; make ci-required is an alias
uv run local-ci --profile quick          # skips readiness and the slower suites
uv run agent-pr-ready                    # real mergeability vs green checks
```

## Trading-safety boundary

Verify venue, API and account capability before adding or enabling an
execution path. [docs/DIRECTION.md](docs/DIRECTION.md) owns the autonomy
ladder and what an execution lane needs.

## Merge discipline

`main` is protected and merges go through the merge queue. Opening a PR is
not merging. Before merging, resolve every review thread, re-read
current-head review and reaction signals, and run `uv run agent-pr-ready`.
Green CI is not sufficient; merge only when it reports ready. Enqueue a squash
merge with auto-merge after readiness passes.

Prefer independent PRs over stacks; if you must stack, merge base-first
(branch auto-delete can close a child whose base vanished; restore the branch
from the merge SHA, reopen, retarget). `mergeStateStatus: BLOCKED` on a ready
PR is the queue, not a failure. `scripts/ci/check_branch_protection.py` owns
the expected protection contract; drift surfaces as an `agent-pr-ready`
warning.

## Hosted agents

A hosted agent (Jules or similar) bootstraps with `uv sync --all-extras --dev`,
a `.env` copied from the template with `MOCK_BROKER=1` and `DRY_RUN=1`, then
`uv run pytest tests/unit -n auto -q`. Hosted agents run on mock defaults only.
