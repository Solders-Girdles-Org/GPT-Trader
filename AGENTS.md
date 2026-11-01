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

Agents merge their own PRs under RJ's standing policy in the shared
[handoff contract](https://github.com/Solders-Girdles/workspace-operations/blob/main/HANDOFF-CONTRACT.md#what-needs-review);
every PR body carries its `Reserved:` and `Self-check:` lines. A change that
can place, change or cancel a trade, or touches a broker or exchange account
or its keys, is RJ's (reserved item e). `agent-pr-ready` flags trading and
broker paths ([trigger](src/gpt_trader/agents/trading_trigger.py)): declare
`Reserved: e - <effect>` and pass `--rj-authorized-head <sha>` once RJ
authorizes that head, or keep `Reserved: none` with a clean cross-family
`Review:` line for the current head confirming no such effect.

Prefer independent PRs over stacks; if you must stack, merge base-first
(branch auto-delete can close a child whose base vanished; restore the branch
from the merge SHA, reopen, retarget). `mergeStateStatus: BLOCKED` on a ready
PR is the queue, not a failure. `scripts/ci/check_branch_protection.py` owns
the expected protection contract; drift surfaces as an `agent-pr-ready`
warning.

## Releases and images

`Release Image` ([workflow](.github/workflows/deploy.yml)) publishes and scans a
GHCR image; it does not deploy, call a broker or move money. Agents may run it,
or push the `v*` tag that starts it, under RJ's standing
[publishing rule](https://github.com/Solders-Girdles/workspace-operations/blob/main/docs/agents/linear.md#access-automation-and-limits)
once CI passes on that commit. A manual run's `release_note` names the card;
record the tag or digest and run link there. Deploying stays RJ's.

## Hosted agents

A hosted agent (Jules or similar) bootstraps with `uv sync --all-extras --dev`,
a `.env` copied from the template with `MOCK_BROKER=1` and `DRY_RUN=1`, then
`uv run pytest tests/unit -n auto -q`. Hosted agents run on mock defaults only.
