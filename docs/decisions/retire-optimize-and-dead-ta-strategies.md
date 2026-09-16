# Retire the optimizer and the dead TA strategies; settle venue scope until the reasoning proposer has evidence

---
status: proposed
date: 2026-09-16
deciders: rj
supersedes:
superseded-by:
---

> While `status: proposed`, this is an open decision the owner has not yet
> made. It is a decision record only: **no code is removed by this PR.** If
> accepted, removal is one separate implementation PR (see Consequences).

## Context

[adopt-agentic-alpha-direction](adopt-agentic-alpha-direction.md) (2026-07-08)
measured the technical-analysis alpha layer to its conclusion and directed the
next unit of alpha investment to a reasoning proposer, scoped as #1252. Since
that record there are **zero commits** on #1252, while the surfaces the record
demoted are still carried in full: an Optuna parameter-search slice, an
ensemble strategy that was never ported to the proposer seam, a renamed-away
`perps_baseline` shim, and a second-venue observation layer. The 2026-09-05
[recorded-experiment-product](recorded-experiment-product.md) record narrowed
the product further to one local loop over the baseline benchmark, with
"no new indicator-family investment or performance claims."

The choice forced now is whether the budget that keeps those surfaces green
(tests, mypy, CI time, review attention, dependency bumps) goes on paying for
them or goes to #1252.

### Measured evidence (from the repo's own records)

| Surface | Evidence | Source |
| --- | --- | --- |
| Long-only MA tuning grid | **0/24** configs positive on both evidence windows | #1246 (closed with findings); cited in [adopt-agentic-alpha-direction](adopt-agentic-alpha-direction.md) |
| `strategy-regime-switcher` | **−0.26** edge vs baseline | #1241 M5 evidence; same record |
| Regime-aware overlay (`trade_ideas/regime.py`) | byte-identical to baseline before M5; **−0.08** pooled after exit-plan and entry-policy channels | #1241, #1242, #1243 |
| Mean reversion | **+0.103 / +0.155** relative edge on two non-overlapping 720-hour windows (435 + 202 resolved ideas) — the only durable positive | #1241; adopted as Stage-2 benchmark by the same record |
| Ensemble | **Never measured.** Not ported to the proposer seam (#1164 stage 3 deferred it), absent from `scripts/ops/replay_evidence.sh` defaults (`baseline-ma-10-50,regime-aware-ma-10-50,strategy-mean-reversion,strategy-regime-switcher`), not selectable in `ideas cycle`/`ideas replay` | #1164, #1241 out-of-scope list |
| `perps_baseline` | Transitional re-export shim since the 2026-07-02 rename to `strategies/baseline` ("will be removed once none remain"); INTX perpetuals were removed 2026-06-30 | `strategies/perps_baseline/__init__.py`, [intx-default-derivatives-venue](intx-default-derivatives-venue.md) |

### Size of the surfaces (measured 2026-09-16 on `main` at #1286)

| Surface | Source | Tests | Other |
| --- | --- | --- | --- |
| `src/gpt_trader/features/optimize/` | 4,652 lines / 23 files | 3,625 lines / 26 files (unit + integration; excludes the 403 bridge-test lines below) | `optimize` extra: `optuna>=4.0.0,<5.0.0` |
| `src/gpt_trader/cli/commands/optimize/` | 3,319 lines / 11 files | (counted above) | `ideas replay baseline --from-optimize-study / --optimize-objective` consume its JSON exports |
| `src/gpt_trader/features/trade_ideas/optimize_bridge.py` | 352 lines | 403 lines / 2 files | Standalone grid replay; **does not import** `features.optimize`. This is the tool that produced the 0/24 result |
| `strategies/ensemble.py` + `ensemble_profile.py` | 745 lines | ~733 lines / 6 files (some shared with factory tests) | `StrategyType` literal, `ensemble_config` / `regime_config` on `BotConfig`; `signals/` + `combiners/` (1,389 lines) are candidate ensemble-only dependencies to audit |
| `strategies/regime_switcher/` | 314 lines | ~1,423 lines / 5 files (some shared) | Registered in `factory.py`; tournament-runnable as `strategy-regime-switcher` |
| `strategies/perps_baseline/` | 66 lines (shim) | none of its own | Referenced only by `cli/commands/optimize/{config_loader,run}.py` and two READMEs |
| `strategies/baseline/` (keep) | 566 lines | ~14,362 lines / 67 files (keyword match; shared fixtures) | Only `strategy.type` used by every profile in `config/profiles/` |
| `strategies/mean_reversion/` (keep) | 469 lines | ~4,166 lines / 21 files | Standing benchmark per the agentic decision |

No module outside `features/optimize` and `cli/commands/optimize` imports
either package (`rg 'features\.optimize|commands\.optimize|import optuna' src scripts`
matches only files inside them plus the `trade_ideas/__init__.py` re-export of
the bridge).

### Venue surface (measured the same day)

| Venue surface | Source | Tests | Config / deps / docs |
| --- | --- | --- | --- |
| `brokerages/coinbase/` (execution destination) | 9,831 lines | ~16,751 lines | — |
| `brokerages/robinhood/` (crypto REST + agentic MCP, observation only) | 2,310 lines / 18 files | 1,836 lines / 18 files, incl. 2 secret-gated `tests/real_api/` files | `robinhood-agentic` extra (`mcp`, `jsonschema`, `keyring`); 4 `ROBINHOOD_*` env vars in `.env.template`; `docs/ROBINHOOD.md` (121 lines); touches in `cli/commands/account.py`, `app/container.py`, `app/config/bot_config.py`, `brokerages/accounts.py`, `trade_ideas/broker_payloads.py` |
| Alpaca equities candles + trading calendar | `recorder/equities_candles.py` 367 lines; `core/trading_calendar.py` 265 lines; 38 `alpaca` mentions in `cli/commands/ideas.py` | ~1,921 lines / 7 files (mixed-source cycle and session-gate tests included) | `exchange-calendars` is a **core** dependency (`pyproject.toml`); `ALPACA_API_*` env |

The venue layer is sanctioned by
[real-account-read-preview-capability](real-account-read-preview-capability.md)
(authenticated reads and non-binding previews only). The execution destination
remains Coinbase only under
[accept-staged-autonomy-direction](accept-staged-autonomy-direction.md), and
[venue-neutrality-posture](venue-neutrality-posture.md) already says no venue
abstraction is built ahead of a concrete second execution venue. What none of
those records settle is whether second-venue *observation* keeps receiving
engineering effort while the Coinbase reasoning proposer has no evidence yet.

## Options

- **Option A — Retire the optimizer and the unmeasured/shim strategies, freeze
  the measured-negative ones, pause second-venue work (recommended).**
  Retire `features/optimize`, `cli/commands/optimize`, the `optimize` extra,
  and the `--from-optimize-study` / `--optimize-objective` replay flags;
  retire `strategies/ensemble.py`, `ensemble_profile.py`, the `ensemble`
  strategy type, and the `perps_baseline` shim. Freeze `regime_switcher` and
  the regime-aware overlay: no new work, kept only as comparison rows in
  replay tournaments and for the recorded-experiment record's "retain for
  measured comparison" disposition. Keep Robinhood and Alpaca/equities
  observation code, tests, and authority as they are, but pause further venue
  work until #1252 W1–W4 have wall-clock evidence. Trade-off: the frozen and
  paused code still costs CI time and dependency bumps; that is accepted in
  exchange for not superseding two accepted records.
- **Option B — Keep everything and fund #1252 alongside.** No removal; the
  proposer work starts on top of the current tree. Trade-off: this is what
  the last two months already tried (five commits, none on #1252); the
  surfaces are not neutral because every dependency bump, mypy run, and
  test-suite reshaping pays for them first.
- **Option C — Option A plus removal of Robinhood and Alpaca/equities.**
  Trade-off: reverses an accepted capability review and deletes working,
  secret-gated observation adapters that cost little while idle; the venue
  question would have to be re-reviewed from scratch if a second venue ever
  becomes concrete. Nothing in the evidence requires it.
- **Option D — Option A plus retiring `regime_switcher` outright.** Trade-off:
  the −0.26 result is a measured negative, which is exactly the kind of
  comparison row the scorecard and the recorded-experiment record want to
  keep; deleting it removes the losing side of the benchmark pair.

## Decision

Proposed (recommended): **Option A.** Retire the `optimize` slice, its CLI,
the `optuna` extra, the `ensemble` strategy family, and the `perps_baseline`
shim; freeze `regime_switcher` and the regime-aware overlay as comparison-only;
keep `baseline` and `mean_reversion`; leave the venue set as it is today —
Coinbase-only execution destination, Robinhood and Alpaca observation retained
under their existing authority but with no further engineering investment —
until the #1252 reasoning proposer has forward-only wall-clock evidence.

What stays, explicitly:

- `trade_ideas/optimize_bridge.py` and its `ideas replay baseline` grid flags
  (the deterministic replay tool that produced the 0/24 evidence). Renaming it
  away from "optimize" is optional cleanup for the implementation PR.
- `strategies/baseline/` and `strategies/mean_reversion/`, the benchmark pair
  the reasoning proposer must beat.
- `strategies/regime_switcher/`, `trade_ideas/regime.py`, and their tests:
  frozen, not deleted. No new channels, tests, or tuning. Removal becomes a
  later record if they block other work.
- `brokerages/robinhood/`, `recorder/equities_candles.py`,
  `core/trading_calendar.py`, `docs/ROBINHOOD.md`, the `robinhood-agentic`
  extra, and the secret-gated `tests/real_api/` files. Their authority is
  unchanged: [real-account-read-preview-capability](real-account-read-preview-capability.md)
  is not superseded, and nothing here widens or narrows what it permits.
- `backtesting/` is out of scope for this record; the agentic decision already
  narrows it to deterministic benchmark replay.

**External compatibility commitments are unaffected.** The
[DEPRECATIONS](../DEPRECATIONS.md) "Keep indefinitely" row for
`rest/base.py: _build_order_payload()` / `_execute_order_payload()` and the
active `COINBASE_ENABLE_INTX_PERPS` deprecation are untouched. This record
adds Recently Removed rows only when the implementation PR lands.

## Consequences

- **Budget freed (measured):** about 7,971 source lines and 3,625 test lines
  for the optimizer, plus about 811 source lines (`ensemble*`, shim) and up to
  ~733 test lines; one optional dependency (`optuna`); the `ensemble` branch of
  `factory.py` and two `BotConfig` fields. The `signals/` and `combiners/`
  packages (1,389 lines) are audited in the implementation PR and removed only
  where they are ensemble-only.
- **What #1252 needs, none of which this record changes:** the proposer seam
  (`strategy_tools/snapshot_proposer.py`), the scorecard and `benchmark_edge`
  accrual, the idea-record evidence-bundle fields (W1), a data-plane model
  credential and cost telemetry (W2), the context pack (W3), and forward-only
  evaluation wiring (W4). W0 (mean-reversion adoption into the Stage-2 cycle
  set) remains an operational act under the pending
  [paper-runtime-cutover](paper-runtime-cutover.md); this record does not
  perform it.
- **Follow-up implementation move (separate PR, only after `accepted`):**
  one PR that deletes `features/optimize`, `cli/commands/optimize`, the
  `optimize` extra, the `--from-optimize-study` / `--optimize-objective`
  flags and their tests, `strategies/ensemble.py`, `ensemble_profile.py`,
  `strategies/perps_baseline/`, the `ensemble` `StrategyType` and config
  fields, and the matching test files; updates `strategies/README.md`,
  `live_trade/README.md`, `docs/ARCHITECTURE.md`, `docs/agents/CODEBASE_MAP.md`,
  and `README.md`'s project tree; adds one Recently Removed row per surface to
  `docs/DEPRECATIONS.md`; and regenerates `var/agents/**` if that layer still
  exists after PR #1289. Verification: `ruff`, `mypy src/gpt_trader`,
  `pytest tests/unit -n auto`, `local-ci`, `docs_link_audit.py`,
  `docs_reachability_check.py`, `generate_decision_index.py --check`.
- **Freeze discipline:** PRs that add tests, channels, or tuning to
  `regime_switcher`, `trade_ideas/regime.py`, `brokerages/robinhood/`, or
  the equities data path cite this record and are declined unless the record
  is superseded. Dependency bumps that keep them compiling are allowed.
- **Re-open trigger for venue work:** a scorecard row for the reasoning
  proposer with wall-clock `benchmark_edge` over the minimum-accrual gate
  (#1252 W4), or a new accepted decision.

## Safety boundary

This record authorizes no broker or API call, no live execution, no money
movement, no autonomy-level change, and no change to any scheduled job,
runtime store, or profile. It removes no live-trading gate, readiness check,
canary runbook, or approval rule, and it does not alter the Coinbase-only
execution destination or the observation-only Robinhood authority. It is a
scope and budget decision about source code that is not on any execution path.
