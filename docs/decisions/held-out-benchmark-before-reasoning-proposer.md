# Run the held-out benchmark experiment before building the reasoning proposer

---
status: accepted
date: 2026-10-11
deciders: GPT-Trader project agent under the #1252 steering; RJ may reverse
supersedes:
superseded-by:
---

> **In plain language.** GPT-Trader's machinery works, but nothing yet shows it
> makes money after costs on prices it has not already seen. The next step is
> one bounded test: run the two existing fixed rules over about five weeks of
> hourly prices recorded after the old paper job stopped, with fees, beside
> "hold cash" and "hold the coin". Build the AI decision-maker only if that
> result gives it a clear bar to beat. The test needs one go-ahead from RJ
> before it runs, because it fetches public price data and writes project
> evidence; it uses no account, credential or money.

## Context

[#1252](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1252) asks for
one finite feasibility assessment comparing at most three paths, from current
code and retained evidence, before any new implementation. No experiment,
model call, fetch or run was performed for this record. Every number below is
read from retained evidence or code; three are simple arithmetic over retained
files and are labelled as such.

### What the retained evidence shows

| Evidence | What it proves | What it does not prove |
| --- | --- | --- |
| [Experiment slice](../../src/gpt_trader/features/experiment/) and its [tests](../../tests/unit/gpt_trader/features/experiment/) | Closed-bar decisions, next-open fills, cash/position settlement, fees, drawdown halt, crash-safe resume and independent reconciliation | Any investment value; the [paper guide](../paper_trading.md#recorded-experiment) says so |
| Real-data runs of 2026-09-06, `runtime_data/experiments/parked-closeout-20260906/` (`closeout.md`, `preserved-balance-audit.json`) | Eleven journals (ten complete, one partial) over real Coinbase hourly bars passed an independent Decimal re-derivation of every balance; paused-and-resumed BTC matched the uninterrupted run | Edge: 200 bars (2026-08-27 20:00 to 09-05 03:00 UTC, about 8 days) per symbol, already seen by the old paper job, baseline crossover only, 4–10 fills per case |
| Same runs, results at 10 bps fee + 5 bps slippage | Five $1,000 accounts net **+$1.77** in total (BTC +1.00, ETH −3.72, LTC +4.99, SOL −5.04, XRP +4.54); fees **$8.41**, so costs took about 83% of the gross result (arithmetic over the audit) | That this is better than doing nothing: prices moved −6.8% to +5.0% over the window (arithmetic over the retained inputs) and no passive row exists |
| Same runs at 60 bps + 10 bps (cost stress) | Four completed accounts net **−$39.33** (BTC −9.97, ETH −13.55, SOL −15.92, XRP +0.11); LTC stopped at 63/200 bars with no fills | — |
| Old hourly paper job, `runtime_data/retained-paper/20260906T0545Z/trade_ideas/` and its [cutover record](paper-runtime-cutover.md) | Two months of wall-clock paper decisions (2026-07-01 to 09-05) with 214 closed outcomes: mean reversion 139 closed, +$3.18 on about $688 of cumulative notional (+0.46% gross per trade); baseline 75 closed, +$0.32 (arithmetic over `closeout_attributions.jsonl` and `audit.jsonl`) | Net results: the retained `legacy-audit/report.md` calls this "not profitability evidence or fee-complete account reconciliation"; at the cost-stress rate a round trip costs about 1.4%, three times mean reversion's gross average |
| M5 replay tournaments ([#1241](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1241), summarised in [adopt-agentic-alpha-direction](adopt-agentic-alpha-direction.md)) | Mean reversion beat the crossover by +0.103 / +0.155 R on two 720-hour windows | Absolute profit: on window A every proposer's own expectancy was negative |
| Reasoning proposer: case-insensitive search of `src/` for "analyst" and "anthropic" on `main` (b71de410) | Nothing: no code, no recorded model decision | — |

Gaps that block a useful answer today:

- **No unseen data.** Collection stopped on 2026-09-05; every retained bar was
  already observed by the old job or used in M5 replays.
- **The stronger rule cannot run in the product.** `engine.advance` hard-codes
  `BaselineProposer`; mean reversion, the only rule with repeated relative
  edge, is not selectable.
- **No passive comparison.** Reports show net P&L against nothing.
- **Fee tier unknown.** Base and stress costs flip the sign; the repository
  does not record RJ's actual Coinbase fee tier.
- **Old journals need their pinned source.** A journal's identity includes a
  hash of every `.py` file under `src/gpt_trader/`. The retained journals carry
  `55095b2e…` from merge `03d9fec8`; current `main` hashes to `cd2144c3…`, so
  current `experiment inspect` refuses them by design. The closeout's inspect
  command names a worktree that no longer exists; re-inspect from a worktree
  at `03d9fec8`, which is still in history.

## Options

| | 1 — Validate the recorded-data product on held-out data | 2 — Default-off reasoning proposer (#1252 W0–W4) | 3 — Bounded evidence/rails repair |
| --- | --- | --- | --- |
| Proven mechanics | Experiment loop, reconciliation, resume, real-data audit | Proposer seam, rails, scorecard (legacy lane) | Retained archives hash-verified; seven open legacy positions identified |
| Usable investment evidence | None yet (see above) | None: no model decision has ever been recorded | Not applicable |
| Missing inputs | Bars after 2026-09-05; mean-reversion option; passive row; predeclared protocol | Model credential and spend ceiling (RJ-owned); a forward lane (old job quiesced, experiment inputs are fixed at creation); W1–W4 code; a benchmark result to beat | Compatibility runtime for the seven positions (RJ chose to retire it) |
| Expected learning | Whether any fixed rule survives realistic costs on unseen data, and the bar a model must clear | Whether model decisions beat mean reversion, forward only, after weeks to months of accrual | Little: $34.56 of simulated notional and a store migration only a restored job would need |
| Scope / effort | One PR plus one run; about one to two agent-days | Four or more PRs, a forward collection lane, ongoing model spend, months of wall-clock | One to three small PRs |
| Dependencies | RJ's go for the run (public prices, no account or money) | RJ decisions on credential, spend, schedule; ideally option 1's result | Reversing the accepted cutover |
| Stop criterion | Predeclared in [#1315](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1315): pooled mean-reversion net P&L ≤ 0 under stress costs ends deterministic-edge claims | Minimum-accrual gate (W4, undefined) with edge vs mean reversion ≤ 0, or spend ceiling reached | — |
| Next observable outcome | A reconciled per-symbol table: two rules × two cost cases beside cash and hold | First model idea with a complete evidence bundle in a forward journal | — |

## Decision

**Option 1, through [#1315](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1315).**
It is the cheapest test that can change what RJ funds, it is the milestone
[Direction](../DIRECTION.md#immediate-product-outcome) already names, and it
needs no new authority beyond one run approval.

Rejected for now:

- **Option 2** is sequenced after option 1, not abandoned. Starting it first
  commits RJ to a credential, ongoing spend and a forward schedule before
  anyone knows the bar it must clear. Its accepted constraints in
  [adopt-agentic-alpha-direction](adopt-agentic-alpha-direction.md) are
  unchanged. W0 (mean reversion in the Stage-2 cycle) is moot while that cycle
  is quiesced; #1315 makes mean reversion the benchmark in the experiment
  instead.
- **Option 3** as a standalone path: its useful parts (mean-reversion
  selection, passive comparison) are inside #1315; the rest serves a runtime
  RJ chose to retire. The pinned-source note above is recorded here instead of
  changing code.
- **Also considered:** handing the question to a commercial backtesting
  service. Rejected because the open question is this system's own decisions
  and costs on unseen data, which the existing slice already measures.

## Consequences

Agent-owned orchestration, in order:

1. Merge this record through the normal gate.
2. Deliver #1315 phase A (one PR: rule selection, passive row, held-out input
   builder, committed protocol; fixture tests only).
3. Ask RJ once for the phase B go-ahead, recommendation first.
4. After the go: fetch, run and audit exactly as the protocol states; post the
   table on #1315 and add the result to this record.
5. Read the result against the predeclared criterion and bring RJ the single
   product choice it implies: fund a forward reasoning test against the
   surviving bar, or stop alpha investment with the evidence recorded.

Until step 5, no W1–W4 implementation, model credential or forward lane is
started under #1252. The venue re-open trigger in
[retire-optimize-and-dead-ta-strategies](retire-optimize-and-dead-ta-strategies.md)
is unchanged.

## Safety boundary

This record authorizes no broker or account call, model call, credential,
service, schedule, order, money movement or autonomy change, and starts no run.
The phase B fetch reads public hourly prices without credentials, and only
after RJ's go. All [Direction](../DIRECTION.md) gates are unchanged.
