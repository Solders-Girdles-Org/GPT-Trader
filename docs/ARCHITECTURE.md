# System Architecture

---
status: current
---

## Product boundary

The owner-facing product is a reproducible recorded-data trading experiment.
[The design decision](decisions/recorded-experiment-product.md) owns the rationale,
reuse/replacement comparison and component disposition. [Direction](DIRECTION.md)
owns the autonomous destination and external authority. [Status](STATUS.md)
points at shipped source and tests; this document defines structure, not deployment.

The experiment has three responsibilities: validate evidence, decide and simulate
one observation, and commit/reconcile its effects. They run in one process with
explicit inputs. No broker, scheduler, model API or live account is involved.

```mermaid
flowchart LR
    A[Recorded hourly bars and settings] --> B[Validate and bind source]
    B --> C[Closed history to benchmark proposal]
    C --> D[Next bar admission and simulated fills]
    D --> E[Atomic observation journal]
    E --> F[Independent fill accounting and operator report]
    E --> C
```

An observation is the transaction boundary, not a multi-agent workflow stage.
A signal uses only completed history; its earliest fill is the next bar's open.
That bar may then close the position under the disclosed conservative OHLC model.
The same committed entry contains observed data, decision, fills, cash/inventory,
fees, controls and resume checkpoint. History is append-only. Duplicate resumes
advance only missing observations. A changed source implementation, input or
setting requires a separate experiment. Inspection opens the journal read-only.

Cash and net results are reconciled against identified fills using the shared
core position projector. Replay additionally checks every stored transition,
including pending plans and drawdown state. Reports are derived from a consistent
snapshot; they are not another state owner. Simulation timestamps encode the
assumed event ordering, not observed exchange fill times.

## Composition and configuration

The CLI is a thin adapter over the experiment library. The input document owns
its data and settings; the journal preserves their bound copy. There are no
profile/environment overrides and no default path into an operational store.
The library takes an explicit root and dependencies, so the offline loop does
not need `ApplicationContainer`, global registration or service lifecycle.
[DI policy](DI_POLICY.md) distinguishes this from the retained runtime.

The default benchmark is fixed indicator arithmetic. A proposal's historic
sizing recommendation is advisory; simulation admission recomputes size from
current cash and costs. Model generation is not implemented by naming an actor
AI, and historical replay cannot validate a trained model's alpha. The
[agentic-alpha decision](decisions/adopt-agentic-alpha-direction.md) owns those
future evaluation constraints.

## Retained runtime and broker boundary

The `run`, `ideas cycle`, recorder, web console and broker adapters remain
compatibility surfaces with existing policy and storage contracts. They are not
silently redirected to the experiment. The [paper guide](paper_trading.md)
retains their operational procedures; the
[cutover proposal](decisions/paper-runtime-cutover.md) controls their disposition.

`ApplicationContainer` remains the retained bot's composition root. It wires
configuration, broker, persistence, risk and observability into `TradingBot`.
The live engine's canonical submission path applies guards, persists admitted
intent, then interacts with a broker. Durable receipt recovery and identified
fills cannot make a remote broker call atomic: missing acknowledgments remain
uncertain until reconciled. This is a useful boundary to preserve, even though
an entirely local fill fits inside one transaction.

Trade-idea persistence uses the [transactional state contract](decisions/transactional-trade-state.md).
It does not establish that any deployed JSON store was migrated. Position-targeted
reductions retain original entry identity and reserve pending quantity. Unknown
inventory, fees and fill evidence stay unknown. Do not infer a complete account
from an empty local journal or substitute planned prices for missing execution.

## Import boundaries

`scripts/ci/check_import_boundaries.py` enforces lower-layer/entrypoint separation,
monitoring dependencies, the trade-idea dependency contract and cross-slice edges.
The experiment may consume core accounting and the pure trade-idea benchmark and
snapshot contracts. It may not import broker adapters, the live engine, CLI,
preflight or the application container. The new experiment-to-trade-ideas edge
exists for selective reuse, not access to the legacy service or approval queue.

Existing allowed edges are debt/intent records, not blanket permission for new
coupling. Add an edge only with an architecture rationale. Pure dependencies are
passed explicitly; no service locator in decision/accounting code. The web
console remains structurally restricted to its trade-idea adapter contract.

## Runtime Profile Registry

The retained runtime uses `ProfileLoader` in
`src/gpt_trader/app/config/profile_loader.py`, tracked profile YAML and typed
`BotConfig`. CLI configuration and overrides, profile and environment resolution belong to
that code and its tests. Do not infer a running profile from a tracked YAML file.
Invalid present profiles fail closed; missing-profile fallback and effective
precedence must be verified at the selected entrypoint.

This profile system does not configure recorded experiments. Operational readers
such as readiness use profile-specific state; that evidence cannot be borrowed
from an experiment or a different date to clear an operational gate.

## State ownership and verification

[Information Architecture](INFORMATION_ARCHITECTURE.md) owns artifact locations.
New experiments use an explicit isolated root, normally
`runtime_data/experiments/<name>/`. Existing operational stores, original datasets,
control grants and worktrees remain intact. Development does not pull merged
source into a checkout used by a scheduled runtime or select a new state root.

Tests cover causal ordering, bounded sizing, monetary conservation, cost allocation,
stale data, duplicate observations, changed inputs, interrupted transactions,
concurrent resumption and inconsistent state. Existing broker-specific financial
and recovery tests remain in force. Source verification and integration are
separate from runtime acceptance and measured trading outcomes.
