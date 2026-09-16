# Script Taxonomy

This directory contains repo tooling, operational probes, generated-artifact
helpers, and local analysis scripts. Runtime application behavior should live
under `src/gpt_trader/`; scripts should stay thin, explicit entrypoints around
that code.

Run repo scripts through `uv run python ...` unless a Makefile target or console
script already wraps them.

## Directory Map

| Path | Purpose | Examples |
|------|---------|----------|
| `scripts/ops/` | Operator-facing probes and runbook helpers for live or canary workflows. These may inspect runtime state or exercise guarded operator flows. | liveness checks, canary process control, product catalog probes, readiness-window reports |
| `scripts/analysis/` | Offline analysis, demos, backtests, benchmarks, and regression probes. These should not submit live orders. | backtest runner, paper stress test, guard parity regression |
| `scripts/monitoring/` | Monitoring exporters, dashboards, and canary observation harnesses. These should read runtime data or emit metrics, not own core execution. | Prometheus exporter, perps dashboard, reduce-only canary probe |
| `scripts/ci/` | Deterministic checks used by CI, local CI, pre-commit hooks, or Makefile quality gates. | legacy-pattern checks, deprecation registry checks |
| `scripts/maintenance/` | Repo hygiene, docs audits, scaffolding, and workspace cleanup tools. These are maintainer utilities, not trading-system runtime paths. | docs link audit, docs reachability check, feature-slice scaffold |
| `scripts/agents/` | Agent-facing commands behind the `agent-*` entry points. | naming scan, PR readiness reconciliation |

## Root Exceptions

Root-level scripts are exceptions, not the default:

| Path | Why It Stays Root |
|------|-------------------|
| `scripts/production_preflight.py` | Stable operator preflight entrypoint used by docs, Makefile targets, and readiness workflows. |
| `scripts/__init__.py` | Package marker for importable script modules in tests and helper code. It is not a command entrypoint. |

New root scripts should be avoided. Add them under the appropriate taxonomy
directory unless they are intentionally promoted to a stable top-level operator
entrypoint and documented here.

## Placement Rules

- Put operator runbook probes under `scripts/ops/`.
- Put offline experiments, backtests, benchmarks, and demos under `scripts/analysis/`.
- Put exporters, dashboards, and observation harnesses under `scripts/monitoring/`.
- Put deterministic gate checks under `scripts/ci/`.
- Put docs, cleanup, and scaffolding utilities under `scripts/maintenance/`.
- Put generated-inventory and AI-agent helpers under `scripts/agents/`.
- Put script tests under `tests/unit/scripts/`; avoid `test_*.py` command scripts under `scripts/`.

## Move Checklist

When moving, deleting, or renaming a script:

1. Update Makefile targets, docs, tests, and `src/gpt_trader/ci/local_ci.py`.
2. Update direct imports in `tests/unit/scripts/`.
3. Adjust any direct-execution path shim that depends on `Path(__file__).resolve().parents[...]`.
4. Search for stale path references with `rg "old/script/path.py"`.
5. Run the focused checks for the changed area.
