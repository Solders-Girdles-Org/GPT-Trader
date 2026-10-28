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
[Linear GPT-Trader project in Work](https://linear.app/rj-openclaw/project/gpt-trader-27e81625d145)
owns accepted tasks and their current owner/resumption trigger. GitHub retains
code, PR review, CI and linked source history. Source integration is separate from runtime deployment,
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
The later plumbing smoke below measured real token counts and accepted replies;
forward performance remains unmeasured. The [full-window author run](paper_trading.md#full-window-author-run-default-off)
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
call, the Console must show three things: the key belongs to the organization linked to the Max
plan, that organization has no purchased credit, and auto-reload is off. If any of them can't be
confirmed, work stops and goes back to RJ through Lead. No agent buys credits or changes billing
settings. RJ confirmed all three on 2026-10-20. The smoke's first call was then rejected unbilled
(the reply schema's grammar was too large), so the protocol was
[re-frozen](../config/experiments/rulebook-author-2026-10-22/PROTOCOL.md#re-freeze-after-the-smoke-found-a-run-path-defect-2026-10-20)
with the same window, settings and caps. The re-run smoke then failed its acceptance check: the
model wrote `reversion` and `mean_reversion` for `mean-reversion` on two of three turns. The reply
schema now lists the parser's entry names. RJ
[read that as our defect](../config/experiments/rulebook-author-2026-10-22/PROTOCOL.md#the-re-run-smokes-entry-names-2026-10-20-rjs-reading-pending)
and asked for a re-run, so the frozen source is `448efed4` (digest `7adac9b2…e223`). On 2026-10-20
the smoke passed on that source: three of three turns were accepted, and the ledger matched the
journal. It spent $1.98 of the $6.50 smoke cap in total. The forward run waits for 2026-11-26T02:00Z.

The [closing-state disposition](decisions/evidence-first-improvement-rounds.md#decision)
records [RJ's 2026-10-23 answer (WO#252, 6378841453)](https://github.com/Solders-Girdles/workspace-operations/issues/252#issuecomment-6378841453):
**maintenance only until #1367's forward round is read under its predeclared
reading and the result is recorded**. What remains is #1367's run and reading;
then RJ chooses close or stage 4, which still needs RJ's explicit grants.
The closing-state date is **after November 25, 2026, 19:00 MST (estimate, not deadline)**;
#1367's protocol and its own run start remain unchanged. #1212, #1224, #1232
and #1238 remain unstarted, paused under that answer. No stage is marked done;
approved stage text is retained. This documentation starts no run and changes
no protocol, schedule or spend cap.

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

## Task continuation

[Linear routing](DEVELOPMENT_GUIDELINES.md#pr-flow) owns task handoff. RJ-747–RJ-751
retain the five original operational items and their holds, including the
November 25 forward-test window. A moved GitHub original is retained history,
not operational completion. The native connector executes promoter plans;
terminal `finding_id` matches are reused without reopening. This replaces the
standalone GitHub writer rather than adding a credentialed Linear client.
Reconsider an automated client only if a supported native tool cannot serve a
required consumer; no credential or scheduler change is part of this cutover.

## M4 promotion evidence continuation

[RJ-747](https://linear.app/rj-openclaw/issue/RJ-747) remains Waiting. Its
November 8 checkpoint is a review date, not permission to resume the old loop.
The [July 7 closeout](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1212#issuecomment-4910064978)
completed all six M4 implementation workstreams, including the paper drawdown
setting and suppression telemetry consumer. Its 4/7 scorecard is historical;
the copied unchecked boxes are not remaining build work.

The [October hold](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1212#issuecomment-6110909334)
named #1252 and #1315. Those literal prerequisites closed: the
[held-out result](decisions/held-out-benchmark-before-reasoning-proposer.md#result-2026-10-17)
was action D (both fixed rules failed both cost cases), and
[#1252's W1–W3 build](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1252#issuecomment-6245004316)
handed execution to #1354. RJ declined that card; the
[cheap-proofs decision](decisions/cheap-proofs-before-paid-ai-test.md)
records the October 20–November 24 round as lapsed **UNRUN**, not failed evidence.
The successor is [RJ-751 / #1367](https://linear.app/rj-openclaw/issue/RJ-751),
with frozen source `448efed49c9677e80c85d743c918f4a547a7d0ff` and forward
not-before **2026-11-26T02:00Z / November 25, 19:00 Phoenix**. The
[smoke receipt](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1367#issuecomment-6297200724)
reports three accepted turns and reconciled spend; it is plumbing proof,
explicitly not research evidence. The later
[offline rehearsal](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1367#issuecomment-6329685087)
is also NOT-EVIDENCE and did not perform the independent Decimal audit.

The seven rows below map the implemented
[scorecard](../src/gpt_trader/features/trade_ideas/scorecard.py) to retained
receipts. They are not a newly measured scorecard. July's aggregate 4/7 supplies
no numeric eligibility, attribution or calibration rates in that receipt.

| Gate and implemented measurement | Actually recorded evidence | Remaining evidence prerequisite if M4 resumes |
| --- | --- | --- |
| `track_record_depth`: ≥200 closed ideas and ≥60 days | July: 23/200, 6/60 days, failing. The later [cutover](decisions/paper-runtime-cutover.md#execution-record-2026-10-09) records 214 filled closeouts and 21 unfilled expiries, with seven filled outcomes unknown. | A valid observation-window report; raw historical counts do not certify a current gate. Replay cannot replace wall-clock depth. |
| `eligibility_pass_rate`: ≥90% of proposed ideas | July aggregate only; no current per-row rate established here. | Proposed-idea eligibility numerator and denominator in the scored window. |
| `attribution_coverage`: 100% of closed ideas | W2 shipped; cutover records 235 attributions, including 21 unfilled expiries. Seven filled ideas remain open in evidence with outcome unknown. | Closed-lifecycle denominator and complete attributed outcomes in a valid window; preserve unknowns rather than invent closeouts. |
| `risk_calibration`: ≥95% of attributed losers within recorded max loss | Measurement code shipped; no current calibrated-loser ratio established by these receipts. | Attributed losing closeouts with recorded realized loss and max-loss estimates. |
| `expectancy`: average R >0 | July: −0.56 R over two closeouts, failing. The held-out test later failed net of costs; that experiment result is not this R gate. | Comparable attributed closeouts with realized amounts and max-loss denominators, over the valid window. |
| `benchmark_edge`: candidate average R minus baseline average R >0 | July: non-baseline closeouts missing. M5 replays showed relative mean-reversion edge but negative absolute expectancy on window A; #1315 then failed both rules net of costs. | Comparable baseline and candidate closeouts in the same wall-clock window; separately labelled replay comparisons remain supporting evidence. |
| `max_drawdown_from_peak`: window worst ≤configured budget limit | July: 0.01% versus accepted 15% paper budget, pass. W5 and ratchet plumbing completed. | Attested-equity ledger points and the applicable budget for the scored window; July's pass is not a current measurement or a new risk-setting request. |

`build_replay_evidence` labels replay evidence alongside the wall-clock scorecard
and never feeds a gate verdict. M4's unchanged done-test is all seven gates
measurable with honest pass/fail across wall-clock and replay evidence, with no
not-yet-measurable rows; it does not require all seven to pass to close the
umbrella. Promotion separately requires passing evidence and healthy loop/audit
checks, kill-switch and daily-loss demonstrations under the
[accepted rubric](decisions/adopt-measured-outcome-rubric.md), independent review
and RJ's explicit grant. The experiment reading substituted for the old scorecard
only within the separately accepted experiment card; it does not mark M4 done.

Next: retain maintenance until RJ-751's frozen forward run and reading are
recorded, then RJ chooses close or stage 4 under the
[closing-state decision](decisions/evidence-first-improvement-rounds.md#decision).
Any return to legacy accrual needs its own decision under the
[cutover](decisions/paper-runtime-cutover.md#decision); the retired hourly loop
is not a continuation step. Separate [RJ-748](https://linear.app/rj-openclaw/issue/RJ-748),
[RJ-749](https://linear.app/rj-openclaw/issue/RJ-749) and
[RJ-750](https://linear.app/rj-openclaw/issue/RJ-750) retain their own scopes and
gates. This reconciliation records no new measurement or operational grant.

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
locked `aiohttp` clears the three 2026-10-19 aiohttp advisories
([#1370](https://github.com/Solders-Girdles-Org/GPT-Trader/pull/1370)). Locked
`certifi 2026.10.19` and `virtualenv 21.14.12` clear the four advisories
tracked in [#1387](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1387),
with `packaging 26.3` required by virtualenv. The subsequent virtualenv
GHSA-cx6f-vjhg-g3cc is cleared by the 21.14.13 lock (RJ-905).
The development-only pip-audit dependency msgpack is locked to 1.2.3 for
CVE-2026-114613. Trivy remains active in the release-image workflow; RJ-905
keeps its 247 findings from the June image scan open until the next authorized
image build and scan can establish which remain. No image publication is
authorized by that maintenance item. `filelock` is a direct runtime
dependency for cycle, budget and autonomy locking, with a security floor of
4.2.1 and a 4.2.4 lock clearing the advisories in
[#1393](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1393). The basic
`FileLock` callers do not use the read/write lock protocol changed in 4.0.
Keeping the constraint only in development dependencies would leave runtime
installs without their declared locking dependency. The runtime checkout picks
up these dependency updates only through its own refresh procedure.

The base install keeps Robinhood Agentic dependencies optional: the transport
loads `anyio` only for credential-storage and OAuth callback operations. A
missing MCP extra raises the adapter's install-extra error at connection time.
The [isolated import tests](../tests/unit/gpt_trader/features/brokerages/robinhood/agentic/test_optional_imports.py)
cover package import, CLI help and missing-extra failure. Promoting `anyio` to
the base dependencies was rejected because this adapter is optional; deferring
its import preserves the existing asynchronous backend behavior.

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
Keep detailed behavior in its owning contract, volatile work in Linear with linked GitHub PRs, and runtime evidence in its prescribed local stores. Preserve accepted
direction while correcting stale status text; an observed implementation does
not silently amend a policy or grant authority.
