# Contributing to GPT-Trader

Code quality and consistency are enforced by automated checks. Set up your
environment as described below so your contributions pass them on the first try.
(AI agents: start at [AGENTS.md](AGENTS.md), which routes to this doc for the
contribution workflow.)

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
3. Run the full local gate: `uv run local-ci` — the command set and
   blocking/advisory contract are owned by
   [docs/DEVELOPMENT_GUIDELINES.md](docs/DEVELOPMENT_GUIDELINES.md#local-ci-command).
4. Execute slice-specific suites relevant to your change set (examples below).

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
- **Coverage**: the current number is the `coverage-json` artifact uploaded by the CI **Unit Tests (Core)** job; run `make cov` for a local snapshot. Target >90% on new code paths.
- **Integration paths**: coordinate with maintainers before toggling derivatives gates.

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
8. **Open a Pull Request** summarising risk impact, telemetry changes, and
   rollout steps

## Issue Labels

Labels mark **exceptions, not categories**. An unlabeled open issue is ordinary
ready work — specified well enough to pick up, sequenced by conversation with
the owner, not by taxonomy. The full label set:

| Label | Meaning |
| --- | --- |
| `agent-ready` | Validated, self-contained spec; an agent can execute it without further human input |
| `decision-needed` | Requires an explicit decision packet and owner call before implementation |
| `blocked` | Sequenced behind a named dependency (name it in the issue body) |
| `trading-safety` | Touches trading safety controls or the execution boundary ([docs/DIRECTION.md](docs/DIRECTION.md)) |
| `agent-review` | Provenance: produced by the recurring agent review lane |
| `bug`, `enhancement`, `documentation`, `duplicate`, `dependencies` | GitHub/Dependabot defaults |

Deferred work is **closed with a comment**, not labeled: closed issues are the
searchable archive, and reopening is cheap. Do not add labels beyond this set
without first retiring this model — label taxonomies decay (the 45-label
predecessor, including the `triage:*` scheduler, was retired 2026-07-02 because
it had stopped being applied).

Use the **Task** issue form when filing new work: it carries the same
Summary / Evidence / Scope / Acceptance / Verification structure the agent
promoter emits, so hand-written and promoted issues share one contract.

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
uv run pytest tests/unit/gpt_trader/features/live_trade/test_degradation_pause_expiry_and_recovery.py::TestPauseExpiryRecovery -v

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

## Quality Gate and CI Contract

The local verification command set, the CI-lane table, and the
blocking/advisory contract are owned by
[`docs/DEVELOPMENT_GUIDELINES.md`](docs/DEVELOPMENT_GUIDELINES.md#continuous-integration)
— including the canonical `uv run local-ci` gate to run before opening a PR
and the common-CI-failure fixes. This doc intentionally does not restate
commands.
