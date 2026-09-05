# GPT-Trader

An agent-developed, Coinbase-oriented trading system on a staged path toward bounded autonomy.

[![CI](https://github.com/Solders-Girdles-Org/GPT-Trader/actions/workflows/ci.yml/badge.svg)](https://github.com/Solders-Girdles-Org/GPT-Trader/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

## Overview

GPT-Trader helps RJ inspect trading decisions through a reproducible local
experiment: recorded bars become explained decisions, bounded simulated fills,
and reconciled cash/positions. The current benchmark is fixed-rule arithmetic;
AI assists development but does not generate its market decisions.

[Direction](docs/DIRECTION.md) owns the longer-term autonomous destination and
external execution gates. [Status](docs/STATUS.md) points to shipped behavior;
the [paper guide](docs/paper_trading.md#recorded-experiment) explains operation
and limitations. Retained broker/runtime assets are distinct from the local
product and from approval to trade.

## Quick Start

```bash
uv sync --all-extras --dev
uv run gpt-trader experiment run --input config/experiments/ma-crossover-demo.json --root runtime_data/experiments/demo
uv run gpt-trader experiment inspect --root runtime_data/experiments/demo
```

The fixture is explicitly synthetic. It demonstrates mechanics and accounting,
not historical market performance. No credentials or running service are needed.
Resume a stopped run with the same `run --root ...` command, omitting `--input`;
use `--max-bars 55` to pause after a bounded number of additional observations.
Ctrl-C stops the foreground process; a new process resumes the last commit.

## Configuration

The experiment input carries the recorded source and simulation settings; its
journal binds a copy of them to the installed source. Changing either starts a
new experiment. See [the input and fill contract](docs/paper_trading.md#recorded-experiment).

Retained runtime profiles and credential configuration serve separately
selected, authorized operations. Their source is
[profile configuration](config/profiles/) and the
[environment template](config/environments/.env.template), with the
[generated inventory](var/agents/configuration/environment_variables.md).
Do not set up credentials or start a runtime to try the local product.

## Project Structure

```
src/gpt_trader/
├── app/                  # DI container (ApplicationContainer)
├── backtesting/          # Backtesting framework (canonical)
├── cli/                  # Command-line interface
├── features/             # Vertical feature slices
│   ├── experiment/       # Recorded-data decision and accounting loop
│   ├── brokerages/       # Coinbase REST/WebSocket integration
│   ├── data/             # Market data acquisition
│   ├── intelligence/     # Strategy intelligence, Kelly sizing
│   ├── live_trade/       # Production trading engine & risk
│   ├── optimize/         # Parameter optimization
│   ├── strategy_tools/   # Shared strategy helpers
│   └── trade_ideas/      # Broker-neutral trade-idea records + audit trail
├── monitoring/           # Runtime guards, metrics, telemetry
├── persistence/          # Event/order persistence
├── security/             # Secrets management, input sanitization
└── validation/           # Declarative validators
```

## Development

### Scaffold a New Slice

```bash
make scaffold-slice name=<slice> flags="--with-tests --with-readme"
```

Or run directly:

```bash
uv run python scripts/maintenance/feature_slice_scaffold.py --name <slice> --dry-run
```

### Quality Gates

```bash
# Linting and formatting
uv run ruff check . --fix
uv run black .

# Type checking
uv run mypy src/gpt_trader

# Run all pre-commit hooks
pre-commit run --all-files

# Check naming conventions
uv run agent-naming
```

### Testing

```bash
# Unit tests (fast, default)
uv run pytest tests/unit -q

# With coverage
uv run pytest tests/unit --cov=src/gpt_trader -q

# Property-based tests
uv run pytest tests/property -q
```

### Local CI Profiles

The canonical local validation command is `uv run local-ci`. Its default `pr`
profile matches the GitHub `pull_request` required-check surface — run it
before opening a PR (`make ci-required` is a thin alias). Use
`uv run local-ci --profile quick` for fast development feedback (skips
readiness inputs, agent-artifact freshness, and the
property/contract/integration suites, with explicit banners), and
`--profile strict` when you also need local-live readiness evidence.

When strict/full fails on stale generated artifacts, run
`uv run agent-regenerate` and then `uv run agent-regenerate --verify`. When it
fails on readiness inputs, refresh the canary inputs with `make canary-daily`
or follow the profile-specific commands in
[`docs/DEVELOPMENT_GUIDELINES.md`](docs/DEVELOPMENT_GUIDELINES.md#local-ci-troubleshooting).

### Test Guardrails

- Keep `test_*.py` modules within the line limit enforced by
  `scripts/ci/check_test_hygiene.py` (currently 400 lines unless allowlisted);
  policy details live in [docs/test_hygiene.md](docs/test_hygiene.md).
- Patch-style mocking is blocked in `tests/`; use `monkeypatch.setattr`.
- Avoid `time.sleep` in tests; use the `fake_clock` fixture for deterministic time.
- Marker conventions are enforced by folder (unit/integration/contract/real_api).

When you rename or move tests, regenerate the testing inventory:

```bash
uv run agent-regenerate --only testing
```

### Agent Tools

Commands for AI-assisted development. The canonical local quality gate is
`uv run local-ci` (`make ci-required` is a thin alias); these helpers are
optional conveniences on top of it:

```bash
uv run agent-check      # Optional JSON summary of lint/format/types/tests
uv run agent-impact     # Analyze change impact
uv run agent-map        # Generate dependency graph
uv run agent-naming     # Check naming conventions
uv run agent-pr-ready   # Reconcile PR mergeability vs green CI
```

## Documentation

| Document | Purpose |
|----------|---------|
| [Architecture](docs/ARCHITECTURE.md) | System design and vertical slices |
| [Direction](docs/DIRECTION.md) | Autonomy, product, venue, approval, and audit gates |
| [Project Status](docs/STATUS.md) | Shipped state, right now |
| [Reliability](docs/RELIABILITY.md) | Guard stack, degradation, chaos testing |
| [Monitoring](docs/MONITORING_PLAYBOOK.md) | Metrics, alerting, dashboards |
| [Live Operations](docs/production.md) | Readiness-gated live operations and rollback |
| [Contributing](CONTRIBUTING.md) | Development workflow |

Full documentation index: [docs/README.md](docs/README.md). AI agents start at
[AGENTS.md](AGENTS.md).

## Architecture Notes

This project uses **dependency injection** via `ApplicationContainer` in `src/gpt_trader/app/`. The legacy `orchestration/` layer was removed during the DI migration; prefer `app/` and `features/` paths.

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## License

MIT — see [LICENSE](LICENSE). Decision record:
[docs/decisions/adopt-mit-license.md](docs/decisions/adopt-mit-license.md).
