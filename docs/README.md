# Documentation Index

---
status: current
---

All docs under `docs/` must be reachable from this index (directly or via other
linked docs). When adding a doc, first read
[Information Architecture](INFORMATION_ARCHITECTURE.md) — it is the governing map
of where each kind of fact lives — then link the new doc in the best-fit section
below.

## Doc Layout and Metadata

Each doc carries a frontmatter block with a single required field:

```yaml
---
status: current   # current | draft | deprecated | superseded
---
```

Decision records under [decisions/](decisions/README.md) use the same field to
carry their lifecycle (`proposed | accepted | rejected | superseded`).

There is intentionally **no `last-updated` field**: it drifted out of sync with
reality and gave a false signal. **Git history is the source of truth for when a
doc changed** (`git log -1 -- docs/<file>.md`); `status` is the human signal git
cannot provide. The metadata block and reachability are enforced by
`scripts/maintenance/docs_reachability_check.py`.

## Start Here

0. **[Information Architecture](INFORMATION_ARCHITECTURE.md)** - Where every fact lives; the rule that prevents doc bloat. Read before adding docs.
1. **[Project Status](STATUS.md)** - Where we actually are: shipped reality, right now
2. **[Direction](DIRECTION.md)** - Where we're going: the staged ladder and the gates to get there
3. **[Architecture](ARCHITECTURE.md)** - Understand vertical slices before touching code
4. **[Development Guidelines](DEVELOPMENT_GUIDELINES.md)** - Where to change things + guardrails
5. **[Readiness Checklist](READINESS.md)** - Gates to move from paper to live trading

## Quick Links

| Document | Purpose |
|----------|---------|
| [Information Architecture](INFORMATION_ARCHITECTURE.md) | Where every fact lives + the anti-bloat rule |
| [Project Status](STATUS.md) | Living "you are here": shipped state per stage |
| [Direction](DIRECTION.md) | Destination, staged ladder, and the gates before execution |
| [Decisions](decisions/README.md) | Durable product/engineering decisions — made and open |
| [Architecture](ARCHITECTURE.md) | System design and capabilities |
| [Architecture Boundaries](architecture/BOUNDARIES.md) | Layer ownership and dependency direction |
| [Ownership Map](architecture/OWNERSHIP.md) | Module ownership map and boundaries |
| [Entrypoints](architecture/ENTRYPOINTS.md) | CLI, preflight, and live bot wiring |
| [Live Operations Guide](production.md) | Readiness-gated live operations, rollback, and emergency procedures |
| [Readiness Checklist](READINESS.md) | Gates to move from paper to live trading |
| [Trade-Idea Interface Design Notes](specs/TRADE_IDEA_INTERFACES_DESIGN_NOTES.md) | Implemented CLI workstream for human-approved trade ideas |
| [Reliability Guide](RELIABILITY.md) | Guard stack, degradation responses, chaos testing |
| [Coinbase Integration](COINBASE.md) | Coinbase configuration + pointers |
| [Robinhood Integration](ROBINHOOD.md) | Read-only Crypto and Agentic capability pointers |
| [Naming Standards](naming.md) | Approved terminology and banned abbreviations |
| [Feature Flags](FEATURE_FLAGS.md) | Config precedence + canonical sources |
| [AI Agent Reference](../AGENTS.md) | Agent workflow + repo rules (canonical) |
| [Agent Docs Index](agents/README.md) | AI-focused maps and pointers |

## Core Documentation

### Getting Started
- [Testing Guide](testing.md) - Running and writing tests
- [Paper Trading](paper_trading.md) - Safe testing with mock broker

### Project Direction & Decisions
- [Information Architecture](INFORMATION_ARCHITECTURE.md) - Where each kind of fact lives; the anti-bloat rule
- [Direction](DIRECTION.md) - Destination, staged-autonomy ladder, and execution gates
- [Decisions](decisions/README.md) - Decision records (`proposed`/`accepted`), one per file
- [Project Status](STATUS.md) - Factual current state

### Architecture & Design
- [System Architecture](ARCHITECTURE.md) - Component overview and vertical slices
- [Architecture Boundaries](architecture/BOUNDARIES.md) - Layer ownership and dependency direction
- [DI Policy](DI_POLICY.md) - Dependency injection patterns and container usage
- [Entrypoints](architecture/ENTRYPOINTS.md) - CLI, preflight, and live bot wiring
- [Core Seams](architecture/SEAMS.md) - Canonical boundaries for Strategy/Execution/Data/Config

### Trading Operations
- [Live Operations](production.md) - Readiness-gated live operations, monitoring, rollback, emergencies
- [Readiness Checklist](READINESS.md) - Paper/live gate criteria and evidence
- [Direction](DIRECTION.md) - Gates before AI-assisted execution migration
- [Reliability Guide](RELIABILITY.md) - Degradation matrix, config defaults, chaos harness
- [Monitoring Playbook](MONITORING_PLAYBOOK.md) - Metrics, alerting, and dashboards
- [Alert Runbooks](RUNBOOKS.md) - Incident response procedures by alert type
- [Observability Reference](OBSERVABILITY.md) - Metrics, traces, and structured logging

### Coinbase Integration
- [Coinbase Integration](COINBASE.md) - Configuration + internal entrypoints

### Robinhood Integration
- [Robinhood Integration](ROBINHOOD.md) - Authenticated observation and non-binding preview/review boundaries

### Development
- [Development Guidelines](DEVELOPMENT_GUIDELINES.md) - Standards for contributing, cleanup passes, and the verification bundle
- [Feature Slice Scaffolding](DEVELOPMENT_GUIDELINES.md#slice-scaffolding) - Bootstrap new slices
- [Test Hygiene Policy](test_hygiene.md) - Line limits, allowlist policy, and splitting guidance
- [Naming Standards](naming.md) - Terminology, casing, and banned abbreviations
- [Naming Suppressions](naming_suppressions.md) - Strict mode behavior and how to suppress
- [Security](SECURITY.md) - Security practices and considerations
- [Deprecations](DEPRECATIONS.md) - Deprecated modules and shims (CI-checked)
- Research backtests — adapter order-intent keys are documented in [Development Guidelines](DEVELOPMENT_GUIDELINES.md)

## Configuration

- [Strategy Profile Diff](STRATEGY_PROFILE_DIFF.md) - Compare baseline strategy settings against runtime profile values

### Trading Profiles

Profiles are config snapshots, not execution approval. Live profiles
(`canary`, `prod`) only run after the gates in
[Live Operations](production.md) and [Direction](DIRECTION.md) are satisfied with
recorded human approval. `canary`/`prod` remain live-operation assets under
that gating — accepted in
[prod-canary-profile-meaning](decisions/prod-canary-profile-meaning.md); the
profile name itself is never execution approval.

| Profile | Broker / data | Role |
|---------|---------------|------|
| **dev** | Mock broker | Development and testing |
| **paper** | Real data, mock execution | Strategy checks without exchange orders |
| **observe** | Real data, blocked execution | Account and market observation |
| **canary** | Live broker, tightly capped | Live-validation asset; runs require recorded approval |
| **prod** | Live broker | Live-operation asset; runs require explicit approval and monitoring |

### Environment Setup
- [Environment Template](../config/environments/.env.template) - Minimal operator config (safe defaults)
- [Environment Variable Inventory](../var/agents/configuration/environment_variables.md) - Full, code-derived reference
- Default: Spot trading with JWT authentication
- CFM futures (US) require `TRADING_MODES=cfm` + `CFM_ENABLED=1`
- INTX perps were removed ([decision record](decisions/intx-default-derivatives-venue.md)); `COINBASE_ENABLE_INTX_PERPS` survives only as a deprecated alias — semantics in [Deprecations](DEPRECATIONS.md)

## Additional Resources

- [Risk Integration](RISK_INTEGRATION_GUIDE.md) - Risk management configuration
- [PnL Calculations](PNL_CALCULATION_DIFFERENCES.md) - Profit/loss methodology

## Important Notes

### Spot vs Perpetuals

These flags are capability selectors, not approval. Enabling a flag exposes the
relevant adapter; live execution still requires the gates in
[Live Operations](production.md) and venue/account verification.

| Mode | Products | Authentication | Flag |
|------|----------|----------------|------|
| **Spot (default capability)** | BTC-USD, ETH-USD, etc. | JWT (CDP key) | `TRADING_MODES=spot` |
| **CFM futures (US)** | US futures contracts (expiry-coded symbols) | JWT (CDP key) | `TRADING_MODES=cfm` + `CFM_ENABLED=1` |
| **INTX perps** | Removed — `-PERP`-suffixed symbols coerce to spot | — | Removed; see [decision record](decisions/intx-default-derivatives-venue.md) |

**Note:** Sandbox does not support futures/perps. Default capability is spot only.

### Current Focus
- **Primary implementation:** Coinbase spot adapters, plus a CFM futures adapter gated on account access and approval (INTX perpetuals were removed; see [Deprecations](DEPRECATIONS.md))
- **Architecture**: Vertical slice design under `src/gpt_trader/`
- **Testing**: `uv run pytest`

## Getting Help

- **Architecture**: [ARCHITECTURE.md](ARCHITECTURE.md)
- **AI Development**: [AGENTS.md](../AGENTS.md) and [Agent Docs Index](agents/README.md)
- **Issues**: GitHub Issues

---

*Prefer code + generated inventories over long-lived how-to docs. See
[Information Architecture](INFORMATION_ARCHITECTURE.md) for what belongs where.*
