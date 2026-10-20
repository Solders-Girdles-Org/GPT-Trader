# Prove the AI trade manager's pieces cheaply before any paid test

---
status: accepted
date: 2026-10-18
deciders: rj (declined the paid test); project agent (next-round plan, delegated by rj)
supersedes:
superseded-by:
---

> **In plain language.** RJ said no to the round-two test that could have
> cost up to $4,540 in model fees. GPT-Trader is too unproven to spend that
> much, and RJ's Max plan doesn't include that much API credit. This is "not
> yet", not "stop". Next, the AI writes its trading rules once a week, and
> the simulator follows those rules hour by hour without calling the model.
> That cuts model calls from about 4,800 to about a dozen. Each piece gets
> proven before anything is spent: first the rules engine, at no cost, then
> a test call of a few dollars to show the real model works, then a forward
> test capped at about $20. The two paid steps need RJ's go, on one card.

## Context

The round-two card
([#1354](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1354))
asked RJ to approve one simulated test of the
[AI trade manager](next-round-after-held-out-benchmark.md#round-two):
Claude Fable 5.1 deciding every four hours for each of 16 accounts, with a
$4,540 hard cap and $860–$1,230 expected spend. RJ answered on 2026-10-18 (the
`rj-answer` comment on #1354):

> "We are not ready for that. GPT-Trader needs much more work, before I'm
> willing to fund it with real money. We also don't get allocations that large
> included in my Claude Max plan. That test would be too costly for us
> especially considering how unproven every part of GPT-Trader still is."

RJ's conditions, as recorded there: model spend is real money; plan rounds so
a spend-gated test comes only after the pieces it depends on have been proven
cheaply; any paid model use still needs RJ's go. RJ said GPT-Trader needs
more work, not that it should stop. So this is "not yet", not the protocol's
"pause now" path of waiting for a stronger model. The next work is the project
agent's call, and RJ sees only choices that are theirs.

What the declined design spends on is the number of model calls. Cost is per
call, not per dollar traded
([round two, item 3](next-round-after-held-out-benchmark.md#round-two)). The
frozen protocol needed about 4,800 calls: 16 accounts, a turn every four hours
over 35 days, plus a turn on each fill
([protocol](../../config/experiments/round-two-2026-10-20/PROTOCOL.md)).
What is proven and what is not, as of this record:

- **Proven at no spend:** the simulator, costs, comparators, independent
  accounting and resume (round one's 32 cases), and the AI channel with an
  injected fake transport (W1 tests).
- **Never exercised:** the real Anthropic transport (W2 made no call), real
  token counts, real reply quality and parse rates, and whether any AI design
  can beat holding after costs.

## Options

- **Option A — AI rulebook author (chosen).** The AI writes a structured
  rulebook from a fixed set of parameterized rules: trend and
  mean-reversion entries, a trend filter, exposure, stop, target and expiry,
  and "hold at the cap" or "stay in cash". The engine runs that rulebook on
  every hourly bar with no model call. The AI rewrites the rulebook at a low
  cadence (weekly as the starting assumption) in one portfolio-level call for
  all eight coins per cost case. That is about a dozen calls per 35-day
  window instead of about 4,800, with a hard cap of roughly $20
  *(estimate, assuming the author sees 30 daily bars per coin rather than
  hourly bars: about 40,000 bytes of context bounds its input at about 42,000 tokens by round
  two's per-byte method; at the cache-write rate, plus 16,000 output tokens at
  Fable 5.1's recorded price, that is about $1.30 a call, times 12 calls;
  step 2 below replaces this with an offline measurement)*. It
  meets RJ's condition that the AI may "choose, write or change its own
  rules". The AI does not just suggest trades: its rules run without a
  person. Model spend no longer swamps the result. A cheap design can be read
  on several fresh windows, which guards against a lucky single reading
  better than one expensive run. The cost is a few agent-days of building.
  The AI also gives up bar-by-bar judgment, which round one's costs argue
  against anyway. *Strongest objection:* RJ's #1344 condition to pause
  "rather than build something less". A weekly author choosing among rule
  templates is a narrower design than round two's trade manager. The agent's
  answer: the AI still decides alone what to trade, when and how much, and
  RJ said "the rules can be anything we want". Because the design is
  narrower, step 3's card says so plainly, and RJ can choose Option B there
  instead.
- **Option B — The built channel at weekly cadence (strongest alternative).**
  `cadence_bars` already accepts 168, so the reviewed W1–W3 code could run
  per coin once a week, plus a turn on every fill (each of which may enter
  or exit), with no new build. If fills are rare (about 1–10 per account),
  that is roughly 100–240 calls and about $20–60 expected. A cap sized like
  the protocol's, with 90 fill turns per account, would be about $1,500
  *(scaled from the protocol's per-turn figures; fill turns are the
  uncertain part)*. Rejected because the cap stays in the range RJ calls real
  money, and cost still grows with coins and fills. It becomes the better
  choice if RJ prefers the soonest reading with no new code and accepts a cap
  in the hundreds with a tighter fill-turn allowance.
- **Option C — The four-hourly design on a cheaper model.** Same call count,
  a lower price. Rejected: it still spends hundreds over 4,800 calls. A weaker
  model also cannot answer RJ's question about what today's models can do.
- **Option D — Fixed-rule round only, no AI.** No model spend
  ([round two, option B](next-round-after-held-out-benchmark.md#options)).
  Not chosen as a round of its own, because it does not test the AI. Option A
  covers it: the rulebook engine with an agent-written starting rulebook is a
  fixed-rule channel at no spend.

## Decision

**Option A, proven in this order, each step gated on the one before:**

1. **Rulebook engine (agent, no spend).** A structured rulebook format and a
   deterministic `ai-rulebook` channel that runs it in the experiment
   engine with the existing rails, plus an agent-written starting rulebook.
   It must be able to express "stay in cash", close equivalents of both
   fixed rules, and a hold rule that renews at expiry. No rail is loosened,
   so the hold rule cannot equal hold-the-coin. Every entry risks at most 1%
   of equity at its stop ([engine](../../src/gpt_trader/features/experiment/engine.py)
   sizing), so a hold at the 20% cap needs a stop within about 5% of price,
   and a wider stop means a smaller position. Positions also expire within
   720 hours. Step 1 measures both variants (at the cap with a tight stop,
   and smaller with a wide stop) against hold-the-coin on round one's data.
   Step 3's card reports the gap, and the protocol may propose a rail
   change for the hold rule, recorded before its window. Proven by unit
   tests and by replaying round one's recorded inputs as test data, not
   evidence.
2. **Rulebook author turn (agent, no spend).** A portfolio-level request
   that sends recent closed bars for all coins, the accounts and the current
   rulebook, and reads back a validated rulebook. It uses an injected
   transport, journals every version and is default-off. It also needs an
   offline `estimate-spend` for this design and a plumbing-smoke mode that
   runs a few turns on known bars, into a scratch directory marked as not
   evidence.
3. **Protocol and one card (agent, then RJ).** Freeze the prompt, starting
   rulebook, cadence, model, settings, window and reading before a window
   that starts after the freeze, under the
   [forward-test bar](held-out-benchmark-before-reasoning-proposer.md#held-out-protocol-and-predeclared-reading).
   One card then asks RJ for the existing Anthropic credential and a
   dollar-sized total cap covering two steps in order. First, the plumbing
   smoke, which proves the real transport, reply parsing, the spend ledger
   and real token counts. Second, only if the smoke passes, the frozen
   forward run. The card shows the measured estimate and offers "not yet".
   It also proposes, with a recommendation, how a weak result is read under
   RJ's pause condition below, for RJ to confirm. It states plainly that this
   design is narrower than round two's trade manager, and offers Option B
   instead.

The bar and the void-and-refreeze rule follow round two's protocol unless
step 3 records a change before its window. Its pause line ("anything other
than passes pauses") does not carry over. **RJ's pause
condition stands:** if an AI that manages the trades "can't be built
credibly with today's models", pause until a stronger model is available
"rather than build something less"
([#1344](next-round-after-held-out-benchmark.md#decision)). RJ declining the
card did not trigger it. Round two's reading of that condition (anything
short of a pass pauses) was the agent's proposal awaiting RJ's confirmation
on the card, and it lapsed with the decline. Step 3's card proposes the
reading for this design.

**Reconsider** if step 1 cannot express the fixed rules closely, if the
offline estimate in step 2 exceeds about $50 for one window, or if the smoke
shows the model cannot produce valid rulebooks reliably.

## Consequences

- The frozen round-two window (2026-10-20 to 2026-11-24) lapses unrun. No
  credential is configured, no spend ledger is created and nothing is
  fetched for it. Its protocol stays as the record of what was frozen. The
  `ai-trade-manager` channel and Anthropic transport stay in source,
  default-off. The rulebook author reuses the transport's spend ceiling.
- #1354 closes with RJ's answer. Steps 1 and 2 are agent-owned issues
  ([#1356](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1356),
  [#1357](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1357)).
  Step 3 is filed when they merge.
- Step 3 ([#1363](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1363)) first
  needed a full-window run path, which step 2 did not include
  ([#1364](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1364)). Its frozen
  [protocol](../../config/experiments/rulebook-author-2026-10-22/PROTOCOL.md) and
  [card draft](../../config/experiments/rulebook-author-2026-10-22/CARD.md) followed.
- Any paid model use, including the smoke, needs RJ's go on the card.
- RJ answered the card on 2026-10-19 with a go
  ([#1367](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1367)): one $25 cap split
  $6.50 and $18.50, funded only by the API credits included with RJ's Max plan, and checked in
  the Console before any call. RJ confirmed the three departures. RJ also confirmed the proposed
  reading of a weak result: a pass leads to the same design on a fresh window, and a fail allows
  one more window. Each new window needs a new card. If that second window also fails, this line
  pauses.

## Safety boundary

This record authorizes no broker or account call, model call, credential,
spend, service, schedule, order, money movement or autonomy change, and starts
no run. The AI trade manager and rulebook author run only in the recorded
experiment's simulated accounts. All [Direction](../DIRECTION.md) gates are
unchanged.
