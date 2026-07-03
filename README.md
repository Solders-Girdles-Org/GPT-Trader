# GPT-Trader

An agent-developed, Coinbase-oriented trading system on a staged path toward bounded autonomy.

[![CI](https://github.com/Solders-Girdles/GPT-Trader/actions/workflows/ci.yml/badge.svg)](https://github.com/Solders-Girdles/GPT-Trader/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

## Overview

GPT-Trader is a command-line trading system for Coinbase, built as vertical feature slices behind a dependency-injection container, with layered risk management and an auditable trade-idea pipeline. The name reflects how AI assistants collaborate in developing this codebase; current trading strategies use technical analysis and rule-based decisioning, not LLM inference.

**Direction.** The long-term goal is an autonomous trading entity — a bot that observes markets, does its own research, and manages funds inside machine-enforced limits. The accepted path is staged autonomy: AI-produced trade-idea records with human approval first, then bounded autonomy per strategy envelope once the risk, audit, and kill-switch rails have a track record. [Direction](docs/DIRECTION.md) owns the staged ladder and the execution gates; [Project Status](docs/STATUS.md) tracks where we actually are.

**Scope.** Coinbase only, spot plus CFM futures. INTX perpetuals were removed, not frozen (see the [removal decision](docs/decisions/intx-default-derivatives-venue.md)). Existing live profiles and broker-specific paths are implementation assets, not approval to trade: expanding or enabling them requires explicit readiness, venue-capability, approval, and audit gates.

### Trading Capabilities

| Mode | Status | Description |
|------|--------|-------------|
| **Spot trading** | Implemented | Coinbase spot paths exist; use only with explicit profile and readiness gates |
| **CFM futures** | Implemented, gated | US-regulated futures paths exist; require account, product, and risk-gate verification |
| **INTX perpetuals** | Removed | `COINBASE_ENABLE_INTX_PERPS` survives only as a deprecated alias; semantics live in [Deprecations](docs/DEPRECATIONS.md) |
| **AI-assisted execution** | Staged rollout | Human-approved trade ideas first; bounded autonomy is the accepted destination ([current state](docs/STATUS.md)) |

## Quick Start

```bash
# Install dependencies
uv sync

# Run the trading bot
uv run gpt-trader run --profile dev
```

## Configuration

### Trading Profiles

| Profile | Broker | Use Case |
|---------|--------|----------|
| `dev` | DeterministicBroker (mock) | Local development |
| `paper` | Mock execution | Real-data strategy checks without exchange orders |
| `observe` | Real data, execution blocked | Read-only market/account observation |
| `canary` | Real (tiny limits) | Production validation only after readiness review |
| `prod` | Real | Legacy live profile; do not treat as approval for unrestricted production use |

### Environment Setup

Copy the template and configure your credentials:

```bash
cp config/environments/.env.template .env
```

Key variables:
- `COINBASE_CREDENTIALS_FILE`, or `COINBASE_CDP_API_KEY` + `COINBASE_CDP_PRIVATE_KEY` — JWT credentials
- `--profile` (CLI flag) — trading profile (`dev`/`paper`/`observe`/`canary`/`prod`)

See [config/environments/.env.template](config/environments/.env.template) for minimal operator defaults and
[var/agents/configuration/environment_variables.md](var/agents/configuration/environment_variables.md) for the full, code-derived inventory.

## Project Structure

```
src/gpt_trader/
├── app/                  # DI container (ApplicationContainer)
├── backtesting/          # Backtesting framework (canonical)
├── cli/                  # Command-line interface
├── features/             # Vertical feature slices
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

Three levels of local validation, from fastest to most thorough:

| Command | Use when |
|---------|----------|
| `uv run local-ci --profile quick` | Fast development feedback; skips readiness inputs and agent-artifact freshness (with explicit banners) |
| `make ci-required` | The local PR-readiness surface, including generated agent-artifact freshness — run before opening a PR |
| `uv run local-ci` | Strict/full runs that also need local-live readiness evidence before a PR handoff |

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
`make ci-required`; these helpers are optional conveniences on top of it:

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
