# Development Guidelines

---
status: current
---

The one workflow document: setup, local verification, the CI contract, the PR
flow, conventions and where to change things. Agent gates (merge discipline,
trading-safety boundary) live in [AGENTS.md](../AGENTS.md); where facts live is
in [Information Architecture](INFORMATION_ARCHITECTURE.md).

## Setup

Python 3.12 and `uv`.

```bash
uv sync --all-extras --dev
cp config/environments/.env.template .env   # MOCK_BROKER=1 runs without credentials
pre-commit install                          # ruff, black, pyupgrade, naming, test hygiene
```

## Verify before a PR

`uv run local-ci` is the local gate (`make ci-required` is an alias). Its
default `pr` profile matches the GitHub `pull_request` required checks:
lint/format, docs audits, type check, test guardrails, core unit tests plus the
Stage 1 rails smoke, and the property, contract and integration suites.
`--profile quick` skips the readiness gate and the slower suites; `--profile
strict` adds the canary readiness gate (`scripts/ci/check_readiness_gate.py`)
as local/live evidence beyond the PR surface.

The individual commands:

```bash
uv run ruff check . --fix && uv run black .
uv run mypy src/gpt_trader
uv run pytest tests/unit -n auto -q
uv run agent-naming
uv run python scripts/ci/check_import_boundaries.py
uv run python scripts/maintenance/docs_link_audit.py
uv run python scripts/maintenance/docs_reachability_check.py
uv run python scripts/maintenance/docs_currency_scan.py --fail-on missing,stale
uv run python scripts/maintenance/generate_decision_index.py --check
```

Run the docs commands whenever you touch `docs/`, the decision index check
when you touch `docs/decisions/`, and `scripts/ci/check_deprecation_registry.py`
when you add a deprecation shim.

### CI contract

`.github/workflows/*.yml` and branch protection are the executable truth;
`scripts/ci/check_branch_protection.py` fails when live settings drift.

| Check | Status |
| --- | --- |
| `CI` / Lint & Format, Docs Link Audit, Type Check, Test Guardrails, Unit Tests (Core), Property Tests, Contract Tests, Integration Tests | Required by `main` protection; also run on `merge_group` |
| `CI` / Windows Unit Tests, Dependency Review, `CodeQL`, `UV Lock Upgrade` | Advisory |
| `Release Image` (version-tag push) and `Integration Tests (Manual)` | Outside the merge gate; neither deploys, moves money or submits orders |

Merges go through the merge queue, which re-validates each entry against the
latest `main`, so strict up-to-date is off. Conversation resolution is
required.

### Local CI troubleshooting

The readiness gate applies only to the `strict` profile and direct readiness
checks. When it reports a missing or stale report, refresh the inputs with
`make canary-daily` (for another profile: `uv run gpt-trader report daily
--profile <profile> --report-format both`, then `make preflight-readiness` and
`make readiness-window` with `PREFLIGHT_PROFILE=<profile>` and
`READINESS_REPORT_DIR=runtime_data/<profile>/reports`), then rerun
`uv run python scripts/ci/check_readiness_gate.py --profile <profile>`. Raise
`GPT_TRADER_READINESS_MAX_REPORT_AGE_DAYS` when your cadence exceeds seven
days; `GPT_TRADER_READINESS_STRICT=1` turns degraded into failed. The gate
also reads `runtime_data/<profile>/events.db` and the status file. Inputs and
freshness windows: [READINESS.md](READINESS.md#readiness-gate-inputs--stale-data-interpretation).

Formatting and lint failures are fixed by the commands above. An import error
on a removed path means use the canonical path in
[DEPRECATIONS.md](DEPRECATIONS.md).

## PR flow

1. Branch from current `main`; `uv sync` to pick up dependency changes.
2. Write tests with the change. Unit tests mirror `src/` paths under
   `tests/unit/`; property, contract and integration suites have their own
   folders and markers. Use `monkeypatch`, never patch-style helpers; use the
   `fake_clock` fixture instead of `time.sleep`; keep test modules under the
   line limit in [test_hygiene.md](test_hygiene.md). Detail:
   [testing.md](testing.md).
3. Run `uv run local-ci`.
4. Open the PR with `.github/pull_request_template.md` filled in; link the
   issue with `Closes #<n>`; state risk impact and behaviour changes.
5. Merge per [AGENTS.md](../AGENTS.md#merge-discipline).

Issue labels mark exceptions, not categories: `agent-ready`, `decision-needed`,
`blocked` (name the dependency in the body), `trading-safety`, `agent-review`,
plus the GitHub defaults. An unlabeled open issue is ordinary ready work;
deferred work is closed with a comment. Do not add labels beyond this set.
File new work with the Task issue form.

## Conventions

- Ruff and Black defaults, line length 100; `pathlib.Path` for files;
  structured logging through `gpt_trader/logging` (`configure_logging`).
- Type annotations on public interfaces; `typing.Protocol` for guard and
  strategy contracts. Names follow [naming.md](naming.md).
- Raise domain exceptions (`src/gpt_trader/features/live_trade/guard_errors.py`
  or the slice's own); never swallow them, so guards can respond. Log with
  symbol, profile and guard name.
- Wiring: `ApplicationContainer` for the retained runtime, explicit
  dependencies for the recorded experiment ([DI_POLICY.md](DI_POLICY.md)).
  Import across slices through surface modules;
  `scripts/ci/check_import_boundaries.py` enforces the edges.
- Refactor one seam at a time behind a stable facade; the acceptance signal is
  behaviour tests for the moved responsibility, not line counts. The
  [recorded-product decision](decisions/recorded-experiment-product.md) allows
  replacing an obsolete local workflow and its tests together.
- Note CFM/`us_futures` gating when you touch derivatives-resident paths
  ([decision](decisions/intx-default-derivatives-venue.md)). Coordinate with
  the operator before changing risk guard thresholds or order routing.

## Where to change things

| Intent | Start here |
| --- | --- |
| Add a strategy | `src/gpt_trader/features/live_trade/strategies/`, register in `src/gpt_trader/features/live_trade/factory.py` |
| Add a runtime guard | `src/gpt_trader/features/live_trade/execution/guards/`, register in `src/gpt_trader/features/live_trade/execution/guard_manager.py` |
| Add a pre-trade validation | `src/gpt_trader/features/live_trade/execution/validation.py` and `src/gpt_trader/features/live_trade/engines/strategy.py` |
| Change order submission | `src/gpt_trader/features/live_trade/execution/order_submission.py` and `src/gpt_trader/features/live_trade/execution/broker_executor.py` |
| Change risk rules | `src/gpt_trader/features/live_trade/risk/manager/__init__.py` and `src/gpt_trader/features/live_trade/risk/config.py` |
| Add a config field | `src/gpt_trader/app/config/bot_config.py` (bot) or `src/gpt_trader/features/live_trade/risk/config.py` (risk), plus `config/environments/.env.template` |
| Change degradation | `src/gpt_trader/features/live_trade/degradation.py` |
| Add a health check | `src/gpt_trader/monitoring/health_checks.py` |
| Add a Coinbase endpoint | `src/gpt_trader/features/brokerages/coinbase/client/` and `src/gpt_trader/features/brokerages/coinbase/endpoints.py` |
| Add a slice | `scripts/maintenance/feature_slice_scaffold.py --name <slice>` (`--with-tests`, `--with-readme`, `--dry-run`) |

`TradingBot.flatten_and_stop()` in `src/gpt_trader/features/live_trade/bot.py`
and `src/gpt_trader/features/optimize/` intentionally bypass the guard stack.

## Retiring things

Delete and rely on git history; record removals in
[DEPRECATIONS.md](DEPRECATIONS.md) and fix inbound links. Keep a compatibility
shim only on purpose, with a target in the registry. An unsettled behaviour
question becomes a `proposed` [decision](decisions/README.md), not a drive-by
change.
