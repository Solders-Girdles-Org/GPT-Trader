# Rulebook-author protocol: the weekly AI rulebook author (frozen before its window)

> **In plain language.** This file fixes everything about the cheap AI test
> before any of its prices exist. Once a week the AI writes the trading rules
> for all eight coins, and the simulator follows those rules hour by hour
> without calling the model. The scored window runs from 2026-10-22 to
> 2026-11-26. The file also fixes what the AI is told, which model runs at
> what settings, and how the result is read. The AI passes only if, under
> both cost assumptions, its pooled result over the 8 coins beats cash,
> holding the coin and the mean-reversion rule. Two paid steps need RJ's go
> on one card: first a test of about three calls on old prices, then the
> run, after the window closes. The worst case for both is about $20.

This protocol is step 3 of the accepted
[cheap-proofs decision](../../../docs/decisions/cheap-proofs-before-paid-ai-test.md)
([#1363](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1363)),
committed before its window opens. Its bar is the
[forward-test bar](../../../docs/decisions/held-out-benchmark-before-reasoning-proposer.md#held-out-protocol-and-predeclared-reading).
Its mechanics follow the lapsed
[round-two protocol](../round-two-2026-10-20/PROTOCOL.md) except where this
file says otherwise. That includes the void-and-refreeze rule for defects. This
file adds mechanics and loosens no threshold. If it differs from the decision,
the decision wins.

The design is **Option A, the rulebook author**. It is narrower than round
two's trade manager. The AI still decides alone what to trade, when and how
much, but only once a week, by choosing among fixed rule templates. It does
not judge every bar. The card says so and offers
[Option B](../../../docs/decisions/cheap-proofs-before-paid-ai-test.md#options),
the built trade manager at weekly cadence, instead.

Machine-readable parameters are in [protocol.json](protocol.json).
`gpt-trader experiment read` takes them from that file and applies the
reading in [author_reading.py](../../../src/gpt_trader/features/experiment/author_reading.py).
The card RJ answers is drafted in [CARD.md](CARD.md).

## Window

- **Dates.** Scored bars run from 2026-10-22T00:00Z up to the cutoff
  2026-11-26T00:00Z: 35 days, 840 hourly bars. 720 warm-up bars (30 days)
  from 2026-09-22T00:00Z come first, so each input has 1,560 bars.
- **Why 30 warm-up days.** The author sees the last 30 complete daily bars
  per coin, and its rules may use up to 500 hourly bars of history. 720
  warm-up bars give every turn, including the first, the same context, and
  let every rule the author may choose act from the first scored bar.
  Warm-up bars are history only: no decision is taken on them. Unlike round
  two's, most of them close before the freeze. That cannot tune anything to
  the scored window, which starts after the freeze. They are what any trader
  sees as recent history, and the starting rulebook is not fitted to them.
- **Why these dates.** The start is the first 00:00 UTC at least one day
  after this file merges, with room for review. These dates hold only if
  this file reaches `main` before 2026-10-21T00:00Z. If it merges later, the
  dates must be moved by that rule in this file before merge. A file on
  `main` with a start less than a day after its merge commit is void.
- **When it runs.** The plumbing smoke may run any time after RJ's go, on
  old prices. The forward run comes after the cutoff, on the window's
  recorded bars, and only if the smoke passes. `fetch-input` refuses to run
  before 2026-11-26T02:00Z, two hours after the cutoff, so the last bar is
  final.

## Frozen source and settings

- **Source.** The frozen source is the `main` commit that merges this file.
  Every run and every reading uses a checkout of that commit. Each author
  journal binds every input payload and the SHA-256 of `src/gpt_trader/**/*.py`
  (`implementation_sha256`). `author-inspect` refuses a journal under any
  other source ("Experiment metadata binding failed"), and the rule runs bind
  it as in round two. The commit and its digest are recorded on the card.
- **Prompt.** `INSTRUCTIONS` and the request built by `build_author_request`
  in [rulebook_author.py](../../../src/gpt_trader/features/experiment/rulebook_author.py),
  context schema `rulebook-author/2`. Step 3 added two things to step 2's
  prompt: the window's start and end, and how the result is read (pooled net
  P&L at sale value, against cash, holding at the exposure cap and the fixed
  rules, by the margin below). The model is told how it is judged, not
  anything about the window's prices.
- **Starting rulebook.** `author.starting_rulebook` in protocol.json:
  step 1's agent-written schema defaults with `entry: "cash"`. The first
  author turn falls at the first scored bar's close and replaces it for every
  coin, so it acts only if that turn fails or is rejected. *Why cash:* a
  failed first turn should leave the accounts out of the market rather than
  trading the agent's untested defaults, and it anchors the author on no
  strategy. *Strongest option rejected:* step 1's trend defaults, which would
  trade unmeasured rules on a failed turn and suggest trend to the author.
- **Cadence.** One portfolio turn per cost case on every 168th scored bar,
  from the close of the first scored bar: five turns per case, ten in all.
  There are no turns on fills. Each turn sees the last 30 complete UTC daily
  bars of every coin, the accounts, the costs, the limits and the current
  rules. Accepted rules act from that bar's close; fills are at the next
  open, and an open position keeps its stop, target and expiry.
- **Model.** `claude-fable-5-1`, effort `high`, `max_tokens` 16,000 (thinking
  included; thinking is always on for this model), no tools, no refusal
  fallback (it would answer with another model), structured output in the
  reply shape `parse_author_reply` accepts. Price: $10 input and $50 output
  per million tokens (Anthropic, checked 2026-10-19). A reply naming any
  other model stops the run. *Why this model:* RJ's question is what
  today's strongest models can do, and at about ten calls the frontier price
  is affordable. *Why `high`:* as in round two; a non-streaming request is
  kept near 16,000 output tokens, and a cut-off reply only keeps the previous
  rules. *Strongest option rejected:* `xhigh`, for the same reason as round
  two.
- **Rails, unchanged.** 1% stop risk per entry, 20% exposure cap, 10%
  drawdown halt, expiry at most 720 hours. No rail change is proposed for
  the hold rule (below).

## Run procedure (only after RJ's go on the card)

1. Check out the frozen commit into a clean directory and run
   `uv sync --all-extras --dev`. Before any model call, confirm that
   `git status` shows no change or untracked file and that the checkout's
   source digest (`implementation_digest()` in `inputs.py`) equals the one on
   the card.
2. **Two ledgers, one total.** The card's $25 is split in advance:
   `<SMOKE_LEDGER>` is `runtime_data/experiments/rulebook-author-2026-10-22/smoke-spend-ledger.sqlite`
   with a $6.50 cap, and `<LEDGER>` is
   `runtime_data/experiments/rulebook-author-2026-10-22/spend-ledger.sqlite`
   with a $18.50 cap, both in the project home. The smoke uses only the first;
   both author runs use only the second, and no other path. *Why two:* the
   smoke's base-cost portfolio has the same ledger account as the forward
   base run, so a shared ledger would count the smoke's calls as the forward
   run's, and the reading would refuse them as a second run.
3. **Plumbing smoke, on old prices.** Build eight smoke inputs in a new
   scratch directory under `work/`: round one's retained `baseline-base.json`
   inputs (`runtime_data/experiments/held-out-2026-09-06/inputs/<SYMBOL>/`),
   each with `benchmark` set to `ai-rulebook`, `scored_from` set to
   `2026-10-03T00:00:00+00:00` (bar 720, so every turn sees 30 daily bars as
   in the forward run) and `rulebooks` set to the protocol's starting
   rulebook from that time. Then run
   `uv run gpt-trader experiment author-smoke --input <each of 8> --root <scratch>/smoke --model-transport anthropic --spend-ledger <SMOKE_LEDGER> --spend-cap-usd 6.50 --max-turns 3 --author-cadence-bars 24`
   with the frozen model, effort and `max_tokens` (the defaults). Its output
   says `NOT-EVIDENCE` and is never a reading.
   - **The smoke passes** when all three turns are answered by the pinned
     model, at least two are accepted (a valid rulebook for every coin),
     every call's real input tokens are within its reserved byte bound, and
     the ledger's charges equal the journal's costs.
   - **If it fails** because the model's rulebooks are invalid, refused or
     cut off on two or more turns, the decision's reconsider line is met: no
     forward run, and the result goes to RJ. A failure outside the code
     (credential, credits, an account limit) is fixed there and the smoke
     is resumed with the same ledger and cap. A failure inside the code is a
     run-path defect (below).
4. After 2026-11-26T02:00Z, for each of the 8 symbols, into a new empty
   directory:
   `uv run gpt-trader experiment fetch-input --protocol config/experiments/rulebook-author-2026-10-22/protocol.json --symbol <SYMBOL> --out runtime_data/experiments/rulebook-author-2026-10-22/inputs/<SYMBOL>`.
   This writes `capture.json` and six inputs (three channels × two cost
   cases). Each `ai-rulebook` input carries the frozen starting rulebook. A
   refused symbol is not scored; nothing is filled or re-fetched to repair
   it. Fetch tooling may be fixed before any input exists, as in round two.
5. **Rules first.** Run each of the 32 baseline and mean-reversion inputs
   once into its own new directory with `experiment run`. Run one of them
   with `--max-bars` partway, resume it, and confirm it equals an
   uninterrupted run. These make no model calls. Each 1,560-bar run takes
   about 90 seconds on RJ's Mac *(scaled from a measured 30 seconds at 912
   bars)*.
6. **Author.** Base costs first, then stress:
   `uv run gpt-trader experiment author-run --protocol config/experiments/rulebook-author-2026-10-22/protocol.json --input <each fetched ai-rulebook-<case>.json> --root runtime_data/experiments/rulebook-author-2026-10-22/runs/ai-rulebook-<case> --model-transport anthropic --spend-ledger <LEDGER> --spend-cap-usd 18.50`.
   Round two's rules apply. A stopped run is resumed in the same directory
   with the same ledger and cap; committed turns replay and are never asked
   again. A new directory for a case that already has one is a rerun and is
   not allowed. A halt that recurs on its first resume with the same cause is
   a run-path defect. The spend stop is final.
7. Rebuild every input, and the input every rule run is bound to, from its
   capture with `experiment verify-input`; refuse any difference.
   `author-inspect` rebuilds each author journal's inputs from the journal.
   Compare them with the fetched files.
8. Re-derive every balance independently in Decimal from the journals'
   fills, without the engine's reconciliation, as round one did (adapted to
   the author journal's rows). This checks results; it cannot change one.
9. Write a runs file: `ai-rulebook` maps each cost case to its author run
   directory, or `{"not_run": "<why>"}` if the spend stop never started it.
   Each rule maps each cost case to a list naming every protocol symbol once,
   as in round two. Then run
   `uv run gpt-trader experiment read --protocol config/experiments/rulebook-author-2026-10-22/protocol.json --runs <runs.json> --source-sha256 <DIGEST> --spend-ledger <LEDGER> --format json`.
   Post every case and every symbol, including no-trade, loss, halt,
   open-at-cutoff and unscored rows, with the smoke's result and the model
   spend beside them. No case is dropped or rerun.

## The reading

**Per cost case**, base (10 bps fee + 5 bps slippage) and stress (60 + 10),
each coin its own $1,000 account with 1% stop risk, a 20% exposure cap and a
10% drawdown halt. Unless this section says otherwise it is round two's
reading, with the author in the manager's place.

- **A case counts** when its author run is complete over all 1,560 bars,
  ran under the frozen author settings and starting rulebook, was answered
  only by `claude-fable-5-1`, and lost at most one of its five turns to
  failed calls. A coin is then scored as in round two (full length, scored
  from 2026-10-22T00:00Z, ending at the cutoff). A case that does not count
  scores no coin.
- **Pooled, not per coin**, over the coins scored for the author and for
  every rule in the bar: the author's net P&L at liquidation value must be
  above each comparator's sum over the same coins, by the margin. The
  comparators are cash ($0), hold-the-coin (20% of the account bought at the
  first scored open, with costs), mean reversion and, if it survives costs
  on this window by round one's reading, baseline.
- **Margin.** One round trip's costs at the exposure cap per compared coin:
  $4.80 at base and $22.40 at stress over 8 coins.
- **Outcome.** *Pass*: at least 7 compared coins and above every comparator
  by the margin. *Trails*: at least 7 and not above some comparator; the
  report names which. *Inconclusive*: fewer than 7.
- **Verdict.** *Passes*: passes under both costs. *Cost-dependent*: passes
  under one. *Fails*: conclusive and passes under neither. *Inconclusive*:
  inconclusive under either.
- **No round-trip minimum** for the author, as in round two: a well-placed
  stay in cash can pass in a falling window, and an author that never
  trades cannot pass.

**Failed and rejected turns.** A failed call (timeout, connection error,
rate limit, overload or server error) keeps every coin's previous rules for
that week. The turn is journaled and never asked again once its bar commits.
With five turns a case, one failed turn is allowed. *Why one, not round
two's 5%:* 5% of five turns is zero, and a single outage would then void a
case whose other four weeks were the model's. Two failed turns hand 40% of
the window to the outage. *Strongest option rejected:* zero, which turns one
transient error into an inconclusive case after the spend. A refused,
truncated, malformed or out-of-range reply is the model's own result: the
previous rules stay, and the turn counts like any other.

**A breached spend record** (a call charged to an account or run directory
the runs file does not list, fewer calls than turns, a rewound decision
time, or an author run bound to another ledger) means the runs may not be
the one run per case allowed. No verdict stands on it. It is reported, and
says nothing about the model.

### Proposed reading of a weak result (for RJ to confirm on the card)

RJ's condition stands. If an AI that manages the trades "can't be built
credibly with today's models", pause until a stronger model is available
"rather than build something less". The decision leaves this design's
reading of that condition to this card. The agent proposes:

- **Passes:** a first reading, not proof. The next step is the same frozen
  design on a fresh window, on a new card.
- **Fails or cost-dependent:** one more fresh window under the same design,
  on a new card. If that window also does not pass, the condition is read
  as met for this line. The rulebook author pauses until a stronger model is
  released, and the project builds nothing narrower in its place. Option B
  stays RJ's to choose.
- **Inconclusive, breached, void, or the smoke fails:** no reading. Each is
  reported with its cause and needs RJ's next decision.

*Why a second window before pausing:* one five-week window on eight
correlated coins is mostly one market regime, and at about $15 a window a
second reading costs far less than a wrong pause. The decision chose this
design partly so it could be read on several fresh windows. *Strongest option
rejected:* round two's reading, under which anything short of a pass pauses at
once. It is simpler, and it would also be the right reading if RJ wants no
further spend on this line after one try. The card offers it.

## Hold rule and rails

Step 1 measured the renewing hold rule against hold-the-coin on a rising
144-bar stretch of round one's BTC bars, as test data
([test](../../../tests/unit/gpt_trader/features/experiment/test_rulebook_replay.py)).
It trailed by $2.21 at the cap with a 4% stop and by $18.07 with a 15% stop
at base costs. At stress costs (3% stop) the figures were $6.69 and $18.27.
These are mechanics measurements, not performance. Holding at the cap needs a
stop within about 5% of price (about 3.7% at stress), positions expire
within 720 hours, and the comparator has neither limit. So in a rising window
matching holding is structurally hard, as it was in round two. **No rail
change is proposed.** Loosening a risk rail is outside this step, and
the at-cap gap is small next to the margin. The handicap is disclosed here
and on the card.

## A defect found after the window opens

Round two's rule applies unchanged. From 2026-10-22T00:00Z the frozen source
is never patched for this run. A defect that could change a request, a
rule's effect, fills, valuation or the reading's inputs voids the attempt.
It is reported, fixed through review, and this protocol is re-frozen for a
fresh window starting at the first 00:00 UTC at least one day after the fix
merges. A void attempt is not the model's one attempt and not a pause. If no
model call was made in the forward run, the card's go carries over to the
re-frozen window; if calls were made, the rerun's spend comes to RJ as a new
card. The smoke runs on old prices and a code fix before the forward run
re-freezes only if the fix changes the run path. Reading arithmetic: this
file wins over the code.

### Re-freeze after the smoke found a run-path defect (2026-10-20)

The first freeze was `e4556632` (digest `d600789f…ef767`). On 2026-10-20 the
plumbing smoke's first call was rejected before any output, with HTTP 400:
"The compiled grammar is too large". The reply schema wrote the 13-field rule
out once for each of the 8 coins. The API turns a structured-output schema
into a grammar, and that grammar passed its size limit. The rejection was
unbilled ($0 in the smoke ledger), and no turn was journaled.
*Fix:* `reply_schema` defines the rule once under `$defs` and each coin
refers to it. The reply's shape and `parse_author_reply` are unchanged. Two
`max_tokens`-1 probes on the smoke ledger confirmed that the shared form
compiles ($0.0104 and $0.0106; nothing else was asked).

The fix changes the request, so this file is re-frozen by the commit that
merges the fix (rule above). It merges before 2026-10-21T00:00Z, so the first
00:00 UTC at least one day later is still 2026-10-22T00:00Z, and **the window,
settings, reading and caps are unchanged**. The new commit and digest go on
the card. No forward-run call has been made, so RJ's go carries over. The
smoke resumes in a new scratch root under the new source, with the same
ledger and $6.50 cap. The shorter schema lowers each turn's input bound from
56,348 to 51,686 tokens (worst case $1.446 a turn, re-measured offline); the
2026-10-19 figures below are kept as the card's estimate and stay an upper bound.

## Spend estimate for the card

`experiment estimate-spend --author` was run offline on the frozen settings
and prompt on 2026-10-19 (no key, network or call). It used round one's 8
retained baseline inputs per cost case, rebuilt in memory as `ai-rulebook`
inputs scored from bar 720, so each turn carries 30 daily bars per coin. The
method is step 2's. A request's bytes plus a 2,000-token allowance bound
its input tokens. Accounts and rulebooks are reserved at their enforced
16,000-byte cap, and each turn is charged at the cache-write rate ($12.50
per million) plus 16,000 output tokens at $50 per million.

| Measure | Base | Stress |
| --- | ---: | ---: |
| Largest context per turn | 46,934 bytes | 46,936 bytes |
| Input-token bound per turn | 56,348 | 56,350 |
| Worst case per turn | $1.504 | $1.504 |

- **Forward run:** 10 turns, at most **$15.04** *(derived)*.
- **Smoke:** 3 turns at the same context size, at most **$4.51** *(derived)*.
- **Both: at most $19.55**, below the decision's $50 reconsider line.
- **Cap proposed: $25 in all**, fixed before any call as $6.50 for the smoke's
  ledger and $18.50 for the forward run's. Each leaves room for at least one
  re-asked call after a crash before commit (the forward run's for two).
  Each spend stop holds at its cap either way.
- **Expected spend is lower:** about $0.71 a turn at an *assumed* 3,000
  output tokens and the full input bound, about **$9** for all 13 turns
  *(derived)*. Real input tokens are likely well under the byte bound, and
  the smoke measures them.

Model spend (about $15 over 16 simulated $1,000 accounts) is reported beside
the trading result, not inside it.

## Limits stated in advance

Recorded bars replayed after the window closes rule out training on the
window and tuning on its outcome. They do not reproduce live latency,
outages or slippage. Five weeks on eight correlated coins is one market
regime, so a pass is a first reading, not proof. Ten weekly calls test the
author's choices, not bar-by-bar judgment. The rails above handicap holding
in a rising window, and the comparator rules share them.
