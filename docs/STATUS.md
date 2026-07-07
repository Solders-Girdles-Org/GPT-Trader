# Project Status — Where We Actually Are

---
status: current
---

The factual **current-state** tracker: what is actually shipped, as distinct from
[DIRECTION.md](DIRECTION.md) (the destination and gates) and
[decisions/](decisions/README.md) (what was decided and why). This doc stays
small and points at the source of truth — it does **not** restate the ladder, the
backlog, or any decision. When a stage description elsewhere disagrees with
observed code, this doc wins until the other is reconciled.

Verify file/function/issue references before relying on them; they reflect the
dated snapshot below. The next work is the live GitHub issue queue,
never a list copied here.

## Snapshot (2026-07-03)

Stage definitions live in [DIRECTION.md](DIRECTION.md#the-ladder); this is only
the state per stage.

| Stage | State |
|-------|-------|
| **0 — Rails** | **Complete** — all rubric evidence shipped and tested |
| **1 — Human-approved loop** | **In progress (runtime routing wired, default-off)** — reviewer tooling, attribution, real-data snapshot proposal (#1031), paper-fill reconciliation (#1035), and the strategy-signal adapter are operator-usable; the live strategy path can now be routed through the approval workflow behind a default-off gate (#1033), but that gate ships off |
| **2 — Bounded autonomy** | Mechanisms started, operational promotion not entered — default-off auto-approval and paper auto-execution gates exist for system-approved ideas, but enabling them remains an operator act gated on measured outcomes |
| **3 — Self-directed entity** | Not started |

## Stage 0 — Rails (complete)

Every Stage 0 capability has shipped, tested evidence in
`src/gpt_trader/features/trade_ideas/`: broker-neutral record + hashing
(`models.py`), approval-gated state machine (`workflow.py`), append-only audit log
(`audit.py`), eligibility + approval policy (`eligibility.py`, `policy.py`),
versioned risk budget (`budget.py`), outcome attribution (`closeout.py`), and
operator lifecycle controls (`service.py`). The full lifecycle is exposed through
the `gpt-trader ideas` CLI.

## Stage 1 — Human-approved loop (in progress)

The shipped surfaces turn the rails into most of a loop: reviewer tooling
(CLI), outcome attribution, the track-record report, real-data
`MarketSnapshot` proposal (`ideas snapshot build` → `ideas propose-baseline`,
issue `#1031` closed 2026-06-28), paper-fill reconciliation onto the audit trail
(`ideas reconcile-paper-fills`, #1035 closed 2026-06-28), and a default-off
library adapter that maps supported strategy buy decisions into proposed trade
ideas through `TradeIdeaService.propose()` only.

**Runtime strategy-signal routing now exists behind a default-off gate**
(`strategy_signal_proposals_enabled`, #1033). When the operator enables it, the
live bot cycle (`features/live_trade/engines/strategy.py`) routes each strategy
decision through the existing default-off adapter into
`TradeIdeaService.propose()` instead of the broker: supported buy shapes become
`proposed` trade ideas and the engine submits no orders while the gate is on. It
ships off, so default behavior is unchanged. This deliberately reused the
existing spine rather than a second proposer brain — see
[stabilize-before-closing-the-loop](decisions/stabilize-before-closing-the-loop.md).
Enabling and reviewing the path is documented in the
[Trade-Idea Interface Design Notes](specs/TRADE_IDEA_INTERFACES_DESIGN_NOTES.md#live-strategy-signal-routing-default-off).
Track precise per-ticket status in the issue queue, not here.

## Stage 2 — Bounded autonomy (mechanisms started, not operationally entered)

The first Stage 2 mechanisms now exist as default-off paper-only gates:
`ideas approve --auto-sweep` can write system approvals inside the budget
envelope only when `GPT_TRADER_IDEAS_AUTO_APPROVAL` is enabled and the audited
autonomy log resolves to `bounded_autonomy`; the paper execution lane admits
those system approvals only when `GPT_TRADER_IDEAS_AUTO_EXECUTION` is also
enabled and the autonomy log still resolves to `bounded_autonomy` at execution
time. The execution gate reuses the daily-loss ratchet, so a breach lowers the
mode before remaining system-approved ideas can execute.

**The in-process event-driven lane exists behind a default-off gate**
(`event_driven_paper_lane_enabled`, #1191) and is **operator-enabled on the
`paper` profile** (recorded approval 2026-07-07; `config/profiles/paper.yaml`).
With the gate on, the live engine carries each proposed idea through the risk
kernel — system
approval, then an execution-time autonomy re-check — into paper execution in
the same engine cycle (`features/idea_execution/event_lane.py`), honoring the
same two env gates and the audited autonomy mode per decision. Kernel denials
land on the idea audit trail (`auto_approval_skipped` /
`auto_execution_skipped`), so a ratchet-down or kill-switch takes effect on
the next event, not the next hourly turn. The hourly batch cycle continues
unchanged as the evidence harness
([adopt-event-driven-execution-topology](decisions/adopt-event-driven-execution-topology.md)).

This is not a promotion claim. Live order submission remains out of scope; the
lane is paper-only and still bounded by the two env gates, the audited
autonomy mode, and the budget envelope at event time.

## The structural fact

The live TA bot (`features/live_trade/`) and the trade-idea workflow
(`features/trade_ideas/`) stay decoupled by default: strategy-to-idea mapping
lives in `features/strategy_tools/trade_idea_adapter.py` as an explicit,
default-off bridge. The live engine now *can* drive that bridge — routing
decisions into the approval-gated rails through `TradeIdeaService.propose()` —
but only when `strategy_signal_proposals_enabled` (or the lane gate
`event_driven_paper_lane_enabled`, which implies it) is set; with both gates off
(the default) the trading intelligence still flows straight to direct execution.

## How to keep this doc honest

- Update it when a capability moves between missing / partial / done — ideally in
  the same PR that changes the state.
- Prefer concrete pointers (file, function, issue number) over prose claims, and
  route volatile specifics (open-issue lists, per-ticket status) to the tracker.
- When this doc and a direction doc disagree, fix the direction doc or open a
  `proposed` decision; don't let the gap persist silently.
