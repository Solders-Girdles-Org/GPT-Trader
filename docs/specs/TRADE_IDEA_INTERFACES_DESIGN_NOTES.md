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

**Decision routing contract.** `TradingEngine._init_strategy_proposal_bridge`
selects collaborators at construction; `decision_flow.handle_decision` uses the
adapter's presence to choose one route. These flags select the strategy route;
they are not a global order-submission lock.

| Proposal flag | Paper-lane flag | Strategy decision destination |
| --- | --- | --- |
| Off | Off | BUY/SELL use `_validate_and_place_order`; CLOSE uses `submit_order` with the position quantity and reduce-only requested; HOLD does nothing. |
| On | Off | Supported spot BUY becomes a proposed idea for review; SELL/CLOSE/HOLD and unsupported products are skipped. |
| Either | On | Same adapter mapping, with executable sizing; each proposed idea continues through `EventDrivenIdeaLane` to gated paper execution. |

The flags are `BotConfig.strategy_signal_proposals_enabled` (profile YAML
`execution.strategy_signal_proposals`) and
`BotConfig.event_driven_paper_lane_enabled` (`execution.event_driven_paper_lane`).
Both config defaults are false; selected profiles may opt in (see
[shipped state](../STATUS.md)). Changing flags on an existing engine is not a
supported route-switch operation; construction wires the collaborators.

The adapter route always returns before direct strategy execution, including
mapping, persistence and paper-lane failures. Failures are logged; unsupported
actions/products are logged as skipped. Successful proposals and subsequent
paper actions persist on the idea audit trail. A failure before persistence
leaves no idea event. SELL/CLOSE do not become direct exits under this route.
The per-cycle order audit is also skipped when either flag is enabled
(`cycle_runner._fetch_positions_and_audit`); this is a cycle-specific boundary.

**Separate submission boundaries.** `TradingEngine.submit_order()` delegates to
`_validate_and_place_order()` regardless of the proposal flags. Both reach the
engine's configured broker only through the guard stack: kill switch,
degradation, sizing/reduce-only, security, risk, mark freshness, exchange rules,
slippage and configured preview checks. Their evidence is an order decision
trace and order events, not a trade-idea approval trail. Callers must satisfy
[DIRECTION's execution authorization](../DIRECTION.md#gate-before-execution-paths);
a route flag or a passing guard is not approval. `OrderSubmitter.submit_order()`
is a lower-level execution helper, not an alternative public safety boundary.

**How to review the proposals.** Proposed ideas land in the standard trade-idea
store (`GPT_TRADER_IDEAS_ROOT`, default `var/data/trade_ideas/`) and are reviewed
through the existing `gpt-trader ideas` CLI — `ideas list --state proposed`,
`ideas show <decision_id>`, then `ideas approve` / `ideas reject` /
`ideas request-changes`. Each proposal records the strategy name, symbol,
mark/as-of source (`live-strategy:decision:...`), action, and confidence as
evidence on the audit trail.

**Paper admission and recovery.** `EventDrivenIdeaLane` uses a lane-owned
`DeterministicBroker`, never the engine broker. Without
`GPT_TRADER_IDEAS_AUTO_APPROVAL`, the idea stays proposed. With approval enabled,
the risk kernel must admit system approval; without
`GPT_TRADER_IDEAS_AUTO_EXECUTION`, an approved idea waits. The lane audits kernel
precheck denials (`auto_approval_skipped` / `auto_execution_skipped`) and rechecks
execution authority. Approval is revalidated in the committing transaction;
a policy change after the preview returns an `approval_denied` outcome without
an approval or denial event from that failed commit, before any execution.
State storage and migration boundaries are owned by
[Transactional trade state](../decisions/transactional-trade-state.md).
`PaperIdeaExecutor.execute()` independently reloads
the persisted idea and checks APPROVED state, approval actor, current system
execution authority, hard expiry, open session and executable sizing. Human
approval uses the human lane; system approval requires the recognized actor,
execution opt-in and current bounded autonomy, including ratchet checks.

The executor accepts only exact allowed paper/mock broker types. It records
SUBMITTED before calling that broker and records fills through
`PaperFillReconciler`. On restart, SUBMITTED/FILLED ideas cannot be executed
again; an interrupted submission needs reconciliation, not blind retry. A
previously approved idea can be refused after expiry or a system-authority
change, even if an earlier admission check passed. The event lane's broker is
in-memory; idea audit persistence does not imply broker-position recovery.

Executable boundary evidence lives in
`tests/unit/gpt_trader/features/live_trade/engines/test_strategy_routing_contract.py`,
`tests/unit/gpt_trader/features/idea_execution/test_executor_restart_contract.py`
and the existing `test_event_lane.py` / `test_executor_session_guard.py` suites.
These mocked checks establish routing and refusal behavior, not operating
liveness or the [measured promotion gates](../DIRECTION.md#graduation).

## Promotion scorecard and replay evidence (#1193)

**`ideas scorecard`** scores the Stage 1 → 2 promotion gates of the
[measured-outcome rubric](../decisions/adopt-measured-outcome-rubric.md) —
track-record depth, eligibility pass rate, attribution coverage, risk
calibration, expectancy (avg R), benchmark edge vs the deterministic baseline,
max drawdown-from-peak — plus the loop-health reds (proposals flowing,
attribution coverage, audit integrity), with the observation-window rule
(rolling 60 days *and* ≥ 200 closed ideas, whichever is larger) applied.
Every metric computes from the idea-level closeout/audit trail
(`features/trade_ideas/scorecard.py`); the batch cycle's run artifacts are
launchd scaffolding and are never read, test-enforced, so the evidence stays
valid under the event-driven lane. Gates that lack inputs render
`not_yet_measurable` rather than pass — drawdown-from-peak stays unmeasurable
until the continuous portfolio monitors (#1192) land, so the scorecard cannot
claim promotability before then. Thresholds are the owner-tunable values
recorded on the rubric decision.

**Replay-accelerated evidence.** The `ideas replay` commands accept a recorded
market-snapshot payload (`ideas snapshot build` output, or a cycle run's
persisted snapshot) as `--file` input in addition to bare candle fixtures, so
calibration and edge compute at machine speed over recorded windows.
`ideas scorecard --replay-report <json>` renders those figures alongside the
wall-clock gates, always labeled `replay-derived` and never blended into a
gate verdict; whether replay evidence counts toward promotion is a separate
owner call recorded on the rubric decision.

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
