# Architecture Boundaries

---
status: draft
---

This document defines the current layering and dependency boundaries used during the cleanup
campaign. It is descriptive of the codebase as it exists today (not an aspirational target).

## Layers (inner to outer)

| Layer | Primary ownership | Notes |
|-------|-------------------|-------|
| Domain + shared foundations | `src/gpt_trader/core/`, `src/gpt_trader/errors/`, `src/gpt_trader/validation/`, `src/gpt_trader/config/`, `src/gpt_trader/utilities/`, `src/gpt_trader/logging/` | Types, math, error taxonomy, validation helpers, and shared utilities. |
| Interfaces (protocols) | `src/gpt_trader/app/protocols.py`, `src/gpt_trader/features/**/protocols.py` | Contracts for brokers, risk managers, and runtime services. Keep these import-only and light. |
| Shared configuration | `src/gpt_trader/app/config/` | `BotConfig` and profile loading. Imports strategy configs from `features/live_trade/strategies`. Used across layers as a shared input surface. |
| Feature slices (business logic) | `src/gpt_trader/features/` (live_trade, intelligence, data, optimize, strategy_dev, strategy_tools, trade_ideas) | Trading logic, strategies, guard stack, optimization workflows, trade-idea records. |
| Shared engines | `src/gpt_trader/backtesting/` | Canonical backtesting engine used by optimization slices. |
| Adapters + infrastructure | `src/gpt_trader/features/brokerages/`, `src/gpt_trader/persistence/`, `src/gpt_trader/monitoring/`, `src/gpt_trader/security/` | External integrations, IO, stores, telemetry (metrics/health/tracing), secrets. `features/brokerages` is an adapter slice. |
| App/runtime + entrypoints | `src/gpt_trader/app/`, `src/gpt_trader/cli/`, `src/gpt_trader/preflight/`, `scripts/production_preflight.py` | Composition root, config loading, runtime lifecycle, and operator entrypoints. |

### Configuration note

`BotConfig` and profile loading live in `src/gpt_trader/app/config/` and are imported by
features and adapters (for example broker factories and REST services). Treat config types as
shared inputs, but avoid importing the container or CLI layers from lower-level modules.

## Allowed dependency directions (imports)

- Domain + shared foundations depend only on stdlib and third-party packages.
- Interfaces depend on domain/shared foundations only.
- Feature slices may import domain/shared foundations, interfaces, and shared config types.
  Cross-slice imports should be rare; prefer `features/strategy_tools/` or `features/data/`
  for shared helpers.
- Adapters/infrastructure may import domain/shared foundations, interfaces, and shared config types.
  Avoid importing CLI/preflight or `app.container` from adapter code.
- App/runtime and entrypoints may import from all lower layers to wire dependencies.

## Public surfaces (stable import points)

A public surface is a small, import-only module that re-exports the stable API for a slice.
It is the only place other slices/tests should import from; everything else is internal
and may change.

Why we use them:
- Stabilize cross-slice imports while internals evolve.
- Reduce dependency churn in tests and tooling.
- Make boundary ownership explicit (the surface is the contract).

Rules of thumb:
- Keep surfaces thin: re-exports only, no IO, no container wiring.
- If another slice/test needs a symbol, add it to the surface instead of importing deep modules.
- Treat non-surface modules as private to the slice.

Examples in this repo:
- Security validation surface: `gpt_trader.security.validate` (facade over `src/gpt_trader/security/`).
- Intelligence surfaces: `gpt_trader.features.intelligence.regime` and
  `gpt_trader.features.intelligence.sizing` (import these for types like `RegimeType`,
  `RegimeConfig`, `PositionSizingConfig`).

## Import boundary guard

We enforce a lightweight import scan in `scripts/ci/check_import_boundaries.py` to keep
layering drift visible in CI. Current rules are intentionally small but cover the main
directional hazards:

- Feature slices (`src/gpt_trader/features/`) must not import entrypoint layers
  (`gpt_trader.cli`, `gpt_trader.preflight`) or `gpt_trader.app.container`.
- Infrastructure layers (`src/gpt_trader/monitoring/`, `src/gpt_trader/persistence/`,
  `src/gpt_trader/security/`) must not import those same entrypoint/container layers.

To extend the rule set:
- Add a new `ImportRule` entry in `scripts/ci/check_import_boundaries.py` with a clear name.
- Set `source_root` to the directory you want to scan.
- Add `forbidden_prefixes` for the module prefixes you want to block.
- Use `allowlist_files` or `allowlist_import_prefixes` sparingly for temporary exceptions.

## Decision tree: where to put new code

- New trading logic, strategy, execution guard, or risk rule? Add it under
  `src/gpt_trader/features/live_trade/` (or the appropriate feature slice).
- New optimization or backtesting workflow? Use `src/gpt_trader/features/optimize/`.
  Core backtesting engine changes belong in `src/gpt_trader/backtesting/`.
- New external integration (exchange, storage, telemetry, secrets)? Use
  `src/gpt_trader/features/brokerages/`, `src/gpt_trader/persistence/`,
  `src/gpt_trader/monitoring/`, or `src/gpt_trader/security/`.
- New operator workflow or wiring? Use `src/gpt_trader/app/` for wiring and config, and
  `src/gpt_trader/cli/` or `src/gpt_trader/preflight/` for entrypoints.
- New shared type, math, or helper used across slices? Prefer `src/gpt_trader/core/`,
  `src/gpt_trader/errors/`, `src/gpt_trader/validation/`, or `src/gpt_trader/utilities/`.
- Need a new contract for DI/testing? Add a protocol in `src/gpt_trader/app/protocols.py`
  or the relevant slice `protocols.py`.
