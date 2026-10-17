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
> one bounded test: run the two existing fixed rules, with fees, over the
> five weeks of hourly prices since the old paper job stopped, beside "hold
> cash" and "hold the coin". Those prices are not on disk yet; the test would
> download them. Build the AI decision-maker only if that result gives it a
> clear bar to beat. The test needs one go-ahead from RJ before it runs,
> because it downloads public price data and writes project evidence; it uses
> no account, credential or money.

## Context

[#1252](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1252) asks for
one finite feasibility assessment comparing at most three paths, from current
code and retained evidence, before any new implementation. No experiment,
model call, fetch or run was performed for this record. Every number below is
read from retained evidence or code. Figures marked *(derived)* are arithmetic
over retained files, not new results; the
[PR #1316 review](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1316#pullrequestreview-5483026608)
recomputed them independently.

The `runtime_data/...` paths below are git-ignored local evidence. They exist
only in the project home on RJ's Mac, not in the repository tree.

### What the retained evidence shows

| Evidence | What it proves | What it does not prove |
| --- | --- | --- |
| [Experiment slice](../../src/gpt_trader/features/experiment/) and its [tests](../../tests/unit/gpt_trader/features/experiment/) | Closed-bar decisions, next-open fills, cash/position settlement, fees, drawdown halt, crash-safe resume and independent reconciliation | Any investment value; the [paper guide](../paper_trading.md#recorded-experiment) says so |
| Real-data runs of 2026-09-06, `runtime_data/experiments/parked-closeout-20260906/` (`closeout.md`, `preserved-balance-audit.json`) | Eleven journals (ten complete, one partial) over real Coinbase hourly bars passed an independent Decimal re-derivation of every balance; paused-and-resumed BTC matched the uninterrupted run | Edge: 200 bars (2026-08-27 20:00 to 09-05 03:00 UTC, about 8 days *(derived)*) per symbol, already seen by the old paper job, baseline crossover only, 4–10 fills per case |
| Same runs, results at 10 bps fee + 5 bps slippage | Five $1,000 accounts net **+$1.77** in total (BTC +1.00, ETH −3.72, LTC +4.99, SOL −5.04, XRP +4.54); fees **$8.41**, about 83% of the $10.18 gain before fees. Slippage is built into fill prices; rebuilt from the journal fills it is $4.20, so fees plus slippage took about 88% of the $14.38 gain before any costs *(derived: totals from `preserved-balance-audit.json`, slippage from each journal's fill prices and the 5 bps setting)* | That this is better than doing nothing: prices moved −6.8% to +5.0% over the window *(derived from the retained inputs)* and no passive row exists |
| Same runs at 60 bps + 10 bps (cost stress) | Four completed accounts net **−$39.33** *(derived)* (BTC −9.97, ETH −13.55, SOL −15.92, XRP +0.11); LTC stopped at 63/200 bars with no fills | — |
| Old hourly paper job, `runtime_data/retained-paper/20260906T0545Z/trade_ideas/` and its [cutover record](paper-runtime-cutover.md) | Two months of wall-clock paper decisions (2026-07-01 to 09-05) with 214 closed outcomes: mean reversion 139 closed, +$3.18 on about $688 of traded notional, a gross return of +0.46% of traded notional; baseline 75 closed, +$0.32 *(derived from `closeout_attributions.jsonl` and `audit.jsonl`)* | Net results: the retained `legacy-audit/report.md` calls this "not profitability evidence or fee-complete account reconciliation"; at the cost-stress rate a round trip costs about 1.4%, three times mean reversion's gross return *(derived)* |
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
| Stop criterion | The [predeclared reading](#held-out-protocol-and-predeclared-reading) below: one run, read once; an inconclusive or cost-dependent rule gets at most one follow-up run, and rules and thresholds never change | Minimum-accrual gate (W4, undefined) with edge vs mean reversion ≤ 0, or spend ceiling reached | The seven open positions ($34.56 *(derived)*) are resolved from recorded post-fill prices under the cutover's compatibility path, or confirmed as retained "open in evidence with outcome unknown" per the [cutover record](paper-runtime-cutover.md#execution-record-2026-10-09); any other repair stops at one PR |
| Next observable outcome | A reconciled per-symbol table: two rules × two cost cases beside cash and hold-the-coin | First model idea with a complete evidence bundle in a forward journal | A reconciled inventory of resolved and retained-unresolved legacy positions |

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
  unchanged. W0 (mean reversion in the Stage-2 cycle) is deferred while that
  cycle is quiesced, not revoked; #1315 makes mean reversion runnable as the
  benchmark in the experiment meanwhile.
- **Strongest counterpoint (Codex, in the PR #1316 review).** Fixed-rule
  results cannot show whether an AI decision-maker adds value, and a
  default-off W1 skeleton with an injected transport needs no credential or
  spend, so it could be built in parallel. It waits because it yields no
  evidence until a credential, spend ceiling and forward lane exist, which are
  RJ's step-5 choice; #1252 asks for this assessment "before new
  implementation"; and #1315 phase A adds the rule-selection seam the skeleton
  would plug into, so building after it avoids rework. If RJ funds a forward
  test at step 5, the skeleton is the first item.
- **Option 3** as a standalone path: its useful parts (mean-reversion
  selection, passive comparison) are inside #1315; the rest serves a runtime
  RJ chose to retire. The pinned-source note above is recorded here instead of
  changing code.
- **Also considered:** handing the question to a commercial backtesting
  service. Rejected because the open question is this system's own decisions
  and costs on unseen data, which the existing slice already measures.

## Held-out protocol and predeclared reading

Phase A of #1315 commits this protocol as a file before any data is fetched;
it may add mechanics but must not loosen a threshold. That file is
[config/experiments/held-out-2026-09-06/PROTOCOL.md](../../config/experiments/held-out-2026-09-06/PROTOCOL.md),
with its parameters in
[protocol.json](../../config/experiments/held-out-2026-09-06/protocol.json).

**Window and data.** Symbols: BTC, ETH, LTC, SOL, XRP, LINK, AVAX, DOT (the
old job's set, chosen before any return is seen). Each symbol's input is 912
contiguous closed hourly bars: 72 warm-up bars from 2026-09-03T00:00Z, then 840
scored bars from 2026-09-06T00:00Z up to the fixed cutoff 2026-10-11T00:00Z.
Warm-up bars are indicator history only (baseline needs 53, mean reversion
20). Decisions are taken only at the close of a scored bar, so the earliest
possible entry fills at the open of the second scored bar. Gaps are refused,
never filled. A symbol is **scored** when its complete 912-bar input is
accepted by the parser and the run processes every bar, including runs that
halt or never trade.

**Rules and costs.** Baseline crossover and mean reversion, default settings,
no tuning. Two cost assumptions, because RJ's fee tier is unknown: base 10 bps
fee + 5 bps slippage per fill, stress 60 + 10. Each symbol × rule × cost case
is its own $1,000 account with the retained settings (1% stop risk, 20%
exposure cap, 10% drawdown halt); pooled results are sums over the scored
symbols.

**Comparators.** Cash: zero. Hold-the-coin: the same $1,000 account buys at
the first scored bar's open with the 20% exposure cap, paying the case's entry
fee and slippage, and holds to the cutoff; the full-size price move is shown
for context only. Pooled hold-the-coin covers exactly the rule's scored
symbols. Benchmark: mean reversion is the accepted standing benchmark under
[adopt-agentic-alpha-direction](adopt-agentic-alpha-direction.md), with
baseline as the second member of the benchmark pair.

**End valuation.** Anything still held at the cutoff, whether a rule's open
position or hold-the-coin, is valued the same way: at liquidation value, the
last scored close less that case's exit slippage and fee. Open positions are
still reported as open; nothing is sold in the journal.

**Audit and reporting.** Every balance is re-derived independently in Decimal
from the journal fills, without the engine's own reconciliation. One case is
paused and resumed and must equal its uninterrupted run. Every case is
reported, including no-trade, loss, halt and open-at-cutoff, with fees and
slippage as shares of the gain before costs. No case is dropped or re-run.

**Outcome per rule and cost case**, on pooled net P&L at end valuation:

| Outcome | Condition |
| --- | --- |
| Inconclusive | Fewer than 7 of 8 symbols scored, or fewer than 30 completed round trips (entries closed by stop, target or expiry before the cutoff) pooled for that rule under that cost case. No claim is made. |
| Pass | Conclusive; net above zero and above hold-the-coin |
| Positive but trails hold-the-coin | Conclusive; net above zero, at or below hold-the-coin |
| Loses less than hold-the-coin | Conclusive; net at or below zero, above hold-the-coin |
| Fail | Conclusive; net at or below zero and at or below hold-the-coin |

**Verdict per rule**, combining its two cost cases, checked in this order:

1. **Inconclusive** if it is inconclusive under either cost case.
2. **Survives costs** if it passes under both cost cases.
3. **Cost-dependent** if it passes under exactly one cost case. It never
   counts as surviving. RJ's actual taker fee may be recorded beside it, but
   only a new, separately approved run at that exact fee (5 bps slippage,
   otherwise the same protocol) can change the verdict.
4. **Does not survive** otherwise.

**Follow-up runs.** An exact-fee run is read as a single case with the
outcome table: Pass makes the rule's verdict Survives at RJ's fee, and any
other conclusive outcome makes it Does not survive. An extension is read with
both cost cases and the verdict order above. The original base/stress verdict
is always reported beside the follow-up. A rule still inconclusive or
cost-dependent after its one follow-up run is treated as Does not survive for
the combined reading, and its coverage is reported. After follow-ups, the
combined reading below is applied again.

**Forward-test bar.** Any forward reasoning test must beat mean reversion,
cash and hold-the-coin, and also baseline when baseline survives costs. This
follows [adopt-agentic-alpha-direction](adopt-agentic-alpha-direction.md),
which makes beating mean reversion the bar; dropping mean reversion from it
is RJ's call. The benchmark pair always runs alongside.

**Combined reading.** Claims are limited to these two rules, these cost
assumptions and this window. The first matching action applies:

| Action | When | What the agent does |
| --- | --- | --- |
| A — Propose a forward test | Either rule survives costs | Propose a funded forward reasoning test to RJ, judged against the forward-test bar. The other rule's verdict is reported and gets no follow-up. |
| B — Exact-fee run | Neither survives; either is cost-dependent | Record RJ's fee tier and propose one separately approved run at it for each cost-dependent rule, plus the extension below for any inconclusive rule. Read them as stated under follow-up runs. |
| C — Extension | Neither survives nor is cost-dependent; either is inconclusive | Each conclusive "does not survive" stands. Propose one extension for each inconclusive rule, using this protocol over the next five-week window, which needs a fresh RJ go, and read it as stated under follow-up runs. RJ's fund-or-stop choice waits for its result. |
| D — Fund-or-stop choice | Both rules do not survive | Report "neither tested fixed rule survives these cost assumptions on this window". Bring RJ the choice between a funded forward reasoning test, judged against the forward-test bar, and stopping alpha investment, with a recommendation. |

As a grid (mean reversion down the side, baseline across the top):

| | Survives | Cost-dependent | Does not survive | Inconclusive |
| --- | --- | --- | --- | --- |
| **Survives** | A | A | A | A |
| **Cost-dependent** | A | B | B | B |
| **Does not survive** | A | B | D | C |
| **Inconclusive** | A | B | C | C |

Every forward reasoning test is judged against the forward-test bar defined
above. The 30-trade floor is a coverage minimum, not a
significance test: five weeks of hourly bars can show that an edge fails to
survive costs, not prove that one exists. An extension or exact-fee run is
offered at most once per rule.

## Result (2026-10-17)

The run followed the protocol unchanged on main ffa9c45b; the full table,
every case and the record of attempts are on
[#1315](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1315#issuecomment-6238693084),
and the evidence is in `runtime_data/experiments/held-out-2026-09-06/` in the
project home. The first fetch was refused by a candle-fetcher defect (each
300-hour request lost its window's first bar). It was fixed in
[#1342](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1342) before
any input existed; each symbol was then fetched once and each input run once.
Capture rebuilds (8/8), the pause/resume case and the independent Decimal
re-derivation (32/32) all agree.

**Action D: neither tested fixed rule survives these cost assumptions on this
window.** Both rules are conclusive *Fail* under both cost cases (8/8 symbols
scored, 104–153 pooled round trips):

| Rule | Costs | Pooled net | Hold-the-coin | Gain before costs |
| --- | --- | --- | --- | --- |
| Baseline | base | −$39.70 | +$213.02 | +$24.19 |
| Baseline | stress | −$266.73 | +$194.13 | +$19.25 |
| Mean reversion | base | −$105.52 | +$213.02 | −$13.41 |
| Mean reversion | stress | −$434.55 | +$194.13 | −$13.84 |

Baseline's gain before costs was about 0.11% of notional per entry, against
about 0.30% of costs for a round trip at the base rate *(derived)*; mean
reversion lost before costs. The next round is proposed in
[next-round-after-held-out-benchmark](next-round-after-held-out-benchmark.md).

## Consequences

Agent-owned orchestration, in order:

1. Merge this record through the normal gate.
2. Deliver #1315 phase A (one PR: rule selection, passive row, held-out input
   builder, committed protocol; fixture tests only).
3. Ask RJ once for the phase B go-ahead, recommendation first.
4. After the go: fetch, run and audit exactly as the protocol states; post the
   table on #1315 and add the result to this record.
5. Read the result against the predeclared criterion and bring RJ the single
   product choice it implies: fund a forward reasoning test judged against
   the forward-test bar, or stop alpha investment with the evidence recorded.
   *Update 2026-10-17: under RJ's
   [improvement-rounds decision](evidence-first-improvement-rounds.md), action
   D opens the next round: the agent proposes what to change and how to test
   it on unseen data, rather than recommending a stop. The reading itself is
   unchanged.*

Until step 5, no W1–W4 implementation, model credential or forward lane is
started under #1252. The venue re-open trigger in
[retire-optimize-and-dead-ta-strategies](retire-optimize-and-dead-ta-strategies.md)
is unchanged.

## Safety boundary

This record authorizes no broker or account call, model call, credential,
service, schedule, order, money movement or autonomy change, and starts no run.
The phase B fetch reads public hourly prices without credentials, and only
after RJ's go. All [Direction](../DIRECTION.md) gates are unchanged.
