# Round-two protocol: the AI trade manager (frozen before its window)

> **In plain language.** This file fixes everything about round two before
> any of its prices exist: the window (2026-10-20 to 2026-11-24), what the AI
> is told, which model runs at what settings, and how the result is read.
> The AI passes only if, under both cost assumptions, its pooled result over
> the 8 coins beats cash, holding the coin and the mean-reversion rule.
> Anything else pauses the project. The run happens after the window
> closes, on its recorded prices, and only after RJ's one card (API key,
> spending cap, run).

This protocol was committed with
[#1352](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1352) (W3)
before its window opened. The authority is the "Round two" section of
[the decision record](../../../docs/decisions/next-round-after-held-out-benchmark.md#round-two)
and the [forward-test bar](../../../docs/decisions/held-out-benchmark-before-reasoning-proposer.md#held-out-protocol-and-predeclared-reading).
This file adds mechanics and settles the open points; it loosens no
threshold. If they differ, the record wins.

Machine-readable parameters are in [protocol.json](protocol.json).
`gpt-trader experiment read` takes them from that file and applies the
reading in [manager_reading.py](../../../src/gpt_trader/features/experiment/manager_reading.py).

## Window

- **Dates.** Scored bars run from 2026-10-20T00:00Z up to the cutoff
  2026-11-24T00:00Z: 35 days, 840 hourly bars. 72 warm-up bars from
  2026-10-17T00:00Z come first, so each input has 912 bars. Warm-up bars are
  history only: no decision is taken on them. They predate the freeze; the
  manager sees at most 48 of them, as context, at its first turns.
- **Why these dates.** The decision fixes the start as the first 00:00 UTC at
  least one day after W3 merges. These dates hold only if this file reaches
  `main` before 2026-10-19T00:00Z. If it merges later, the dates must be
  moved by that rule in this file before merge; a file on `main` with a
  start less than a day after its merge commit is void.
- **When it runs.** After the cutoff, on the window's recorded bars, and
  only after RJ's go on the card. Fetch no earlier than 2026-11-24T02:00Z, so
  the last bar is final. RJ's go may come before or after the cutoff.

## Frozen source and settings

- **Source.** The frozen source is the `main` commit that merges this file.
  Every run and every reading uses a checkout of that commit. The journal
  binds the SHA-256 of `src/gpt_trader/**/*.py` (`implementation_sha256`)
  into each run's identity. A run made or read by any other source is refused
  on inspection ("Experiment metadata binding failed"), so a later change on
  `main` cannot reach this run. The commit and its digest are recorded on the
  card.
- **Prompt.** `INSTRUCTIONS` and the request built by `build_request` in
  [trade_manager.py](../../../src/gpt_trader/features/experiment/trade_manager.py),
  context version `ai-trade-manager-context/2`. W3 added two things to W1's
  prompt: the window's start and end, and how the result is read (net of
  costs at sale value, against cash, holding the coin at the exposure cap
  and the fixed rules). The model is told how it is judged, not anything
  about the window's prices.
- **Starting rulebook.** The `manager.rulebook` text in protocol.json (W1's
  starting rulebook, unchanged). The model may rewrite it on any turn, up to
  4,000 characters; every version is journaled.
- **Cadence.** A turn on every fourth scored bar (every four hours, from the
  close of the first scored bar), plus a turn on any bar with a fill (entry,
  stop, target, expiry or manager exit). Each turn sees the 48 closed bars
  ending at the decision bar.
- **Model.** `claude-fable-5-1`, the strongest model in the frontier tier of
  RJ's workspace model stack on RJ's Claude API credits at the freeze. The
  ID has no snapshot suffix; the transport stops the run if a reply names
  any other model. Effort `high`; `max_tokens` 16,000, thinking included.
  Thinking is always on for this model and is not configurable. No tools, no
  refusal fallback (it would answer with another model), standard capacity,
  global inference, structured output in the reply shape `parse_reply`
  accepts. Price table: $10 input and $50 output per million tokens
  (Anthropic, read 2026-10-17).
  *Why `high`:* Anthropic's guidance treats `high` as the minimum for
  intelligence-sensitive work, and a non-streaming request is kept near
  16,000 output tokens to stay inside the SDK's timeout. *Strongest option
  rejected:* `xhigh`, which might decide better but thinks longer; a turn
  that runs past `max_tokens` is truncated into a no-trade turn, and raising
  the cap safely needs a streaming transport, which W2 does not have.

## Run procedure (only after RJ's go on the card)

1. Check out the frozen commit and run `uv sync --all-extras --dev`.
2. For each of the 8 symbols, into a new empty directory:
   `uv run gpt-trader experiment fetch-input --protocol config/experiments/round-two-2026-10-20/protocol.json --symbol <SYMBOL> --out <evidence>/<SYMBOL>`.
   This writes `capture.json` and six inputs (three channels × two cost
   cases), each manager input carrying the frozen `manager` settings. A gap,
   conflicting duplicate or short window refuses the build and writes
   nothing; that symbol is not scored. Nothing is filled or re-fetched to
   repair it. If the fetch tooling itself is broken, it may be fixed and run
   from a later commit before any input exists, as in round one; the inputs
   must still pass step 5 from the frozen commit.
3. **Rules first.** Run each of the 32 baseline and mean-reversion inputs
   once into its own new directory with `experiment run`. Run one of them
   with `--max-bars` partway, resume it, and confirm it equals an
   uninterrupted run of the same input. These make no model calls.
4. **Manager.** Run the 16 manager inputs once each, in protocol symbol
   order, all base-cost inputs before the stress-cost ones:
   `uv run gpt-trader experiment run --input <input> --root <run> --model-transport anthropic --spend-ledger <LEDGER> --spend-cap-usd <CAP>`.
   - **One ledger.** `<LEDGER>` is the single path named on the card,
     outside every run root, used by all 16 accounts. The cap belongs to the
     ledger file: a different path would start a fresh count, so no other
     path is ever used.
   - **Resume, never rerun.** A run stopped by a crash, an interrupt or a
     halt (credential, model or request error) is resumed in the same
     directory with the same ledger and cap. Resuming is not a rerun: the
     journal replays every committed turn from its record and never asks
     for it again. Starting a new directory for an input that already has
     one is a rerun and is not allowed.
   - **The spend stop is final.** If the spend stop ends a run, that run is
     not resumed under a higher cap or a new ledger. It, and any run not yet
     started, stays incomplete.
5. Rebuild every input, and the input every run is bound to, from its
   symbol's capture with `experiment verify-input`; refuse any difference.
6. Re-derive every balance of all 48 runs independently in Decimal from the
   journal fills, without the engine's reconciliation, as round one did.
7. Write a runs file mapping each channel and cost case to its run
   directories, and run
   `uv run gpt-trader experiment read --protocol config/experiments/round-two-2026-10-20/protocol.json --runs <runs.json> --spend-ledger <LEDGER> --format json`.
   Post every case and every symbol, including no-trade, loss, halt,
   open-at-cutoff, unscored and stopped rows. No case is dropped or rerun.

## The reading

**Per cost case**, base (10 bps fee + 5 bps slippage) and stress (60 + 10),
each symbol its own $1,000 account with 1% stop risk, a 20% exposure cap and
a 10% drawdown halt:

- **Compared symbols.** The symbols scored for the manager and for every rule
  in the bar under that case. A manager symbol is scored when its run is
  complete over all 912 bars, starts scoring at 2026-10-20T00:00Z, ends at
  the cutoff, was decided only by `claude-fable-5-1` under the frozen
  settings, and lost no more than 5% of its turns to failed calls (below).
  A rule symbol is scored as in round one.
- **Pooled, not per coin.** The manager's net P&L at liquidation value,
  summed over the compared symbols, must be above each comparator's sum over
  the same symbols: cash ($0), hold-the-coin (20% of the account bought at
  the first scored open, with costs), mean reversion and, if it joins the
  bar, baseline. Every per-symbol result is reported beside it. *Why pooled:*
  it is round one's reading, and on eight correlated coins over five weeks a
  per-coin count mostly measures noise. *Strongest option rejected:* also
  requiring a majority of coins, which guards against one coin carrying the
  sum; round three's fresh window is the guard instead.
- **Baseline joins the bar** only if it survives costs on this window
  itself: it passes under both cost cases by round one's reading (at least 7
  of 8 symbols scored and 30 pooled round trips, net above zero and above
  hold-the-coin). Both rules' round-one verdicts are reported.
- **Outcome.** *Pass*: at least 7 compared symbols and above every
  comparator. *Trails*: at least 7 compared symbols and at or below any
  comparator; the report names which. *Inconclusive*: fewer than 7.
- **No trade minimum for the manager.** Round one's 30-round-trip floor does
  not apply to it. Holding is the comparator, so a channel that rarely
  trades is still read against what it could have earned: a manager that
  never trades earns $0, which never beats cash, and one that buys and holds
  is judged against holding. Its round trips are reported. Five weeks can
  show that the manager fails to beat these comparators, not prove skill.

**Failed calls.** A failed call (timeout, connection error, rate limit,
overload or server error) makes that turn a permanent no-trade turn: no
entry, an open position is kept, and that bar is never asked again; the next
due turn asks afresh. Such turns count in the result like any other. A
symbol with more than 5% of its turns failed (at four-hourly turns, about
ten) is not scored, because an outage then decided too much of it. Refused
turns and malformed replies, including a reply cut off at `max_tokens`, are
the model's own decisions: they make no trade and count in the result, and
never unscore a symbol.

**Verdict and what follows**, combining the two cases in this order:

1. *Inconclusive* if either case is inconclusive.
2. *Passes* if both cases pass: propose round three, the same test on the
   next unseen window, before any claim of skill.
3. *Cost-dependent* if exactly one passes.
4. *Fails* otherwise.

Anything other than *passes* pauses the project, as the
[pause rule](../../../docs/decisions/next-round-after-held-out-benchmark.md#pause-rule-and-the-test-that-restarts-the-project)
states. The reading is judged on trading results alone. **Model spend** is
reported beside it from the spend ledger, not the journals: a call whose
billing is unknown records $0 in its journal turn but keeps its full
reservation in the ledger, and a call whose bar never committed appears only
there. The read reports the ledger's charge, its calls by status, and ledger
calls minus journaled turns (re-asked uncommitted bars; a negative number
means some turn was charged elsewhere, which is reported as a breach).

## If the spend stop ends the arm early

Accounts the stop ends, and accounts never started, are incomplete and not
scored; the reading above applies unchanged. Because the manager runs in a
fixed order, a stop near the end leaves the earlier accounts complete, and
a case with 7 of 8 compared symbols is still read. Which account is cut is
set by the order, not by any result. A case left with fewer than 7 is
inconclusive, and the arm pauses. The cap is sized so this should not
happen (below).

## A defect found after the window opens

From 2026-10-20T00:00Z the builders can see the window's prices, so the
frozen source is never patched for this run.

- **Defects in the run path** (anything that shapes a request, what a
  decision does, fills, valuation or the reading's inputs): if the defect
  could change a request, a decision's effect or any reported number, the
  attempt is **void**. It is reported, the fix goes through review, and this
  protocol is re-frozen with the fix for a fresh window starting at the first
  00:00 UTC at least one day after the fix merges. A void attempt is not the
  model's one attempt and not a pause. If no model call was made, the card's
  go carries over to the re-frozen window; if calls were made, the rerun's
  spend comes to RJ as a new card. When it is unclear whether the defect
  could have mattered, the attempt is void.
- **Defects that cannot reach this run** (code the run does not execute,
  such as the live runtime): recorded, no action.
- **Fetch tooling:** fixable before any input exists (run procedure,
  step 2).
- **Reading arithmetic:** this file wins over the code. The reading this
  file states is computed from the frozen checkout's `experiment inspect`
  reports and reported beside the code's reading.

## Spend estimate for the card

`experiment estimate-spend` was rerun offline on the frozen settings
(no key, network or call): round one's 8 captures, rebuilt as 16 manager
inputs with this protocol's settings, 2026-10-17. Method and margin are W2's:
a token covers at least one byte, so a request's bytes plus a 2,000-token
allowance bound its input tokens. A real count is likely well below the
bound; it is known only from the first approved call.

| Measure | Frozen rulebook | Rulebook at its 4,000-character limit |
| --- | --- | --- |
| Context per turn, largest | 7,009 bytes | 10,716 bytes |
| Input-token bound per turn, largest | 10,927 | 14,634 |
| Worst case per turn *(derived)* | about $0.94 | about $0.98 |
| Cadence turns, 16 accounts | 3,360 | 3,360 |

The worst case per turn is the input bound at the cache-write rate the spend
stop reserves ($12.50 per million) plus 16,000 output tokens at $50 per
million. **Turns for fills:** each entry or exit fill adds at most one turn.
The cap allows 90 fill turns per account (up to 45 round trips per coin,
more than twice round one's busiest rule, about 19), so at most 300 turns
per account and 4,800 in all. **The cap's ceiling is about $4,720**
*(derived: 4,800 turns × $0.983)*. The card may set a lower cap; a call is
sent only while the ledger's charge plus its own worst case fits, so a lower
cap stops the arm only if real spend runs near it. **Expected spend is far
lower**: at an *assumed* 3,000 output tokens and the input bound, a turn
costs about $0.26, about $860 for 3,360 turns *(derived; real input tokens
are likely well under the byte bound)*. A test account is $1,000, so model
spend would swamp any trading result there; at a larger account size it
would not. The card says so plainly.

## GPT-6 Astra

The frozen commit has no OpenAI transport, and adding one changes the
source hash, so a GPT-6 Astra arm cannot join this window. If RJ wants that
arm, both arms are re-frozen together for a later window, under this
protocol, once its transport is built and reviewed.

## Limits stated in advance

Recorded bars replayed after the window closes rule out training on the
window and tuning on its outcome. They do not reproduce live latency,
outages or slippage. Five weeks on eight correlated coins is one market
regime, so a pass is a first reading, not proof.
