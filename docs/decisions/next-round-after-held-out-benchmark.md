# Next improvement round after the held-out benchmark

---
status: accepted
date: 2026-10-17
deciders: rj
supersedes:
superseded-by:
---

> **In plain language.** Round one is done: both fixed trading rules lost
> money after fees on five weeks of unseen prices, and simply holding the
> coins did better. RJ chose round two on 2026-10-17: build toward an AI that
> manages the trades on its own, with whatever rules it chooses, and pause the
> project if that cannot be built credibly with today's models. The agent's
> answer is that a fair, cheap test of it can be built now, but the public
> studies it found give no convincing evidence that today's models beat "just
> hold" after fees, so whether it is useful is unproven. Round two is one
> bounded test with a pause rule written down in advance: if the AI does
> not beat holding, cash and the old rules over five weeks that have not
> happened yet when the test is frozen, the project pauses until a stronger
> model is released (the agent's proposed reading, which RJ confirms). The
> test uses the strongest model available when it is frozen. The API key,
> spending cap and run come to RJ later as one card, which also offers
> pausing now instead.

## Context

Under RJ's [improvement-rounds decision](evidence-first-improvement-rounds.md),
a weak result opens the next round, and the agent proposes what to change and
how to test it fairly. The [held-out benchmark](held-out-benchmark-before-reasoning-proposer.md#result-2026-10-17)
read **action D** on 2026-10-17: neither tested fixed rule survives the cost
assumptions on that window.

What round one showed (8 coins, 2026-09-06 to 2026-10-11, one $1,000 account
per coin, at most 20% of it in the market):

- **Costs are the wall.** A buy-and-sell round trip costs about 0.30% at the
  base fee assumption. The moving-average rule found about 0.11% per trade
  before costs, so fees and slippage took nearly three times its edge. Its
  pooled result was −$39.70 at base costs and −$266.73 at stress costs
  *(derived from the run's `read.json` and journals)*.
- **Mean reversion showed no edge at all.** It lost $13.41 even before
  costs, and −$105.52 / −$434.55 after them.
- **Holding won.** The coins rose (from −0.8% to +37%); holding them at the
  same 20% exposure made +$213 pooled. Any useful approach has to beat that
  in rising markets and stay out in falling ones.

This agrees with earlier evidence: no tuned moving-average configuration was
positive on both evidence windows (0 of 24; M5, [#1241](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1241)),
and the old paper job's small gross gains did not cover stress costs.

## Options

- **Option A — Forward AI test (recommended).** Build the AI trade-proposer
  ([#1252](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1252), W1–W4)
  default-off, with every model input and output recorded, and read it over a
  predeclared forward window against the
  [forward-test bar](held-out-benchmark-before-reasoning-proposer.md#held-out-protocol-and-predeclared-reading):
  it must beat mean reversion, cash and hold-the-coin at the same costs (baseline
  joins the bar only if it survives costs; it did not). Round one's lesson goes
  into its design: trade rarely, treat holding and staying out as normal
  choices, and count every trade's cost. Building is agent work with no spend.
  Running it needs RJ's separate go on an API credential, a spending cap and
  the forward window. Weeks of building, then weeks of forward time. It is the
  most direct test of whether AI decisions add value, the direction the
  project already accepted ([agentic alpha](adopt-agentic-alpha-direction.md)).
- **Option B — Cheaper fixed-rule round.** Redesign the fixed rules to trade
  less (for example, decide on 4-hour or daily bars, add a trend filter), using
  round one's window as design data, and test them on the next unseen window,
  2026-10-11 to 2026-11-15 (readable after 2026-11-15). No model spend, about a
  month. The strongest alternative; not recommended because no fixed rule
  measured here has yet cleared costs (no tuned configuration was positive on
  both M5 windows, and the best relative performer, mean reversion, had no
  edge before costs on this window). A pass would still advance the project,
  as a stronger benchmark, but it would not test AI decisions.
- **Option C — Stop alpha investment.** Keep the evidence and stop. Always
  RJ's choice; not recommended, because RJ's conditions treat a weak result as
  the start of the next round.

## Decision

**RJ, 2026-10-17** (the `rj-answer` comment on
[#1344](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1344)): not
option A as framed, and not C. Build toward an AI that manages the trades on
its own; which strategy or rules it uses is open. If that cannot be built
credibly with today's models, pause the project until a stronger model is
available rather than build something less. RJ's words: "The vision for the
project is the AI being able to manage the trades on its' own. I don't care
which trading strategy it uses so the rules can be anything we want. I don't
want a trade proposer though."

RJ's conditions, as recorded on #1344:

1. No design in which the AI only suggests trades. The AI may choose, write
   or change its own rules, fixed rules included.
2. Practice money and every live-trading gate stay as they are.
3. When model spending is needed, the credential, the spending cap and the run
   come to RJ as **one card**, not three. RJ's Claude API key uses credits
   that expire unused.
4. Open question for the agent: can it be built now? Answer with evidence; if
   not, record the pause and the test that would restart it.

### What "proposer" meant

In [#1252](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1252),
"proposer" is only the name of the code slot a decision comes from. In the
recorded experiment the decision goes straight to the simulator: the
experiment's limits (exposure cap, stop, drawdown halt) apply and the
simulated trade is placed, with no person approving each one
([engine](../../src/gpt_trader/features/experiment/engine.py), and
[Direction](../DIRECTION.md#immediate-product-outcome): "Ordinary local
simulations do not require per-trade human approval"). Today's engine,
though, only asks a rule for entries: while a position is open, its fixed
stop, target and expiry decide the exit. Managing trades means the AI also
reviews open positions and may exit or keep them, so W1 adds that. With it,
the design meets RJ's aim. From here the round-two channel is called the
**AI trade manager**.

### Can it be built now? (agent assessment, 2026-10-17)

**A bounded, fair simulation test of an AI trade manager is feasible now.
Useful performance is unproven, and public evidence leans against it.** That
is not yet the credible capability RJ's condition asks for; only a test can
show whether it exists. Round two therefore runs as one bounded, cheap test
with the pause written down in advance, not as an open-ended build.

What supports building now:

- **The machinery exists.** The experiment slice already takes one decision
  per closed bar through a single seam (`propose` in
  [benchmarks.py](../../src/gpt_trader/features/experiment/benchmarks.py)),
  journals every event, fills at the next open with fees and slippage, and
  re-derives every balance independently. Round one proved it on 32 cases.
  An AI channel is a new decision source in that seam, a review step for
  open positions and a recorded evidence bundle, not a new system.
- **A held-out test is possible without a live forward lane.** A model
  cannot honestly be tested on prices it was trained on; that is why
  [agentic alpha](adopt-agentic-alpha-direction.md) made model channels
  forward-only. Models do recall exact prices from before their cutoff
  (Lopez-Lira et al., arXiv 2504.14765), and testing after a model's
  training cutoff is standard practice in published benchmarks (StockBench,
  arXiv 2510.02209). Round two goes further: the prompt, rulebook, cadence,
  pinned model version and reading are frozen before a window that **starts
  after the freeze**, so no model available at the freeze can have been
  trained on it, whatever its stated cutoff. With no web or other tools,
  replaying the window's recorded bars after it closes protects against
  training contamination and against the builders tuning on the outcome. It
  does not reproduce live latency, outages or slippage; those are disclosed
  limits, not solved ones. It reuses the round-one simulator, costs,
  comparators and reconciliation; W1 adds the AI channel and position review
  to the engine.
- **The strongest model available at the freeze.** RJ's condition is about today's
  models, so a test on a weaker one could not settle it. Round two uses the
  strongest frontier-tier model of RJ's workspace model stack available on
  RJ's existing Claude API credits at W3's freeze: Claude Fable 5.1 as of
  2026-10-17. The strongest alternative is GPT-6 Astra, the other
  frontier-tier model; it is not the default because it needs a separate
  paid OpenAI API credential. The card offers it as an optional second arm
  on the same frozen window, read separately under the same protocol.
  *(W3 found it cannot join this window: see
  [round-two protocol](#round-two-protocol-w3).)*
- **It is cheap.** At Claude Fable 5.1's list price ($10 input / $50 output
  per million tokens, Anthropic API documentation read 2026-10-17) and an
  *assumed* 6,000 input and 3,000 output tokens per decision (thinking is
  billed as output, so W3 freezes the effort level), a turn costs
  about $0.21. The bar must pass under both cost cases, each its own
  account, so the AI runs 16 accounts (8 coins × 2 cost cases) over a
  35-day window: about $120 deciding once a day and about $710 every four
  hours, before extra turns on fills and stops and before the optional
  second arm *(estimates; before the card, W2 estimates input tokens
  offline from the requests W1's fake transport builds, with no API key or
  model call, states its method and safety margin, and bounds output by the
  frozen `max_tokens` and effort level; real counts are measured only
  during the approved run)*. W3 sets a cadence no faster than
  every four hours: round one showed costs punish frequent trading, and
  hourly would cost
  about $2,800. The engine steps through hourly bars, so a slower
  cadence is a W1 setting.
- **Measured request size (W2, 2026-10-17).** `experiment estimate-spend`
  rebuilt every four-hourly request from round one's 16 recorded inputs
  offline, with no key or call, using W1's context (instructions, 48 bars,
  account, starting rulebook) and W2's reply schema. The context is
  6,400–6,900 bytes per turn. *Method and margin:* a token covers at least one
  byte, so a request's bytes plus a 2,000-token allowance for what the API
  adds bound its input tokens: at most 10,497 per turn, mean 10,220. A real
  count is likely well below the bound, since a token usually spans several
  bytes, but it is known only from the first approved call. Output is bounded
  by `max_tokens` (8,000 as a placeholder until W3 freezes it), thinking
  included. A rulebook rewritten to its 4,000-character limit adds about
  4,000 bytes, and each call is bounded from its own bytes. For the 3,360
  cadence turns (16 accounts × 35 days × 6), the worst case is about $1,773
  *(derived: the input bound at the cache-write rate of $12.50 per million,
  which the spend stop reserves, plus 8,000 output tokens at $50 per million,
  about $0.53 a turn)*. At the input bound and the assumed 3,000 output
  tokens a turn costs about $0.25, about $850 *(derived)*, against the
  earlier $710. These figures replace the assumed 6,000 input tokens; W3
  reran the command on its frozen settings and added turns for fills
  ([round-two protocol](#round-two-protocol-w3)).

What argues against expecting a pass (public evidence, gathered 2026-10-17;
facts from the sources, judgments marked):

- The only costed, live crypto study found here ("Can AI Make Money in
  Crypto?", arXiv 2609.34510, September 2026; 4-hour decisions, 0.05–0.10%
  fees, holding and closing allowed) found no LLM or agent method made money
  in its 2025 backtest. Live, none of its four deployed methods (one LLM and
  three machine-learning methods) beat buy-and-hold BTC (+11.6%; the LLM
  made +3.7%), and live slippage was about six times paper's.
- Nof1's Alpha Arena real-money crypto season (October–November 2025, as
  reported second-hand): two of six models finished up, four lost 42–59%;
  in its stock season (November–December 2025) only one of the models
  finished up. These were older models trading leveraged perpetuals.
- A 2026 review of five well-known LLM trading frameworks ("Alpha Illusion",
  arXiv 2605.16895) found most cost categories unmodelled, and in its own
  one-year rerun two frameworks ended below buy-and-hold after costs.
  FINSABER (arXiv 2505.07078) found earlier LLM advantages mostly vanish over
  longer tests.
- On the other side, StockBench (stocks, March–June 2025, after the tested
  models' cutoffs) found 8 of 13 models beat equal-weight buy-and-hold
  (+0.4%), the best by about 2 points, with costs not described and no
  significance test.
- No primary-source, costed test of the current Claude, GPT or Gemini
  generations against a hold benchmark was found. Self-published arenas with
  current models (TradeRank) publish too little method to read.
- *Agent judgment:* the published results do not show that today's models
  cannot do this in this setting: they used leverage, older models, stocks
  or no costs, and the one costed crypto study tested a single LLM. Among
  the sources reviewed, none gives convincing, cost-complete evidence that
  current models reliably beat holding in this crypto setting. Five weeks
  on eight correlated coins is one market regime, so even a pass is a first
  reading, not proof.

**Strongest option rejected: pause now on the public evidence.** It saves a
few agent-days of building and about $120–710 of model credits. It is
rejected because the public tests are not this setting, the test RJ's
condition asks for costs little, RJ's credits expire unused, and pausing
without a reading of our own would leave the restart question just as open
as today.

### Round two

1. **Build, default-off, no spend** (agent work, filed under #1252): the AI
   trade manager channel in the experiment slice with an injected transport
   that also reviews open positions, and a recorded evidence bundle (W1);
   the real model transport with a hard spend stop (W2); and the round-two
   protocol (W3). The AI keeps a rulebook it may rewrite on any turn, using
   only what it has seen up to that bar, and every version is journaled;
   model-written executable code is out of scope. Round one's lesson goes
   into the design: trading costs about 0.30% a round trip, and holding or
   staying out are normal choices.
2. **A window that has not happened yet.** W3 freezes the prompt, initial
   rulebook, cadence, pinned model version, settings and the whole reading
   before the window starts. The window is the 35 days from the first 00:00
   UTC at least one day after W3 merges; prices since 2026-10-11 are already
   known to the people building this, so they cannot be the test.
3. **The bar**, as the
   [forward-test bar](held-out-benchmark-before-reasoning-proposer.md#held-out-protocol-and-predeclared-reading)
   states it: beat cash, hold-the-coin and mean reversion, and baseline when
   baseline survives costs, at the same costs, limits and $1,000 accounts, on
   the 8 round-one coins. Hold-the-coin keeps round one's 20% exposure, so a
   pass says nothing about beating full-size holding. W3 also fixes, before
   the window: pooled versus per-coin success, what makes a rarely trading
   channel's reading conclusive (round one's 30-round-trip minimum does not
   fit it), how failed model calls count, the model's effort level, and that
   each case runs once with no reruns. The bar is judged on trading results
   alone, like the comparators, which make no model calls; model spend is
   reported beside it. A model's cost is per decision, not per dollar
   traded, so on $1,000 test accounts it would swamp any result (about $350
   per cost case at four-hourly decisions against round one's +$213 for
   holding), while at a larger account size it would not; the card says so
   plainly. W3 also freezes the source code that shapes what the model sees
   and does (through the journal's source hash, as round one did) and says
   in advance how a defect found after the window opens is handled.
4. **One card to RJ** once W1–W3 have merged: the API credential, a spending
   cap of at most maximum turns × (estimated input tokens × input price +
   frozen `max_tokens` × output price), shown beside the expected spend and
   enforced by W2's hard spend stop, the run over the frozen window, and the
   optional GPT-6 Astra arm. W3 states in advance how an arm is read if the
   spend stop ends it before the window does. The card also offers pausing
   now instead, which RJ's answer allows, and restates the pause rule below
   for RJ to confirm. It names the three ways round two departs from
   [agentic alpha](adopt-agentic-alpha-direction.md)'s constraints, which RJ
   set, for RJ to confirm: a run on recorded bars from a window that started
   after the freeze counts as forward evidence; results are read by the
   experiment's predeclared reading instead of the old scorecard; and trades
   run in the experiment's simulator rather than the paper lane's approval
   rails, as [Direction](../DIRECTION.md#immediate-product-outcome) already
   allows for local simulations. The
   alternative, a wall-clock run that calls the model every few hours for
   five weeks, needs a scheduled service but also captures live latency and
   outages; it stays available if RJ prefers it.

### Round-two protocol (W3)

Frozen on 2026-10-18 in
[config/experiments/round-two-2026-10-20/PROTOCOL.md](../../config/experiments/round-two-2026-10-20/PROTOCOL.md)
([#1352](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1352)),
which owns the details; the main points:

- **Window** 2026-10-20T00:00Z to 2026-11-24T00:00Z, run afterwards on its
  recorded bars. **Source** frozen at the `main` commit that merges the
  protocol, bound into every run through the journal's source hash.
- **Model and prompt.** Claude Fable 5.1 at effort `high`, `max_tokens`
  16,000, a turn every four hours plus one on each fill, 48 bars of context.
  The prompt now also states the window and how the result is read.
- **The bar.** Pooled over the compared symbols (at least 7 of 8), under
  both cost cases, above cash, hold-the-coin and mean reversion, and
  baseline if baseline survives costs on this window, each by more than
  one round trip's costs per symbol ($4.80 base, $22.40 stress). No trade
  minimum for the manager: in a falling window a few well-chosen trades can
  pass, and round three guards against luck. A symbol with more than 5% of
  its turns lost to failed calls is not scored; refused and malformed
  replies count as the model's own no-trade decisions. Every symbol is
  listed in the reading, as a run or as not run. Each case runs once; a
  resume in the same directory is not a rerun.
- **Spend** is reported beside the bar from the one spend ledger, matched
  call by call to journaled turns. The card's cap is at most about $4,540,
  by item 4's formula over 4,800 turns (including 90 fill turns per
  account); expected spend is about $860 to $1,230 *(derived; real counts
  come only from the approved run)*.
  Accounts the spend stop ends are not scored; the reading is otherwise
  unchanged.
- **A defect found after the window opens** is never patched into the run;
  if it could change a request, a decision's effect or a reported number,
  the attempt is void and re-frozen for a fresh window.
- **GPT-6 Astra cannot join this window.** The frozen commit has no OpenAI
  transport, and adding one would change the source hash. If RJ wants that
  arm, both arms are re-frozen together for a later window. This narrows
  item 4 above, which offered it on the same window.

### Pause rule and the test that restarts the project

Read once, by the protocol W3 commits:

- **Passes the bar under both cost cases:** propose round three, the same
  test on the next unseen window, before any claim of skill. A pass
  authorizes further validation, not a claim that autonomous trading works.
  If RJ adds the GPT-6 Astra arm, a pass by either arm opens round three for
  that model alone, and the report states that two arms were tried; round
  three's fresh window is the guard against a lucky pass.
- **Anything else** (fails, trails hold-the-coin, beats only some
  comparators, inconclusive, or cost-dependent; for both arms if there are
  two): **the project pauses**. RJ set the pause; treating anything short
  of a pass as a pause is the agent's reading of "credibly", which RJ
  confirms on the card. RJ's later, specific answer on #1344 allows a
  pause in place of RJ's
  [improvement-rounds](evidence-first-improvement-rounds.md) condition 3,
  that a weak result opens the next round. Choosing "pause now" on the card
  takes the same pause path. This is an investment decision on
  insufficient evidence, not a finding that today's models cannot trade.
  Pausing records the result here, closes round two's build items, leaves
  one open restart-watch item for the agent, and leaves every gate, record
  and the built channel in place, default-off.
- **Restart test:** the models available at round two's freeze, including
  the GPT-6 Astra arm whether or not RJ adds it, are not eligible, whether
  round two runs or RJ pauses now: RJ's answer waits for a stronger model.
  A model is eligible when it is **released after that freeze** and enters the frontier tier of RJ's workspace model stack
  (re-evaluated at each major release). Each eligible model gets **one**
  attempt, in release order: the same protocol, prompt and starting
  rulebook, changed only where the new model's API requires it (each change
  recorded), re-frozen for that model before a fresh window that starts
  after the freeze, with RJ's one-card go. Every attempt is reported here
  cumulatively, passes and failures alike. The project restarts
  only on a pass, and a pass again means round three's confirmation, not
  proof.

## Consequences

- [#1252](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1252) is
  round two's build umbrella, re-scoped from the quiesced Stage-2 cycle to
  the experiment slice; W1 is its first item. W0 (mean reversion in the old
  cycle) stays deferred; the experiment already runs mean reversion beside
  any channel.
- No fixed-rule redesign (option B) is built; the experiment keeps running
  both fixed rules beside the AI trade manager as comparators.
- No model call, credential or spend happens before RJ's one card.

## Safety boundary

This record authorizes no broker or account call, model call, credential,
spend, service, schedule, order, money movement or autonomy change, and starts
no run. The AI trade manager runs only in the recorded experiment's simulated
accounts. All [Direction](../DIRECTION.md) gates are unchanged.
