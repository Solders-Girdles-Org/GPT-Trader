# AGENTS.md — Start here

Entry point for every agent, and a fine one for humans. It routes and states
the gates; each fact lives in one owning document, linked below. State each
fact once; link, don't copy.

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

Python 3.12 and `uv`. Setup: `uv sync --all-extras --dev`, then
`cp config/environments/.env.template .env` (`MOCK_BROKER=1` runs without
credentials).

```bash
uv run pytest tests/unit -n auto -q      # unit tests
uv run ruff check . --fix && uv run black .
uv run mypy src/gpt_trader
uv run agent-naming                      # naming standard (also a pre-commit hook)
uv run local-ci                          # the PR gate; make ci-required is an alias
uv run local-ci --profile quick          # skips readiness and the slower suites
uv run agent-pr-ready                    # real mergeability vs green checks
```

## Trading-safety boundary

Live profiles and broker adapters are implementation assets, not approval.
Live order submission requires recorded human approval plus any scoped
decision packet; verify venue, API and account capability before adding or
enabling an execution path. [docs/DIRECTION.md](docs/DIRECTION.md) is the
authority. The standing merge approval below never covers live orders or
execution enablement.

## Merge discipline

`main` is protected and merges go through the merge queue. Merging carries
standing operator approval (2026-07-02): no per-PR sign-off is needed once the
readiness gate passes, and opening a PR is not merging. Before merging,
resolve every review thread, re-read current-head review and reaction
signals, and run `uv run agent-pr-ready`. Green CI is not sufficient; merge
only when it reports ready.

```bash
git switch -c <branch>
git push -u origin HEAD
gh pr create --fill            # fill .github/pull_request_template.md; Closes #<n>
gh pr merge --squash --auto    # enqueues; the queue validates against latest main
```

Prefer independent PRs over stacks; if you must stack, merge base-first
(branch auto-delete can close a child whose base vanished; restore the branch
from the merge SHA, reopen, retarget). `mergeStateStatus: BLOCKED` on a ready
PR is the queue, not a failure. `scripts/ci/check_branch_protection.py` owns
the expected protection contract; drift surfaces as an `agent-pr-ready`
warning.

## Handoff between agents

Codex and Claude exchange work through the shared [handoff contract](docs/HANDOFF-CONTRACT.md) (mirror of `Workspace Operations/HANDOFF-CONTRACT.md`): code handoffs are the pull request with a six-field packet, reviews are posted to GitHub, and this file's gates still govern. Manual paste between agents is not the handoff.

## Hosted agents

A hosted agent (Jules or similar) bootstraps with `uv sync --all-extras --dev`,
a `.env` copied from the template with `MOCK_BROKER=1` and `DRY_RUN=1`, then
`uv run pytest tests/unit -n auto -q`. Hosted agents run on mock defaults only.
