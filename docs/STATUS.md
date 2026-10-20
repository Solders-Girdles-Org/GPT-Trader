# Project Status — Source Pointers

---
status: current
---

This page points to implemented capabilities and their evidence.
[Information Architecture](INFORMATION_ARCHITECTURE.md) gives code, tests,
generated inventories and live GitHub state precedence over status prose.
If a pointer or claim disagrees with those sources, reconcile this page; it
cannot override observed behavior or an accepted decision.

[Direction](DIRECTION.md) owns the destination and execution gates,
[decisions](decisions/README.md) own durable choices, and the
[issue tracker](https://github.com/Solders-Girdles-Org/GPT-Trader/issues) owns
accepted next work. Source integration is separate from runtime deployment,
migration, authorization and measured promotion.

## Local product and continuation

The [recorded experiment](paper_trading.md#recorded-experiment) is the default
owner entrypoint. Its [implementation](../src/gpt_trader/features/experiment/)
and [failure tests](../tests/unit/gpt_trader/features/experiment/) demonstrate
observation, rule-based decision, bounded fills, independent accounting and
resume. The [product decision](decisions/recorded-experiment-product.md) owns
component disposition and the accepted [cutover](decisions/paper-runtime-cutover.md)
records the quiesced hourly paper job and its retained evidence. Source
integration does not migrate or restart that job.

The deterministic `ai-rulebook` channel adds an input-bound version schedule,
closed-bar entries and exits, and atomic activation/replay through the same
engine ([rulebook contract](paper_trading.md#declarative-rulebook-channel),
[#1356](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1356)). Its
starting rules are agent-written. The [portfolio author](paper_trading.md#portfolio-rulebook-author-default-off)
adds a strict per-coin rewrite schema, weekly closed-daily-bar context, an offline
spend bound and a replayable scratch-only smoke runner with the existing spend
ceiling ([#1357](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1357)).
Only synthetic/stub plumbing is tested; real token counts, reply quality and
forward performance remain unmeasured. The [full-window author run](paper_trading.md#full-window-author-run-default-off)
and its read-only per-coin report are the forward run path
([#1364](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1364)). Step 3 of the
[cheap-proofs decision](decisions/cheap-proofs-before-paid-ai-test.md) froze the
[rulebook-author protocol](../config/experiments/rulebook-author-2026-10-22/PROTOCOL.md)
(window 2026-10-22 to 2026-11-26), its reading and a [card draft](../config/experiments/rulebook-author-2026-10-22/CARD.md)
for RJ ([#1363](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1363)). On
2026-10-19 RJ said go on the card ([#1367](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1367)):
the plumbing smoke, then the forward run after 2026-11-26T02:00Z if the smoke passes, under one
$25 cap ($6.50 smoke, $18.50 forward). The spend is funded only by the API credits included with
RJ's Max plan, and the card's three departures and weak-result reading are confirmed. Before any
call, the Console must show that the key's organization has no purchased credit and that
auto-reload is off. An agent cannot read the Console, so that check is waiting on RJ; no model
call has been made.

Latest retained-runtime source also includes [durable execution receipts](../src/gpt_trader/features/idea_execution/executor.py),
[identified fill accounting](../src/gpt_trader/core/fill_accounting.py), and
[targeted reductions](../src/gpt_trader/features/trade_ideas/position_operations.py).
Their exact limitations live in the [paper contracts](paper_trading.md#durable-direct-fill-accounting).

Strategy scope follows the accepted
[retirement record](decisions/retire-optimize-and-dead-ta-strategies.md): the
optimizer slice, ensemble and `perps_baseline` shim are gone
([#1311](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1311)), the
replay [grid bridge](../src/gpt_trader/features/trade_ideas/optimize_bridge.py)
stays, the regime switcher and overlay are frozen comparison rows, and
[baseline](../src/gpt_trader/features/live_trade/strategies/baseline/) plus
[mean reversion](../src/gpt_trader/features/live_trade/strategies/mean_reversion/)
are the benchmark pair the reasoning proposer must beat.

RJ chose the long-term path on 2026-10-17: evidence first, in improvement
rounds toward a real-money autonomous bot
([decision](decisions/evidence-first-improvement-rounds.md)). Round one, the
held-out benchmark ([#1315](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1315)),
ran that day under its committed [protocol](../config/experiments/held-out-2026-09-06/PROTOCOL.md)
and read action D: neither fixed rule survives costs, and hold-the-coin beat
both ([result](decisions/held-out-benchmark-before-reasoning-proposer.md#result-2026-10-17)).
RJ chose round two the same day: an AI that manages the trades on its own,
or a pause until a stronger model if that cannot be built credibly today. The
agent's plan is one test of the strongest model available against a
predeclared bar, pausing if it does not pass
([decision](decisions/next-round-after-held-out-benchmark.md)).
W1 shipped: the default-off `ai-trade-manager` experiment channel with an
injected transport and journaled turns ([#1348](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1348)).
W2 shipped the Anthropic model transport with a hard spend ceiling
([model_transport.py](../src/gpt_trader/features/experiment/model_transport.py),
[#1350](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1350)); it runs
only behind `experiment run --model-transport anthropic` with a spend ledger
and cap, and no model call has been made. W3 froze the round-two protocol
for the window 2026-10-20 to 2026-11-24
([protocol](../config/experiments/round-two-2026-10-20/PROTOCOL.md),
[#1352](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1352)).
The build umbrella [#1252](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1252)
closed. On 2026-10-18 RJ declined the round-two card
([#1354](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1354)) as
"not yet": the frozen window lapses unrun, and no credential, spend ledger,
fetch or model call was made. The next round is
[cheap proofs before any paid AI test](decisions/cheap-proofs-before-paid-ai-test.md):
an AI-written rulebook, run by the engine without model calls, proven at no
spend before a dollar-sized card. Do not start the old runtime,
use its approval grants for the new product, or infer a deployment from this
page.

## Documentation continuation

[Architecture](ARCHITECTURE.md) routes the recorded experiment, retained runtime
and maintained development tools to their source. Detailed runtime references
and the dependency policy remain linked for their specific contracts; they do
not impose runtime composition on the experiment. The PR template names the
current [local verification contract](DEVELOPMENT_GUIDELINES.md#verify-before-a-pr)
without the retired agent-inventory freshness step.

## Dependency workflow continuation

The [coordinated dependency closeout](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1292)
updates the SHA-pinned workflow actions together and groups future CodeQL updates
to keep initialization and analysis on one version. It also refreshes the five
locked packages needed to clear the current dependency vulnerability audit.
setup-uv keeps the preceding cache-pruning policy explicitly. Follow that PR for
exact verification, review, merge and supersession receipts; source adoption
grants no release or runtime authority.

The [Ruff consolidation](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1302)
makes Ruff the single linter and formatter: `ruff format` replaces Black, and
Ruff's `UP` rules replace the pyupgrade hook. Pre-commit runs Ruff from
`uv.lock`. Its follow-ups are closed: Ruff now scans the tracked `data/`
packages ([#1308](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1308)),
bare `agent-pr-ready` auto-detects the branch's PR
([#1307](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1307)), and the
locked `multidict` clears the dependency audit
([#1306](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1306)). The
runtime checkout picks up the `multidict` bump only through its own refresh
procedure.

## Retained source pointers (2026-09-04 baseline)

Pointers checked against `main` through the merged transactional state,
strategy routing and paper cycle changes
([#1277](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1277),
[#1275](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1275),
[#1278](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1278)). No account,
operational state, runtime migration or graduation result was inspected for
this source snapshot.

| Implemented surface | Source and evidence |
| --- | --- |
| Trade-idea records, approval workflow, policy, budgets, audit and attribution | [Service](../src/gpt_trader/features/trade_ideas/service.py) and [feature tests](../tests/unit/gpt_trader/features/trade_ideas/) |
| Transactional admission and persistent trade state | [Persistence](../src/gpt_trader/features/trade_ideas/persistence.py), [transaction tests](../tests/unit/gpt_trader/features/trade_ideas/test_state_transactions.py), and accepted [storage/migration contract](decisions/transactional-trade-state.md) |
| Strategy-to-idea routing and in-process paper continuation | [Routing contract](specs/TRADE_IDEA_INTERFACES_DESIGN_NOTES.md#live-strategy-signal-routing-default-off), [routing tests](../tests/unit/gpt_trader/features/live_trade/engines/test_strategy_routing_contract.py), and [event lane](../src/gpt_trader/features/idea_execution/event_lane.py) |
| Paper-cycle failure isolation and venue price increments | [Paper execution contract](paper_trading.md#product-increments-and-partial-cycle-results), [cycle tests](../tests/unit/gpt_trader/features/idea_execution/test_cycle_failure_isolation.py), and [snapshot increment tests](../tests/unit/gpt_trader/features/recorder/test_snapshot_increments.py) |
| Portfolio monitors, approval policy and measured promotion scoring | [Monitors](../src/gpt_trader/features/trade_ideas/monitors.py), [policy](../src/gpt_trader/features/trade_ideas/policy.py), [scorecard](../src/gpt_trader/features/trade_ideas/scorecard.py), and [measured-outcome decision](decisions/adopt-measured-outcome-rubric.md) |

## Reading source and operational state separately

The [transactional-state decision](decisions/transactional-trade-state.md#adoption-and-rollback)
owns migration, writer coordination and rollback. Committed SQLite support does
not prove that an existing runtime store has been migrated.

The [routing contract](specs/TRADE_IDEA_INTERFACES_DESIGN_NOTES.md#live-strategy-signal-routing-default-off)
owns gate precedence and submission boundaries. Tracked
[paper configuration](../config/profiles/paper.yaml) is an implementation input;
a documentation review does not establish the effective mode of a running bot.

The [staged ladder](DIRECTION.md#the-ladder) and
[graduation contract](DIRECTION.md#graduation) require operational evidence and
recorded decisions. Existing mechanisms and green source tests do not by
themselves prove entry into bounded autonomy or approval for live orders.

## Keeping this page current

Update a source pointer when its capability changes, preferably in the same PR.
Keep detailed behavior in its owning contract, volatile work in GitHub issues
and PRs, and runtime evidence in its prescribed local stores. Preserve accepted
direction while correcting stale status text; an observed implementation does
not silently amend a policy or grant authority.
