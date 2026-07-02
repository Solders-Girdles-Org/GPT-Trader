# Canonical risk-limit vocabulary — budget vs runtime limits

---
status: proposed
date: 2026-07-02
deciders: RJ
supersedes:
superseded-by:
---

## Context

The repository carries **three separate expressions of "how much may be at
risk"**, none derived from the others:

1. **`RiskBudget`** (`src/gpt_trader/features/trade_ideas/budget.py`) — the
   versioned, append-only, renegotiable budget from the accepted staged-autonomy
   direction (`docs/DIRECTION.md`): `max_loss_per_idea_pct`,
   `max_daily_loss_pct`, `max_open_notional_pct`, concurrency/latency caps,
   `gain_retention_floor_pct`, futures/shorts permissions. Enforced at
   **approval time** by `ApprovalPolicy`
   (`src/gpt_trader/features/trade_ideas/policy.py`).
2. **`RiskConfig`** (`src/gpt_trader/features/live_trade/risk/config.py`) — the
   live risk manager's parameters: `max_leverage`, `daily_loss_limit_pct`,
   `max_exposure_pct`, `max_position_pct_per_symbol`, CFM caps, plus
   operational guards (staleness, slippage, degradation cooldowns, kill
   switch). Enforced at **runtime** by `LiveRiskManager.pre_trade_validate`
   and the daily-loss breaker.
3. **`BotRiskConfig`** (`src/gpt_trader/app/config/bot_config.py`) — bot-level
   sizing (`position_fraction`, stops) that *also* carries `max_leverage` and
   a second `daily_loss_limit_pct`, which seeds the runtime breaker.

The overlapping fields already disagree, in both value and units:

| Concern | Trade-ideas budget | Live runtime | Divergence |
|---|---|---|---|
| Daily loss cap | `max_daily_loss_pct = Decimal("10")` (percent points) | `daily_loss_limit_pct = 0.05` (float fraction) | 10% vs 5%, and `"10"` vs `0.10` for the same suffix `_pct` |
| Open exposure | `max_open_notional_pct = Decimal("100")` | `max_exposure_pct = 0.8` | 100% vs 80%, same unit trap |
| Per-position | `max_loss_per_idea_pct` (loss at invalidation) | `max_position_pct_per_symbol` (notional exposure) | different semantics that read as the same thing |
| Futures | `allow_futures_leverage` (boolean permission) | `cfm_max_leverage` + per-symbol caps (numeric) | permission vs magnitude, unlinked |

Why decide now: strategy-signal routing (accepted, default-off — PR #1090)
points live engine decisions **into** the trade-ideas workflow, so both
vocabularies will govern the same trade once execution is wired. Meanwhile a
seven-PR wave (#1091 budget gates, #1096, #1099, #1104–#1107) is actively
extending the budget/policy vocabulary — #1091 adds aggregate enforcement math
(same-day realized loss, open-notional projection) directly on top of
`RiskBudget` fields. Every merged consumer raises the cost of renaming or
re-homing these fields later.

## Options

- **Option A — `RiskBudget` is canonical for risk appetite; runtime limits
  derive from it.** The versioned budget becomes the single expression of
  *what may be at risk* (it is already the only audited, owner-renegotiable
  one, which is what the DIRECTION lever-handover requires). The live
  `RiskConfig` keeps only **enforcement mechanics** (staleness, slippage,
  cooldowns, kill switch, per-symbol operational caps), and its appetite
  fields (`daily_loss_limit_pct`, `max_exposure_pct`, leverage permissions)
  are seeded from the active budget version at engine startup, with explicit
  unit normalization and a shared day-boundary definition. `BotRiskConfig`
  drops its duplicated appetite fields (transitional aliases during
  migration). Trade-off: a derivation seam and a startup dependency on the
  budget store; runtime and approval-time enforcement both remain (two gates,
  one number).
- **Option B — Two vocabularies by design, reconciled by a mapping.** Declare
  approval-time budget and runtime limits deliberately separate layers; ship a
  documented field mapping plus a preflight/CI consistency check that fails
  when runtime limits are looser than the budget. Trade-off: cheapest now, no
  code churn mid-wave; drift remains possible between check runs, and the
  unit mismatch (`_pct` meaning percent points vs fractions) stays live in
  two dialects.
- **Option C — Extract a shared core limits model.** A single
  `RiskLimits`-style schema in core that both `RiskBudget` and `RiskConfig`
  embed. Trade-off: cleanest type story, but the most invasive change, and it
  would collide with the in-flight trade-ideas wave touching the same files.

## Decision

*(pending — owner call; recommendation below)*

**Recommendation: Option A**, phased. It matches the accepted direction — the
budget is *the* lever-handover mechanism, and autonomy stage 2 explicitly pairs
the "budget envelope" with a "runtime daily-loss breaker", i.e. two enforcement
points reading one appetite. It also means the open Codex wave is building on
the vocabulary that stays canonical: **#1091 can merge on top of this proposal
as-is**, because it deepens the budget side; what changes later is the live
side deriving from it rather than carrying its own numbers.

## Consequences

If Option A is accepted:

- New derivation seam: engine startup resolves the active `RiskBudget` version
  and seeds `RiskConfig.daily_loss_limit_pct` (from `max_daily_loss_pct`),
  `max_exposure_pct` (from `max_open_notional_pct`), and futures permission
  gating — with one normalization function owning the percent-points ↔
  fraction conversion, and one definition of the trading day shared by the
  approval gate (#1091's realized-loss window) and the runtime breaker.
- `BotRiskConfig.daily_loss_limit_pct` and `max_leverage` become transitional
  aliases, then are removed.
- Naming pass: `_pct` fields are normalized to one unit convention
  (`docs/naming.md` gains the rule); `max_loss_per_idea_pct` vs
  `max_position_pct_per_symbol` semantics documented in the glossary so
  loss-at-invalidation and notional exposure are never conflated.
- Follow-up work is filed as issues when the decision lands; the seven open
  trade-ideas PRs do **not** need rework under this option.

## Safety boundary

This decision authorizes no broker/API call, no live execution, and no
autonomy change. It only decides where risk-limit numbers live and which
direction derivation flows; live order submission remains gated by
`docs/DIRECTION.md` and recorded human approval.
