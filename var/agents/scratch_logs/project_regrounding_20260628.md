# GPT-Trader Project Regrounding Packet - 2026-06-28

---
status: current
---

## Scope

Bounded evidence-first pass over current repo docs, source, tests, live GitHub
queue, and safe local operator surfaces. No live broker/API calls, canary/prod
operation, production preflight, money movement, or order submission were run.

## Live Truth Baseline

| Surface | Current evidence | Classification |
| --- | --- | --- |
| Worktree | `git status --short --branch` reported `## HEAD (no branch)` at intake, then this run switched to `codex/reground-project-direction-20260628`; no worktree diff at intake | Trusted |
| Local head | `4d17854b Add docs-to-code currency scanner and unit tests`; `origin/main` at `a4acc83d` | Trusted local-ahead context |
| Open PRs | PR #1049 open draft, branch `claude/amazing-pasteur-idqhrb`, `CLEAN`, logging bug fix | Unrelated active draft |
| Issue #1031 | Closed 2026-06-28; Coinbase `MarketSnapshot` builder | Trusted complete |
| Issue #1035 | Closed 2026-06-28; paper fill reconciliation to audit trail | Trusted complete |
| Issue #1033 | Open, `triage:build-now`; strategy signal to trade-idea adapter | Next core spine |
| Issue #1044 | Open reporting/export consolidation follow-up | Partial/stale against shipped export surfaces |

## Docs Claim Matrix

| Claim | Repo-doc source | Source/test truth | Live GitHub truth | Runtime exercise truth | Classification |
| --- | --- | --- | --- | --- | --- |
| Stage 0 rails are complete | `docs/STATUS.md`, `docs/OPERATING_RUBRIC_V2.md`, `docs/PRE_MIGRATION_DECISION_FRAMEWORK.md` | `features/trade_ideas/{models,workflow,audit,eligibility,policy,budget,service}.py` plus unit coverage | No open Stage 0 blocker found in named queue | `ideas propose-baseline`, approve, audit verify exercised local rails | Trusted |
| Active autonomy boundary is `human_approved_execution` | `AGENTS.md`, `docs/PRE_MIGRATION_DECISION_FRAMEWORK.md`, `docs/ARCHITECTURE.md`, rubric v2 draft | `ApprovalPolicy` and `TradeIdeaService` require human approval before approved/submitted lifecycle | #1033 explicitly non-goal: no auto approval/order submission | No command submitted/cancelled/modified broker orders | Trusted |
| Real-data proposer input exists | Old `STATUS.md` said missing; #1031 requested builder | `snapshot_builder.py`, `snapshot.py`, CLI `ideas snapshot build`, tests `test_snapshot_builder.py` and `test_ideas_snapshot_build.py` | #1031 closed 2026-06-28 | Local `MarketSnapshot` fixture -> `ideas propose-baseline` produced one eligible proposed idea | Trusted operator-usable |
| Paper fills can reconcile onto audit trail | Old `STATUS.md` said manual only; #1035 requested reconciliation | `paper_reconciliation.py`, CLI `ideas reconcile-paper-fills`, tests for apply/dry-run/live-profile rejection | #1035 closed 2026-06-28 | Local EventStore fill dry-run matched 1; `prod --apply` rejected; `paper --apply` recorded submission + fill | Trusted operator-usable |
| Strategy/live bot intelligence flows through trade ideas | `STATUS.md` says worlds disconnected and #1033 is bridge | `rg` found no `features.trade_ideas` imports from `features/live_trade`; `decision_trace.py` only carries optional IDs | #1033 open and build-now | Not exercised because implementation is absent and out of scope | Missing |
| Expiry sweep is scheduled | `STATUS.md` says partial | `service.expire_due_ideas()` exists; no scheduler wiring found in this pass | No named issue in requested queue | Not exercised beyond source inspection | Partial |
| Machine-readable exports are unified | #1044 says open unified artifact follow-up | `artifacts.py`, report/audit/closeout exports, and ticket export exist; ticket still has separate broker-payload contract | #1044 remains open | Local report JSON, closeout JSON, audit CSV, and ticket JSON succeeded | Partial/stale follow-up |

## Safe Operator Exercise

Scratch root: `/tmp/gpt-trader-regrounding-20260628-rJRqYZ`.

| Command/surface | Result |
| --- | --- |
| `uv run gpt-trader ideas --help` | Listed trade-idea lifecycle commands, including snapshot, reconcile, report, closeout, audit, and export-ticket |
| `uv run gpt-trader ideas snapshot build --help` | Confirmed explicit read-only Coinbase candle snapshot command; not run against network in this pass |
| `uv run gpt-trader ideas reconcile-paper-fills --help` | Confirmed dry-run default, `--apply`, paper/mock/dev profile path, and no broker/account contact text |
| `ideas propose-baseline --snapshot scratch/snapshot.json` | Produced `trade-20350612-btcusd-74be48e8`, state `proposed`, no approval violations |
| `ideas approve ...` | State became `approved` through human actor |
| `ideas export-ticket ... --venue manual --out scratch/ticket.json` | Wrote deterministic broker-neutral ticket artifact locally |
| `ideas reconcile-paper-fills --profile paper` | Dry-run matched 1 paper fill, recorded 0, no mutation |
| `ideas reconcile-paper-fills --profile prod --apply` | Rejected with profile validation; no mutation |
| `ideas reconcile-paper-fills --profile paper --apply` | Matched 1, recorded submission + fill, final state `filled` |
| `ideas closeout record ...` | Recorded thesis-target closeout with realized P/L evidence |
| `ideas audit verify` | Passed after reconciliation with 4 audit events |
| `ideas report --output-dir scratch/report-artifacts` | Wrote JSON report with 1 idea, 1 terminal closeout, 100% closeout coverage |
| `ideas closeout export --output-dir scratch/report-artifacts` | Wrote JSON closeout export with 1 row |
| `ideas audit export --format csv` | Wrote local audit CSV |

## Trusted Foundations

- Stage 0 trade-idea rails are a trusted foundation unless fresh evidence
  disproves them: broker-neutral records, workflow, audit, eligibility, policy,
  budget, service lifecycle, CLI review, TUI review, closeout, report, and
  export surfaces.
- The active boundary remains `human_approved_execution`. Implemented adapters,
  profiles, or closed issues are not approval for live automation.
- Broker-neutral records stay canonical. Broker/API/account/venue/live-control
  expansion still routes through `decision-needed` packets or approved runbooks.
- `OPERATING_RUBRIC_V2.md` is useful as a draft measured-outcome rubric; build
  status belongs in `STATUS.md` and GitHub issues.

## Contradictions And Drift

- `docs/STATUS.md` was stale against #1031/#1035 and source: real-data snapshot
  building and paper-fill reconciliation now exist as operator-usable surfaces.
- `docs/STATUS.md` still correctly identified the structural #1033 gap: live
  strategy decisions are not routed through `TradeIdeaService`.
- #1044 is open, but report/audit/closeout/ticket export surfaces are already
  present and locally exercised. Treat it as a partial/stale consolidation item,
  not the next core-spine move, unless a later pass narrows the remaining exact
  envelope mismatch.
- PR #1049 is an unrelated draft bug fix in the order-event logging path; it is
  not a blocker for the Stage 1 spine decision in this pass.

## Recommended Next Moves

1. Implement #1033 as the next bounded product move: strategy-signal ->
   trade-idea adapter, proposal-only, default-off, no broker/API calls, and no
   auto-approval/order submission.
2. After #1033 lands, run a focused Stage 1 loop exercise that starts from a
   strategy signal, emits a proposed idea, human-approves it, reconciles a paper
   fill, records closeout, and refreshes the report.
3. Then reconcile #1044 separately: either close it as satisfied with evidence
   or narrow the remaining shared-envelope/source-digest requirement into one
   bounded reporting issue.

## Open Decisions

- Whether `ideas snapshot build` should remain an explicit operator command
  only, or later get scheduled inside a paper loop.
- Whether `ideas reconcile-paper-fills --apply` remains operator-run, or a
  paper/dev scheduler/hook should call it after #1033 gives approved ideas a
  complete paper-loop source.
- Any move from `human_approved_execution` to bounded autonomy still needs a
  separate decision packet with strategy envelopes, kill-switch evidence, audit
  evidence, and venue/account/API capability verification.

## Deferred Work

- No #1033 implementation in this pass.
- No GitHub issue edits or closures in this pass.
- No live market-data fetch, broker/API call, canary/prod operation, production
  preflight, order submission, or account-capability check.
- No broad backlog generation.

## Verification Receipts

- `git status --short --branch` -> clean detached intake, then clean
  `codex/reground-project-direction-20260628` before edits.
- `git log --oneline --decorate -8` -> local head `4d17854b`, origin head
  `a4acc83d`, recent merged trade-idea spine PRs present.
- `git diff --stat` -> no intake diff.
- `gh pr list --limit 20 --json ...` -> only PR #1049 open draft.
- `gh issue view 1031/1035/1033/1044 --json ...` -> states recorded above.
- `rg` over `features/live_trade`, `features/intelligence`, `features/brokerages`,
  and `app` for `trade_ideas`/`TradeIdea` -> no live-trade bridge import found.
- Safe local CLI exercise receipts are in the table above.
