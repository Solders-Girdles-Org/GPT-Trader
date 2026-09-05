# Recorded experiment as the owner-facing product

---
status: accepted
date: 2026-09-05
deciders: RJ authorized first-principles redesign; Codex engineering implementation
supersedes: adopt-five-role-composition
---

## Outcome

Give RJ a reproducible answer to: **what did this trading decision do to my
simulated cash, positions and risk, and can I account for the result?** The
immediate product is a recorded-data experiment, with one entrypoint and one
journal. The longer-term autonomous entity in [Direction](../DIRECTION.md)
remains a destination to earn, not a description of the present product.

## Evidence and alternatives

The canonical checkout inspected on September 5 was `404aef1e`; remote source
had advanced to `5d6fd570`. Those are materially different systems. The latter
contains durable admitted intents and paper receipts, targeted reductions, and
[identified-fill accounting](../../src/gpt_trader/core/fill_accounting.py).
The existing [paper contracts](../paper_trading.md#durable-direct-fill-accounting)
explicitly distinguish gross P&L, fee coverage, simulated resolutions and unknown
opening inventory. These are useful implementation assets, not reasons to
claim a complete cash-accounting product already exists.

The current benchmark is arithmetic, not an embedded reasoning agent. Its
[accepted evaluation record](adopt-agentic-alpha-direction.md) found limited
relative TA evidence and directed future alpha work toward agent reasoning.
That does not establish model alpha, profitable trading, or soundness of every
existing orchestration layer.

| Approach | Benefit | Continuing cost / decision |
| --- | --- | --- |
| Full replacement of all broker/runtime code | One fresh implementation | Re-proves exchange semantics, uncertainty, reconciliation and historical migration without product evidence; rejected |
| Incrementally connect every existing engine/queue/report | Reuses many implementations | Preserves multiple state owners, profiles, approval-shaped simulation and operator handoffs; rejected as the default product path |
| Replace the local experiment workflow; selectively reuse pure semantics | One understandable causal loop and independently checked balances | Requires an explicit disposition for the old runtime; selected |

## Design

The [experiment slice](../../src/gpt_trader/features/experiment/) owns a pure
closed-bar transition and a SQLite journal. Input contains one source-labelled
hourly USD spot series and explicit simulation settings. There are no runtime
profile, environment, broker, account, service or scheduler parameters.

Each observation transaction includes the prior signal's next-bar execution,
cash/position settlement, fees, exit result, current observation, next decision
and checkpoint. A failed transaction commits none of these. The next process
resumes committed observations; replay and independent fill projection must
agree before further work. The source digest, data and settings bind the run.
An existing incomplete or foreign database is refused, not silently initialized.

The existing `BaselineProposer`, snapshot contract, `FillFact`, `PositionBaseline`
and `project_position` are reused. The benchmark's sizing is recorded as
advisory; executable quantity is recalculated from current simulated cash,
entry bounds, estimated stop loss including costs, and exposure limits, rounded
down. The simulation does not reuse static $10,000 equity as its account balance.
It does not parse numeric exit levels from prose or infer missing fill prices.

This deliberately small product admits one long spot position, immediate full
simulated fills, USD fees and a disclosed OHLC ordering model. It provides no
venue inventory, partial-fill transport, leverage, shorting or live execution.
Those are separate verified broker capabilities to integrate only when a real
product need and authority justify them. The simulation's admission rules are
not another live risk kernel.

## Component disposition

| Component / knowledge | Disposition and reason |
| --- | --- |
| Immutable observations, explicit stop/target/expiry, Decimal arithmetic, identity checks, peak-relative drawdown | Preserve semantics: causal evidence, accounting and failure bounds remain necessary |
| Baseline crossover / structured plans | Reuse as deterministic mechanics benchmark; not alpha adoption or a recommended strategy |
| Identified-fill projector and opening baseline | Reuse independently to verify inventory/gross P&L; add cash and net-fee accounting |
| Durable broker intent, receipt recovery, targeted reductions, venue increments/session calendars | Keep sound implementations and tests for the broker boundary; local atomic fills do not emulate external acknowledgment uncertainty |
| Regime/mean-reversion strategies, source data and past evidence | Retain for measured comparison; no new indicator-family investment or performance claims |
| Human approvals between purely local simulated steps | Retire from the default experiment; invoking a bounded local run is its authority, with no fabricated human approval events |
| Static advisory equity, prose exit parsing, midpoint fallback, separate attribution as full portfolio truth | Replace in the experiment with executable sizing, structured plans, identified fills and independent balances |
| Five-role composition / calendar-driven queue as mandatory product architecture | Superseded as the target; preserve installed compatibility contracts until an authorized cutover |
| BotConfig/ApplicationContainer for every offline function; legacy Goal Pipeline stages for ordinary development | Retire as universal requirements; explicit dependencies and direct task-to-PR ownership suffice here |
| Live approvals, kill switch, downward autonomy ratchets, measured promotion, model evidence provenance | Preserve purpose and authority; none are waived by easier development or simulation |

## Migration and retirement

This is the default local onboarding workflow, not a permanent parallel trader.
The installed paper cycle remains a compatibility operation with unique history.
Its source and stores are not migrated by this decision. See the concrete
[operational cutover proposal](paper-runtime-cutover.md), which requires RJ.

After source verification, the next product milestone is a held-out recorded
market experiment with independently checked balances and declared cost/data
assumptions. Success means useful decisions and credible evidence that RJ can
inspect, including no-trade/loss cases, not a favourable demo P&L. A model
decision channel must preserve complete input/output/model provenance and earn
forward-only comparison under the existing alpha decision; historical model
replay cannot establish edge. Do not build a provider or acquire credentials
merely to make the product's name contain GPT.

Before removing old runtime entrypoints, choose either pinned compatibility
operation or retirement, reconcile its outstanding positions/uncertain outcomes,
and preserve its original datasets, grants and journal. Reuse the retained
broker-boundary semantics if a later authorised live/forward-paper product is
justified. Do not make the new loop maintain every historical interface.

## Authority and engineering workflow

The goal authorizes local simulation and reversible source redesign. Agents own
implementation, focused failure tests, required local CI, current-head review,
and the standing gated PR merge. No SPEC/GOAL relay, extra owner sign-off or
fake runtime autonomy grant is required for these steps. Existing CI remains
in force; structural dependency rules gain the narrow experiment contract.

RJ owns changes to risk appetite for actual operation, external model/account
access, deployment/schedules, runtime migration, money movement and live order
authority. This decision authorizes none of them. The stricter legacy paper
approval flags protect their operational lane; they are not copied into a
broker-free experiment that cannot reach that lane.
