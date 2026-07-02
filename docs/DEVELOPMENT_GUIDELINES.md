# Development Guidelines (gpt_trader)

---
status: current
---

These guidelines cover contributions to the readiness-gated `gpt_trader` stack. Older
guides from the pre-DI era were removed from the tree; use git history if you
need to review historical practices.

## Architectural Principles

- **Vertical slices**: Add features within `src/gpt_trader/features/<slice>/` and
  keep cross-slice coupling minimal.
- **Explicit wiring**: Register new dependencies in `ApplicationContainer`
  (`src/gpt_trader/app/container.py`) instead of hidden imports. See
  `docs/DI_POLICY.md` for detailed guidance on when to use container vs
  singletons.
- **Public surfaces**: Prefer importing across slices/tests via surface modules
  (e.g., `gpt_trader.security.validate`, `gpt_trader.features.intelligence.contracts`)
  instead of deep/internal modules. Add new exports to the surface when needed.
- **Configuration-first**: Extend `BotConfig` when new runtime options are
  required; expose overrides through the CLI when appropriate.
- **Modular refactoring**: Extract large modules (>500 lines) into focused
  subpackages or module-local collaborators with clear separation of concerns.
  See `features/live_trade/execution/`,
  `features/live_trade/risk/`, and the `features/live_trade/engines/`
  collaborators (telemetry, equity, order-record mapping) as examples. Decompose
  one reviewable seam at a time:
  - Keep the public class/import stable as a **facade**; move logic behind
    private collaborators (free functions or classes) that it delegates to.
  - Extract the **lowest-risk seam first** — pure, IO-free helpers before
    stateful or async ones.
  - The acceptance signal is **behavior tests for the moved responsibility**,
    not line counts (line counts are supporting evidence only).

## Slice Scaffolding

- Use `scripts/maintenance/feature_slice_scaffold.py --name <slice>` to bootstrap new
  vertical slices under `src/gpt_trader/features/<slice>/`.
- Add `--with-readme` and `--with-tests` so documentation and unit tests live beside
  the slice (`tests/unit/gpt_trader/features/<slice>/`).
- Use `--dry-run` for previews; the scaffold tool refuses overwrites by design.
- Keep slice names snake_case, prefer explicit imports, and avoid cross-slice
  dependencies.
- Use the [script taxonomy](../scripts/README.md) when adding or moving repo
  tooling under `scripts/`.

## Where to Change Things

| Intent | Start Here |
|--------|------------|
| Add a new trading strategy | `src/gpt_trader/features/live_trade/strategies/` + register in `src/gpt_trader/features/live_trade/factory.py` |
| Add a new runtime guard | `src/gpt_trader/features/live_trade/execution/guards/` + register in `src/gpt_trader/features/live_trade/execution/guard_manager.py` |
| Add a new pre-trade validation | `src/gpt_trader/features/live_trade/execution/validation.py` + `src/gpt_trader/features/live_trade/engines/strategy.py` |
| Change order submission behavior | `src/gpt_trader/features/live_trade/execution/order_submission.py` + `src/gpt_trader/features/live_trade/execution/broker_executor.py` |
| Modify risk rules | `src/gpt_trader/features/live_trade/risk/manager/__init__.py` + `src/gpt_trader/features/live_trade/risk/config.py` |
| Add a new env config field | `src/gpt_trader/app/config/bot_config.py` (bot-level) or `src/gpt_trader/features/live_trade/risk/config.py` (risk manager) + `config/environments/.env.template` |
| Modify degradation behavior | `src/gpt_trader/features/live_trade/degradation.py` |
| Add/modify a health check | `src/gpt_trader/monitoring/health_checks.py` |
| Add a Coinbase REST/WS endpoint | `src/gpt_trader/features/brokerages/coinbase/client/` + `src/gpt_trader/features/brokerages/coinbase/endpoints.py` |

## Intentional Guard-Stack Bypasses

The canonical order path routes through `TradingEngine._validate_and_place_order()` (live loop),
with `TradingEngine.submit_order()` as the external wrapper. The following locations intentionally
bypass guards:

| Location | Purpose |
|----------|---------|
| `src/gpt_trader/features/live_trade/bot.py` (`TradingBot.flatten_and_stop()`) | Emergency position closure (must succeed even during risk trips) |
| `src/gpt_trader/features/optimize/` | Optimization/backtesting flows using simulated brokers |

## Code Style

- Python 3.12 with Ruff + Black defaults (line length 100).
- Type annotations for public interfaces; prefer `typing.Protocol` for guard or
  strategy contracts.
- Prefer `pathlib.Path` for filesystem access.
- Use structured logging via `gpt_trader/logging` helpers (call `configure_logging`)
  whenever a new module emits logs.

## Error Handling

- Raise domain-specific exceptions from `src/gpt_trader/features/live_trade/guard_errors.py`
  or define new ones in the relevant slice.
- Avoid swallowing exceptions; propagate up to the trading engine/guard manager so guard rails
  can respond.
- Provide actionable log messages (symbol, profile, guard name, etc.).

## Testing

- Place unit tests under `tests/unit/gpt_trader/` mirroring the module path.
- Use fixtures for Coinbase mocks (`tests/fixtures/brokerages/` when available).
- Run `uv run pytest -q` locally before submitting a pull request.
- Add regression coverage for new guard conditions, telemetry counters, or CLI
  flags.
- **Subpackage testing**: When refactoring into subpackages, ensure each
  submodule has independent test coverage. Maintain backward compatibility by
  keeping facade modules (e.g., `risk/__init__.py`) that re-export the public
  API.

## Continuous Integration

This section is the compact contributor-facing CI contract. The executable
source of truth remains `.github/workflows/*.yml` plus GitHub branch protection.
Current `main` branch protection requires only the named `CI` contexts listed in
the first row below, with strict up-to-date checks and conversation resolution
enabled. Selected context-specific lanes self-skip by changed path while keeping
those check names stable.

| Check / workflow job | Tier | Trigger | Blocking status | Why it exists |
| --- | --- | --- | --- | --- |
| `CI` / `Lint & Format`, `Docs Link Audit`, `Type Check`, `Test Guardrails`, `Unit Tests (Core)`, `Property Tests`, `Contract Tests` | Required merge safety | `pull_request`, `merge_group`, push to `main`/`develop`, manual | Required by `main` branch protection | Fast repo integrity, docs reachability, type checks, and core test coverage |
| `CI` / `Agent Health` | Advisory PR health | Same as `CI` | Not branch-protection required | Publishes an agent-health report without defining merge eligibility |
| `CI` / `Integration Tests` | Advisory PR health | Same as `CI` | Not branch-protection required | Runs the mock-broker integration suite (`tests/integration`, excluding `slow`/`real_api`) on every PR; needs no secrets |
| `CI` / `Agent Artifacts Freshness` | Generated artifact advisory on PR; blocking outside PR | Same as `CI` | Not branch-protection required; exits successfully with a warning on `pull_request`, fails on non-PR events when stale | Shows when `var/agents/**` needs regeneration without stalling ordinary PRs |
| `CI` / `Windows Unit Tests (Portability)` and `Dependency Review` | Event/compatibility advisory | Windows follows `CI`; dependency review is `pull_request` only | Not branch-protection required | Covers Windows-sensitive units and high-severity dependency changes |
| `CodeQL` / `Analyze Python` | Scheduled/security advisory | Push/PR to `main`/`develop`; weekly Monday 06:00 UTC | Not branch-protection required | GitHub code scanning |
| `Agent Artifacts Refresh` / `Refresh, validate, and publish package`, `Verify uploaded package`; `UV Lock Upgrade` / `Upgrade uv.lock` | Scheduled/advisory maintenance | Scheduled and manual | Not branch-protection required; may publish a branch or PR | Keeps generated agent artifacts and dependency lock maintenance visible |
| `Release Image` / `Build, Publish, and Scan Docker Image` | Release image publication/readiness | Version-tag push (`v*`) or manual run with a `release_note` reference | Outside the PR merge gate; publishes and scans images only | Builds, publishes, and scans Docker images; does not deploy staging/production, rollback, run canary/prod preflight, call broker/API commands, move money, or submit orders |
| `Integration Tests (Manual)` / `Integration Suite (Mock Broker)` | Manual readiness | Manual | Outside the PR merge gate | Full mock-broker integration suite including `slow` tests; needs no secrets or live-broker access and does not grant live trading, canary, or order authority |

The default PR workflow keeps required merge-safety check names stable for branch
protection, but several context-specific lanes now self-skip when their inputs
do not change. `Agent Artifacts Freshness` runs for agent artifact source or
output inputs, and `Dependency Review` runs for dependency manifest changes.

### Local CI Command

For a fail-fast entrypoint that matches the local PR-readiness command set, run:

```bash
make ci-required
```

It runs lint/format, docs audits, mypy, agent artifacts freshness, test
guardrails, and core unit tests, stopping on the first failure. Use
it when you want the local PR-readiness surface without optional suites or local
readiness evidence. Agent artifacts freshness is advisory here: stale artifacts
warn without stopping the run, while non-PR GitHub CI remains the blocking
enforcement point.

Run the local CI command when you want the same local PR-readiness set plus
the repository's optional local profile controls:

```bash
uv run local-ci
```

This covers the same core validation categories used around PRs: lint + format,
docs audits, mypy, agent artifacts freshness, test guardrails, and core unit
tests. Profile-specific local checks can still differ from GitHub pull_request
enforcement, especially around readiness evidence.

The command accepts `--profile`/`-p` to select either the default strict/full
profile or the quick/dev profile. Strict (the default and the `full` alias) runs
the local PR-readiness validation set, keeps agent artifacts freshness enabled as
an advisory warning, and adds the canary readiness gate as local/live readiness
evidence. GitHub pull_request CI and `make ci-required` do not enforce that
canary readiness gate.
The CLI prints the active profile plus the status of the readiness gate and
agent-artifacts checks before executing any steps. Quick (aliased as `dev`)
intentionally disables those two checks so you can run local CI without needing
readiness reports or regenerating `var/agents`; the output still documents which
checks were skipped and why.

| Command | Intended use | Agent artifacts freshness | Readiness gate |
| --- | --- | --- | --- |
| `make ci-required` | Local PR-readiness validation surface | Advisory, non-blocking | Not run |
| GitHub `pull_request` CI | GitHub PR validation in Actions | Path-conditional; non-blocking if stale when run | Not run |
| `uv run local-ci` / strict/full | Local PR-readiness validation plus local/live readiness evidence | Advisory, non-blocking | Runs `scripts/ci/check_readiness_gate.py --profile canary --strict` |
| `uv run local-ci --profile quick` | Fast development loop | Skipped with an explicit banner reason | Skipped with an explicit banner reason |

Optional suites:

```bash
uv run local-ci --include-property-tests
uv run local-ci --include-contract-tests
uv run local-ci --include-agent-health
```

For quick loops you can explicitly request the dev profile:

```bash
uv run local-ci --profile quick
uv run local-ci --profile dev
```

Need help diagnosing `uv run local-ci` failures? See the [Local CI troubleshooting](#local-ci-troubleshooting) steps below.

### Local CI troubleshooting

Local CI (`make ci-required` / `uv run local-ci`) can report issues before the
unit tests run. Stale agent artifacts are advisory in local runs and should be
regenerated before merge; readiness gate inputs can still fail strict/full
`uv run local-ci`. The readiness gate applies to strict/full `uv run local-ci`
and direct readiness checks, not to `make ci-required` or GitHub pull_request CI.
When you hit one of these findings, follow the sequence below before re-running
the command.

#### 1. Agent artifacts freshness

1. Run `uv run agent-regenerate` from the repo root to redraw `var/agents/**` from their sources.
2. Stage the updated artifacts and rerun `uv run agent-regenerate --verify`.
3. If the warning persists, compare `git status var/agents` and resolve any upstream conflicts in the source inputs under `scripts/agents/**` or
   `config/environments/.env.template` before regenerating again.

#### 2. Readiness gate staleness

1. Local CI runs the readiness gate with `PREFLIGHT_PROFILE=canary` and `READINESS_REPORT_DIR=runtime_data/canary/reports`. Check the `scripts/ci/check_readiness_gate.py` or `uv run local-ci` output for `Readiness gate degraded …` (missing report) or `Readiness gate degraded: latest report … is X days old` errors.
2. Generate fresh inputs for your profile (`canary` by default) by running `make canary-daily`, which creates a fresh daily report, `preflight_report_*.json`, and readiness window state. For other profiles, use `uv run gpt-trader report daily --profile <profile> --report-format both` plus `READINESS_REPORT_DIR=runtime_data/<profile>/reports PREFLIGHT_PROFILE=<profile> make preflight-readiness` and `make readiness-window PREFLIGHT_PROFILE=<profile>`.
3. If the gate complains about report age, regenerate the report and optionally raise `GPT_TRADER_READINESS_MAX_REPORT_AGE_DAYS` (or pass `--max-report-age-days`) when your cadence is longer than the default 7 days; add `--strict` or set `GPT_TRADER_READINESS_STRICT=1` if you want the gate to fail instead of degrade.
4. Rerun `uv run python scripts/ci/check_readiness_gate.py --profile <profile>` or `uv run local-ci` to confirm the gate now sees the refreshed data.
5. The gate also reads `runtime_data/<profile>/events.db` for liveness and `var/data/status.json` (or your configured status file), so ensure those files exist alongside the report directory before rerunning local CI.
6. For more background on the required files, freshness windows, and how stale data is interpreted, see [Readiness gate inputs & stale-data interpretation](READINESS.md#readiness-gate-inputs--stale-data-interpretation).

### Agent Artifacts Freshness

The **Agent Artifacts Freshness** check verifies generated inventories under
`var/agents/**` are up to date with their sources. It is blocking for non-PR
GitHub CI events. Locally (`make ci-required` and strict/full `uv run local-ci`)
and on GitHub pull requests it reports stale artifacts as a non-blocking advisory
warning, so ordinary loops and PRs are not stalled by the scheduled refresh lane.
When you see the advisory warning, regenerate the artifacts and commit the results
before merge (non-PR CI enforces it).

```bash
uv run agent-regenerate
uv run agent-regenerate --verify
```

Regeneration should update files in `var/agents/**`; stage and commit those
changes in your PR.

### Resolving Generated Artifact Conflicts

If merge conflicts appear in `var/agents/**`, avoid hand-editing the generated
files. Resolve conflicts in the source inputs first (for example, under
`scripts/agents/**`, `config/environments/.env.template`, or related code), then
regenerate the artifacts.

Recommended flow:

```bash
# 1) Resolve conflicts in the source inputs.
# 2) Clear conflict markers from generated files (choose a side or delete them).
uv run agent-regenerate
uv run agent-regenerate --verify # optional verification
```

Stage the regenerated `var/agents/**` outputs and include them in the same PR
as the source changes.

## Documentation

- Update `docs/ARCHITECTURE.md`, `docs/RISK_INTEGRATION_GUIDE.md`, or other
  relevant guides whenever behaviour changes.
- Note CFM/`us_futures` gating whenever derivatives-resident code paths are touched (INTX perpetuals were removed; see [decision record](decisions/intx-default-derivatives-venue.md)).
- Keep agent-facing references (`AGENTS.md`, `docs/agents/CODEBASE_MAP.md`, and generated `var/agents/**`) aligned with new workflows.

## Operational Hygiene

- Validate new behaviour with `uv run gpt-trader run --profile dev --dev-fast`.
- Confirm metrics output updates when telemetry changes (`metrics.json`).
- Coordinate with operations before altering risk guard thresholds or order
  routing.
- Preview stale Codex worktrees under `/tmp/gpt-*` with `python scripts/maintenance/cleanup_worktrees.py`.
- Add `--apply` to remove the worktrees and delete their local branches.
- Only `codex/*` or `issue/*` branches with missing upstream remotes are eligible.

## Cleanup Passes

Cleanup work removes drift, clarifies the canonical path, or surfaces a behavior
decision that must happen before more automation is added. It is not a feature
backlog — track cleanup candidates as GitHub issues, not in a doc.

- Keep each pass small enough to verify and commit independently. Start from a
  clean working tree and end with `git status --short --branch` showing only the
  branch line.
- Prefer removing or rehoming stale surfaces before rewriting core behavior.
- Treat broker/profile availability as implementation state, not product
  approval. Consult [DIRECTION.md](DIRECTION.md) before adding or enabling
  execution paths.
- Do not preserve compatibility shims only because they exist: keep them
  intentionally, deprecate them with a target in [DEPRECATIONS.md](DEPRECATIONS.md),
  or remove them with tests.
- Keep generated inventories current with `uv run agent-regenerate --verify` when
  a pass moves, removes, or changes generated-artifact inputs.
- A pass that uncovers an unsettled behavior question records it as a `proposed`
  decision in [decisions/](decisions/README.md), not as a drive-by change.

Prefer this verification bundle after passes that touch docs, scripts, config, or
generated-artifact inputs:

```bash
git status --short --branch
uv run ruff check .
uv run python scripts/ci/check_legacy_patterns.py
uv run python scripts/ci/check_deprecation_registry.py
uv run python scripts/maintenance/docs_link_audit.py
uv run python scripts/maintenance/docs_reachability_check.py
uv run python scripts/maintenance/generate_decision_index.py --check
uv run agent-regenerate --verify
git diff --check
```

## Submitting Changes

1. Create a descriptive branch.
2. Implement code + tests + docs.
3. Run `uv run pytest -q` and any targeted integration scripts.
4. Open a pull request summarising risk impact, telemetry changes, and rollout
   steps.

Legacy contribution guides were removed from the tree; if you need to review
them, pull from repository history. Do not base new development on those
documents.
