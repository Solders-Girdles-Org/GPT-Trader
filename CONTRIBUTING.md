# Contributing to GPT-Trader

We use a set of automated tools to ensure code quality and consistency. Please follow these steps for your development environment to ensure your contributions pass our automated checks.

## One-Time Setup

### 1. Install `pre-commit`

We use `pre-commit` to run checks before you commit your code. This helps catch issues early. You can install it using `pipx` (recommended) or `pip`.

**With `pipx` (Recommended):**
```bash
pipx install pre-commit
```

**With `pip`:**
```bash
pip install pre-commit
```

### 2. Install the Git Hooks

After installing `pre-commit`, you need to install the hooks into your local git repository.

```bash
pre-commit install
```

That's it! Now, every time you run `git commit`, the pre-commit hooks will run automatically. If they find any issues (like formatting errors), they may fix the files and abort the commit. In that case, just `git add` the modified files and run `git commit` again.

## Testing Requirements

### Current Expectations
- Keep the full test suite green.
- Add focused tests for every new feature or regression fix.
- Document any skips or deselections tied to legacy code paths.

### Pre-PR Verification Checklist
1. Review `docs/README.md` and prefer code + `var/agents/**` generated inventories for anything that drifts.
2. Refresh dependencies: `uv sync`.
3. Collect tests: `uv run pytest --collect-only`.
4. Run the full unit suite: `uv run pytest tests/unit -q`.
5. Execute slice-specific suites relevant to your change set (examples below).
6. Validate docs links: `make agent-docs-links` (runs `scripts/maintenance/docs_link_audit.py`).

### Recommended Commands

```bash
# Core suites
uv run pytest tests/unit/gpt_trader -q
uv run pytest tests/unit/gpt_trader/features/brokerages/coinbase -q
uv run pytest tests/unit/gpt_trader/features/live_trade -q

# Coverage snapshot
uv run pytest --cov=gpt_trader --cov-report=term-missing
```

### Test Metrics
- **Coverage**: 82.6% overall as of 2026-07-01 (`make cov`); the current number is the `coverage-json` artifact uploaded by the CI **Unit Tests (Core)** job. Target >90% on new code paths.
- **Integration Paths**: Coordinate with maintainers before toggling derivatives gates

## Running the Bot Locally

To run the spot trading bot for development, use the `gpt-trader` command (spot is the active trading path):

```bash
uv run gpt-trader run --profile dev --dev-fast
```

## Development Workflow

Before branching, make sure to:
- Review `docs/README.md` for the current doc index (and verify key claims in code).
- Sync with the latest `main`.
- Run `uv sync` to pick up dependency changes.

1. **Fork** the repository
2. **Create a feature branch** (`git checkout -b feature/amazing-feature`)
3. **Write tests** for your changes
   - Unit tests required for all new functions
   - Integration tests for API interactions
   - Must maintain 100% pass rate on active tests
4. **Run the test suite** to ensure nothing is broken
   - `uv run pytest --collect-only` to verify test discovery
   - `uv run pytest tests/unit/gpt_trader -q` must pass
   - No new test failures allowed
5. **Follow repository organization standards**
   - Place files in correct directories (see Repository Organization below)
   - Update documentation using consolidated structure
   - Add new documentation to appropriate `/docs` subdirectories
6. **Commit your changes** (`git commit -m 'Add amazing feature'`)
7. **Push to your fork** (`git push origin feature/amazing-feature`)
8. **Open a Pull Request**

## Issue Labeling for AI Routing

Issue labels drive repository issue routing for agents and humans. Keep them consistent and machine-friendly:

- **Type label (required):** one of `bug`, `enhancement`, `architecture`, `chore`, `documentation`, `tests`, `ci`, or `security`.
- **Area label(s) (required, 1–2):** one or two labels from the active domain set, e.g. `trade-ideas`, `live-trade`, `cli`, `mcp`, `reporting`, `runtime`, `monitoring`, `audit`, `closeout`, `broker-neutral`, `trading-safety`, or `coinbase`.
- **Optional detail labels:** use tags like `persistence`, `reliability`, `packaging`, `deploy`, `developer-experience`, `tech-debt`, etc. as needed.
- **Agent routing labels:** routing signals for the agent workflow; the detailed pipeline lives in [the agent review pipeline](docs/agents/project_review_pipeline.md#stage-4-queue).
  - `agent-review` — produced by the recurring GPT-Trader agent review lane
  - `agent-ready` — validated and ready for agent implementation (no decision/blocker gate)
  - `decision-needed` — requires an explicit decision packet and agent recommendation before implementation (the single decision-gate label; `needs-human-decision` was retired 2026-07-01)
  - `codex-review-feedback` — follow-up from Codex review comments or checks
  - `claw-candidate` — candidate for Claw implementation
- **Triage labels:** applied after an issue is sorted into the current queue shape.
  - `triage:build-now` — core spine; build now, unblocks other work
  - `triage:build-next` — valuable; build after the now-spine lands
  - `triage:blocked` — valuable but sequenced behind a dependency
  - `triage:defer` — premature or scope-creep; revisit later
- **`codex` label:** routing signal for the agent-review / Codex workflow. Issues in this lane (e.g. the `agent-review-finding` template) are **exempt** from the Type/Area requirements above — `codex` alone is sufficient.

For this repository, status/priority labels are intentionally deferred until they solve a real need. The default issue templates should already include the intended label shape so no extra manual steps are needed at issue creation.

## Quality Standards

### Code Quality
- Clean, readable code with meaningful variable names
- Adhere to [Naming Standards](docs/naming.md)
- Follow [CLI conventions](docs/agents/conventions.md) for command output, exit codes, and `CliResponse`
- Comprehensive docstrings for public functions
- Type hints where beneficial
- Maximum line length: 100 characters

### Test Quality
- Descriptive test names that explain what's being tested
- One assertion per test when possible
- Use fixtures for shared setup
- Use pytest `monkeypatch` for mocks; patch-style helpers are blocked in `tests/`
- Keep test modules <= 400 lines unless allowlisted by `scripts/ci/check_test_hygiene.py`
- Avoid `time.sleep`; prefer deterministic `fake_clock`
- Match marker conventions to folder (integration/contract/real_api)

### Resilience Testing

The `tests/fixtures/failure_injection.py` module provides deterministic failure simulation for testing retry logic, degradation behavior, and error handling without network calls or sleeps.

**Key Components:**

| Component | Purpose |
|-----------|---------|
| `FailureScript` | Scripted sequence of failures/successes |
| `InjectingBroker` | Wraps a broker to inject failures per-method |
| `no_op_sleep` | Instant sleep for deterministic timing |
| `counting_sleep` | Records sleep durations for backoff verification |

**Example Usage:**

```python
from tests.fixtures.failure_injection import FailureScript, InjectingBroker, counting_sleep

# Fail twice, then succeed
script = FailureScript.fail_then_succeed(failures=2)
injecting = InjectingBroker(mock_broker, place_order=script)

# Verify exponential backoff
sleep_fn, get_sleeps = counting_sleep()
executor = BrokerExecutor(broker=injecting, sleep_fn=sleep_fn)
executor.execute(order)
assert get_sleeps() == [0.5, 1.0]  # Exponential delays
```

**Running Resilience Tests:**

```bash
# Broker executor resilience tests
uv run pytest tests/unit/gpt_trader/features/live_trade/execution/test_broker_executor_resilience_*.py -v

# Degradation recovery tests
uv run pytest tests/unit/gpt_trader/features/live_trade/test_degradation.py::TestPauseExpiryRecovery -v

# Order submission idempotency test
uv run pytest tests/unit/gpt_trader/features/live_trade/execution/test_order_submission_flows.py::TestTransientFailureWithClientOrderIdReuse -v
```

All resilience tests run deterministically under `pytest -n auto`.

## Repository Organization

### Directory Structure Standards

The repository follows a standardized organization optimized for both human developers and AI agents:

#### Source Code & Configuration
- `/src/gpt_trader/` - Active trading system (vertical slice architecture)
- `/tests/` - Test files organized by component
- `/config/` - Configuration files, trading profiles, and templates
- `/scripts/` - Operational scripts organized by domain (see `scripts/README.md` for the full taxonomy):
  - `agents/` - AI-agent and generated-inventory helpers
  - `analysis/` - Offline analysis, demos, backtests, and regression probes
  - `ci/` - Deterministic checks used by CI and quality gates
  - `maintenance/` - Repo hygiene, docs audits, and scaffolding tools
  - `monitoring/` - Monitoring exporters, dashboards, and canary observation harnesses
  - `ops/` - Operator-facing probes and runbook helpers for live/canary workflows
  - Root `scripts/*.py` - Reserved for a small set of sanctioned entrypoints (see "Root Exceptions" in `scripts/README.md`); new root scripts should be avoided

#### Documentation Standards
- `/docs/` - Canonical, low-overhead documentation (prefer flat structure)
- `/docs/agents/` - AI-focused maps, inventories, and agent workflows

#### Archive Management
- Use git history for retired docs or scripts
- Record removals in `docs/DEPRECATIONS.md`

### File Placement Guidelines

#### New Documentation
[Information Architecture](docs/INFORMATION_ARCHITECTURE.md) governs where each
kind of fact lives — read it before adding a doc. In short:
- **Durable prose** (decisions, direction, architecture, standards): `/docs/` (keep flat unless a subdirectory is clearly justified)
- **Current state**: `docs/STATUS.md` (pointer-only) — not README or other prose
- **The work queue**: GitHub issues
- **Per-task plans, audits, scratch**: `work/` (gitignored) — never under `/docs/`
- **Agent rules**: `AGENTS.md` (canonical); `/docs/agents/` holds agent maps/inventories/workflows, not a second copy of the rules
- **Never**: archive directories or version-suffixed docs — retire by deleting and rely on git history

#### New Scripts
Place new scripts in the taxonomy directory that matches their purpose (see
`scripts/README.md`); avoid adding new root-level scripts.
- **Operator runbooks/probes**: `/scripts/ops/`
- **Analysis/Benchmarks**: `/scripts/analysis/`
- **CI/Validation**: `/scripts/ci/`
- **Monitoring**: `/scripts/monitoring/`
- **Maintenance**: `/scripts/maintenance/`
- **Automation/Agents**: `/scripts/agents/`

#### Deprecated Content
- Remove from repo and rely on git history
- Document removals in `docs/DEPRECATIONS.md`
- Update all internal references

### Naming Conventions
- **Documentation**: `category_topic.md` (lowercase, underscores)
- **Scripts**: `action_target.py` (clear purpose indication)
- **Directories**: `lowercase_names/` (descriptive, single purpose)

### Link Maintenance
- All documentation links must be functional
- Use relative paths within repository
- Update references when moving files
- Test links before submitting PRs

### Documentation Quality
- Current state lives in [`docs/STATUS.md`](docs/STATUS.md) (pointer-only) — update it there, not in README or other prose
- Document breaking changes in the PR body and the linked issue
- Include examples for complex features
- State each fact once and link to it (see [Information Architecture](docs/INFORMATION_ARCHITECTURE.md)); avoid duplicate process prose — agent rules belong in `AGENTS.md`

## Quality Gate Commands

Before submitting a PR, run these commands to catch issues early:

### Type Checking (mypy)

```bash
# Full type check
uv run mypy src/gpt_trader

# Check specific module (faster iteration)
uv run mypy src/gpt_trader/features/live_trade/execution/
```

**Common mypy issues:**
- Resolve new errors or document existing ones in the PR summary
- New code should pass strict type checking

### Unit Tests

```bash
# Fast parallel run (recommended)
uv run pytest tests/unit -n auto -q

# Verbose output for debugging
uv run pytest tests/unit -v --tb=short

# Run specific test file
uv run pytest tests/unit/gpt_trader/features/live_trade/execution/test_order_submission_flows.py -v

# Run with coverage
uv run pytest tests/unit --cov=src/gpt_trader --cov-report=term-missing
```

### Naming Standards Check

```bash
# Check for naming convention violations
uv run agent-naming
```

## Common CI Failures and Fixes

| Failure | Cause | Fix |
|---------|-------|-----|
| `black --check` | Formatting | Run `uv run black .` |
| `ruff check` | Linting violations | Run `uv run ruff check --fix .` |
| `mypy` errors | Type issues | Fix type annotations (pre-existing shim errors can be ignored) |
| Import error | Wrong module path | Use canonical paths (see `docs/DEPRECATIONS.md`) |
| Test using deprecated path | Patch targets shim | Update to patch canonical module directly |

### Full Quality Gate

Run the full PR-readiness gate before opening a PR:

```bash
make ci-required
```

For quick iteration the commands below cover the most common failures, but they
are **not** a full substitute for `make ci-required` (which also runs docs
audits, `agent-regenerate --verify`, and test guardrails):

```bash
uv run ruff check .
uv run black --check .
uv run mypy src/gpt_trader
uv run pytest tests/unit -n auto -q
pre-commit run --all-files
```

## CI Contract

The compact blocking/advisory CI contract lives in
[`docs/DEVELOPMENT_GUIDELINES.md`](docs/DEVELOPMENT_GUIDELINES.md#continuous-integration).
Use that section for branch-protection context names, blocking status, and
workflow semantics. Keep this section as a local command quick-reference, not a
second CI-lane table.

### Local Command Quick Reference

Makefile shortcuts:
- `make lint` (ruff check + black --check)
- `make lint-fix` (ruff check --fix)
- `make lint-fmt-fix` (ruff check --fix + black)
- `make fmt` (black)
- `make fmt-check` (black --check)

```bash
# Lint lane
uv run ruff check . && uv run black --check .

# Type check lane
uv run mypy src

# Test guardrails
uv run python scripts/ci/check_test_hygiene.py

# Unit tests
uv run pytest tests/unit -n auto -q

# Property tests
uv run pytest tests/property -v

# Contract tests
uv run pytest tests/contract -v
```

## Pre-commit Hook Configuration

Our pre-commit hooks enforce:
- **Black**: Code formatting (line length 100)
- **Ruff**: Fast Python linting
- **MyPy**: Type checking (optional types)
