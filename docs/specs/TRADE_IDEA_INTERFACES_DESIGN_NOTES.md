---
status: current
scope: Operator and agent interfaces over the trade_ideas slice
audience: Implementation agents (Codex) and reviewers
---

# Trade-Idea Interfaces — Design Notes

## Context

The accepted direction (docs/DIRECTION.md) is staged
autonomy: AI drafts complete trade-idea records, a human approves them, and
every state change lands in an append-only audit log. The core slice for this
already exists and is complete at the domain level:

| Module | Provides |
|--------|----------|
| `features/trade_ideas/models.py` | `TradeIdea` record (frozen dataclass, `to_dict`/`from_dict`, `record_hash`) |
| `features/trade_ideas/workflow.py` | `TradeIdeaState`, `ALLOWED_TRANSITIONS`, `validate_transition` |
| `features/trade_ideas/service.py` | `TradeIdeaService` — the one audited code path for every actor |
| `features/trade_ideas/policy.py` | `ApprovalPolicy` — autonomy mode as enforceable checks |
| `features/trade_ideas/budget.py` | `RiskBudget`, versioned `RiskBudgetLog`, seeded defaults |
| `features/trade_ideas/audit.py` | Append-only `TradeIdeaAuditLog` (JSONL), `AuditEvent` |
| `features/trade_ideas/store.py` | `TradeIdeaStore` — versioned records under record hash |
| `features/trade_ideas/baseline.py`, `replay.py` | Baseline proposer and replay scoring |
| `features/strategy_tools/trade_idea_adapter.py` | Default-off strategy decision → `TradeIdeaService.propose()` bridge |

**Current interface state:** the CLI review surface exists.
`gpt-trader ideas` constructs `TradeIdeaService` through the trade-ideas factory
and exposes the agent-facing approval workflow: propose, list, show, approve,
reject, request-changes, expire, resubmit, mark-submitted, mark-filled, budget,
audit, and report. It also exposes the read-only Stage 1 baseline calibration
surface, `gpt-trader ideas replay baseline`, which parses a local candle fixture
and formats `TradeIdeaReplayRunner` / `ReplayReport` output without using
`TradeIdeaService` storage. `ideas list` exposes
`TradeIdeaService.list_view_result(TradeIdeaListQuery(...))` for filters,
sorting, and pagination metadata, so `gpt-trader ideas` is the implemented human
review surface for proposing, filtering, and recording approve, reject,
request-changes, and expire decisions through `TradeIdeaService`. MCP or other
remote surfaces remain future work. The service docstring states the adapter
boundary explicitly: *"interfaces such as CLI or MCP servers must stay thin
adapters over these methods."*

A Textual TUI review screen was also implemented but has since been removed (see
`docs/decisions/remove-tui-subsystem.md` and `docs/DEPRECATIONS.md`). These notes
preserve the interface decisions that shaped the implemented CLI surface; they
are not a request to re-promote a TUI review workstream.

The Stage 1 strategy-signal bridge is a library adapter that the live engine now
drives behind a default-off gate. `StrategySignalToTradeIdeaAdapter` accepts an
existing strategy decision shape plus explicit point-in-time context, maps
supported buy signals to complete broker-neutral `TradeIdea` records, and submits
them through `TradeIdeaService.propose()` only when explicitly enabled. Disabled,
hold, sell, or close decisions produce no idea. It does not approve, preview,
submit, modify, cancel, or reconcile orders, and it does not call broker/account
APIs. The runtime wiring is described in
[Live strategy-signal routing](#live-strategy-signal-routing-default-off) below.

## Live strategy-signal routing (default-off)

Issue #1033 wires the adapter into the live bot cycle behind an explicit,
default-off gate. This is the runtime half of the Stage 1 human-approved loop in
[docs/DIRECTION.md](../DIRECTION.md); current shipped state is tracked in
[docs/STATUS.md](../STATUS.md) and the seam is documented in
[docs/architecture/SEAMS.md](../architecture/SEAMS.md).

**What the gate does.** When enabled, `TradingEngine._handle_decision`
(`features/live_trade/engines/strategy.py`) routes every strategy decision into
`TradeIdeaService.propose()` through the adapter and returns before submitting an
order. Supported buy shapes become `proposed` trade ideas; hold, sell, and close
shapes are logged and produce no idea. The per-cycle order audit — which
reconciles broker state and can cancel drifted orders — is also skipped while the
gate is on (`cycle_runner._fetch_positions_and_audit`), so proposal-only mode
never mutates broker state: it reads market data but places and cancels nothing.
With the gate off (the default) decisions flow to direct execution exactly as
before.

**How to enable it.** The gate is off by default and must be set explicitly:

- Config field: `BotConfig.strategy_signal_proposals_enabled` (default `False`).
- Profile YAML: `execution.strategy_signal_proposals: true` under a profile in
  `config/profiles/`.

Enabling it puts the bot in proposal-only mode: it drafts ideas for human review
and never places orders. It is not an execution lane and does not bypass
`ApprovalPolicy`; every proposed idea still requires the human approve step.

**How to review the proposals.** Proposed ideas land in the standard trade-idea
store (`GPT_TRADER_IDEAS_ROOT`, default `var/data/trade_ideas/`) and are reviewed
through the existing `gpt-trader ideas` CLI — `ideas list --state proposed`,
`ideas show <decision_id>`, then `ideas approve` / `ideas reject` /
`ideas request-changes`. Each proposal records the strategy name, symbol,
mark/as-of source (`live-strategy:decision:...`), action, and confidence as
evidence on the audit trail.

**Event-driven paper lane (#1191, default-off).** Setting
`execution.event_driven_paper_lane: true` (config field
`BotConfig.event_driven_paper_lane_enabled`) implies the proposal routing above
and additionally carries each proposed idea through the risk kernel in the same
engine cycle: system approval, an execution-time autonomy re-check, then paper
execution against a lane-owned paper broker
(`features/idea_execution/event_lane.py`). The lane honors the same operator
env gates as the batch Stage 2 mechanisms — without
`GPT_TRADER_IDEAS_AUTO_APPROVAL` ideas stay `proposed` for review, and without
`GPT_TRADER_IDEAS_AUTO_EXECUTION` approved ideas await the batch executor.
Kernel denials are audited on the idea trail (`auto_approval_skipped` /
`auto_execution_skipped`). Paper only: live order submission stays gated by
[docs/DIRECTION.md](../DIRECTION.md).

## Design Principles

1. **Thin adapters only.** Interfaces parse input, resolve actor identity,
   call one `TradeIdeaService` method, and render the result. No workflow,
   policy, or budget logic in CLI code. If an interface needs a new
   behavior, it goes into the service first. The read-only replay baseline
   calibration command is the explicit exception: it is not a workflow mutation
   or storage adapter, so it may call `TradeIdeaReplayRunner` directly and
   render `ReplayReport` without constructing `TradeIdeaService`.
2. **Every action is identity-stamped.** No anonymous mutations. Each
   mutating command requires an `actor_id` and an `actor_type`; review
   actions from interactive interfaces are always `ActorType.HUMAN`.
3. **No live execution lane.** Nothing in these workstreams places, modifies,
   or cancels live broker orders. `mark-submitted` / `mark-filled` are audit
   bookkeeping for manually executed tickets, not order routing. The only
   machine execution surface is the paper-only `execute-paper` /
   `ideas cycle` lane; system-approved ideas reach it only through
   [stage2-execution-gate](../decisions/stage2-execution-gate.md).
4. **Policy refusals are first-class UX.** `PolicyViolationError.violations`
   is a list of every reason an approval was refused. Interfaces must show
   the full list, never just the first reason or a generic failure.
5. **JSON-first for agents.** All CLI commands support
   `--format json` via the existing `CliResponse` envelope so Codex/Claude
   and CI can drive the workflow programmatically. Text output follows the
   `✓`/`✗` standards in [`TRADE_IDEA_CLI_SPEC.md`](TRADE_IDEA_CLI_SPEC.md).
6. **Append-only mindset.** Interfaces never edit records in place. A change
   request produces a `needs_changes` event; the revised record is a new
   version saved via `resubmit`.
7. **Default-off strategy bridges.** Strategy-signal bridges live outside the
   broker-neutral `trade_ideas` core, require explicit enablement, and may only
   call `TradeIdeaService.propose()` until a later decision/runbook scopes
   runtime wiring.

## Shared Decisions (Workstream 0 — implemented)

The CLI implements these decisions today. Any future interface should reuse them
instead of creating a parallel service or storage contract.

### Storage root

- Default root: `var/data/trade_ideas/` (consistent with the existing
  `var/data/status.json` convention). The service derives
  `records/`, `audit.jsonl`, and `risk_budget.jsonl` under it.
- Override: environment variable `GPT_TRADER_IDEAS_ROOT`; CLI also accepts
  `--ideas-root PATH` (highest precedence) for tests and sandboxing.

### Service factory

Implemented in `features/trade_ideas/service.py`:

```python
DEFAULT_IDEAS_ROOT = Path("var/data/trade_ideas")

def create_trade_idea_service(root: Path | None = None) -> TradeIdeaService:
    """Resolve root (arg > GPT_TRADER_IDEAS_ROOT > default) and build the service."""
```

The CLI constructs the service directly through this factory because idea
review has no broker or config dependency. A cached `trade_idea_service`
property on `ApplicationContainer` remains a future option once a proposer loop
runs inside the bot.

### Actor identity resolution

Precedence for `actor_id`: `--actor` flag → `GPT_TRADER_ACTOR` env var →
`getpass.getuser()`. The resolved value is recorded verbatim in the audit
log. `actor_type` rules:

| Action | actor_type |
|--------|-----------|
| `propose`, `resubmit` | `ai` by default; `--actor-type human` allowed |
| `approve`, `reject`, `request-changes`, `cancel` | always `human` (the policy enforces this for approve; interfaces hard-code it for the rest) |
| `expire` (sweep) | `system` |
| `mark-submitted` | `system` (default per service) or `human` |
| `mark-filled` | `venue` (default per service) |
| budget `set` | `human` (policy refuses non-human in current mode) |
| `execute-paper`, `ideas cycle` fills | `system` submission under `paper`; fill from `venue` |

### Error mapping

Implemented in `CliErrorCode` in `cli/response.py`:
`POLICY_VIOLATION = "POLICY_VIOLATION"` and
`IDEA_NOT_FOUND = "IDEA_NOT_FOUND"`.

| Exception | CliErrorCode | Exit | Notes |
|-----------|--------------|------|-------|
| `PolicyViolationError` | `POLICY_VIOLATION` | 1 | Put `violations` list in `data["violations"]`; text mode prints each on its own line |
| `UnknownTradeIdeaError` | `IDEA_NOT_FOUND` | 1 | |
| `InvalidTransitionError` | `VALIDATION_ERROR` | 1 | |
| `AuditIntegrityError` / `BudgetIntegrityError` | `OPERATION_FAILED` | 1 | Integrity failures are loud, never swallowed |
| Malformed input JSON / missing fields | `INVALID_ARGUMENT` | 1 | Report the offending field |

## Workstreams

| # | Spec | Depends on | Size |
|---|------|-----------|------|
| 0 | Shared wiring (this doc, "Shared Decisions") | — | Implemented |
| 1 | [`TRADE_IDEA_CLI_SPEC.md`](TRADE_IDEA_CLI_SPEC.md) — `gpt-trader ideas` command group | 0 | Implemented |
| 2 | Ideas review screen (Textual TUI) | 0 (not 1) | Removed |

Workstreams 0+1 provide the agent-facing CLI surface and unblock the
AI-propose -> human-approve loop end to end. Workstream 2 shipped a
keyboard-driven Textual review screen, but the TUI subsystem was later removed
(see `docs/decisions/remove-tui-subsystem.md`); the `gpt-trader ideas` CLI is the
implemented human review surface. Future interface work should start from the
shipped CLI adapters.

## Non-Goals (all workstreams)

- No order submission, modification, or cancellation through any broker API.
- No broker-specific order payload generation or execution adapter. Deterministic
  broker-neutral ticket export is implemented in
  [`TRADE_IDEA_CLI_SPEC.md`](TRADE_IDEA_CLI_SPEC.md) and remains a render-only
  artifact.
- No bounded-autonomy behavior; `ApprovalPolicy` defaults stand.
- No INTX surfaces (frozen).
- No MCP server (a future workstream; the CLI JSON mode is the agent surface
  for now).
- No editing of historical records or audit events, ever.

## Conventions Codex Must Follow

- Naming: banned abbreviations `cfg`, `svc`, `mgr`, `util`, `utils`, `amt`,
  `calc`, `upd` (see `docs/naming.md`). Run `uv run agent-naming`.
- Tests: prefer `monkeypatch`; assert on `CliResponse`
  (`result.errors[0].code == CliErrorCode.X.value`). Unit tests live under
  `tests/unit/gpt_trader/...` mirroring source paths.
- Quality gate before PR: `make ci-required`, plus
  `uv run ruff check . --fix`, `uv run black .`, `uv run mypy src/gpt_trader`.
  (`uv run agent-check` is an optional JSON summary helper, not the gate.)
- Import boundaries: `scripts/ci/check_import_boundaries.py` runs in CI —
  keep interface code free of cross-slice imports it would flag.
