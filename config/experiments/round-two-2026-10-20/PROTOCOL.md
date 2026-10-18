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
  history only: no decision is taken on them. They close before the window
  opens, and most close after the freeze, so nothing can be tuned to them;
  the manager sees up to 48 of them, as context, at its first turns.
- **Why these dates.** The decision fixes the start as the first 00:00 UTC at
  least one day after W3 merges. These dates hold only if this file reaches
  `main` before 2026-10-19T00:00Z. If it merges later, the dates must be
  moved by that rule in this file before merge; a file on `main` with a
  start less than a day after its merge commit is void.
- **When it runs.** After the cutoff, on the window's recorded bars, and
  only after RJ's go on the card. `fetch-input` refuses to run before
  2026-11-24T02:00Z, two hours after the cutoff, so the last bar is final.
  RJ's go may come before or after the cutoff.

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
  ID has no snapshot suffix (Anthropic's model table lists it as complete);
  a reply naming it or a dated snapshot of it (`claude-fable-5-1-YYYYMMDD`)
  counts as the pinned model, and the transport stops the run if a reply
  names any other model. Effort `high`; `max_tokens` 16,000, thinking included.
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

1. Check out the frozen commit into a clean directory and run
   `uv sync --all-extras --dev`. Before any model call, confirm that
   `git status` shows no change or untracked file and that the checkout's
   source digest (`implementation_digest()` in `inputs.py`) equals the one on
   the card; a stray `.py` file under `src/gpt_trader/` would change it.
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
     for it again, and only the uncommitted bar is asked again. Starting a
     new directory for an input that already has one is a rerun and is not
     allowed.
   - **A halt that recurs is not resumed again.** A halt whose cause is
     outside the code (a revoked key, exhausted credits, an account limit)
     is fixed there and resumed. A halt that recurs on its first resume
     with the same cause, and any halt for a cost above its reservation, is
     a run-path defect: that account is not resumed, and the defect rules
     below apply.
   - **The spend stop is final.** If the spend stop ends a run, that run is
     not resumed under a higher cap or a new ledger. It, and any run not yet
     started, stays incomplete.
5. Rebuild every input, and the input every run is bound to, from its
   symbol's capture with `experiment verify-input`; refuse any difference.
6. Re-derive every balance of all 48 runs independently in Decimal from the
   journal fills, without the engine's reconciliation, as round one did.
7. Write a runs file mapping each channel and cost case to a list naming
   **every** protocol symbol once: its run directory, or
   `{"symbol": "<SYMBOL>", "not_run": "<why>"}` for a refused fetch or an
   account the spend stop never started. The read refuses a list that
   leaves a symbol out, and refuses a rule marked not run while its symbol
   has a run anywhere else (only a refused fetch leaves a rule without a
   run). Then run
   `uv run gpt-trader experiment read --protocol config/experiments/round-two-2026-10-20/protocol.json --runs <runs.json> --source-sha256 <DIGEST> --spend-ledger <LEDGER> --format json`.
   It refuses any run bound to another source digest. Post every case and
   every symbol, including no-trade, loss, halt, open-at-cutoff, unscored,
   stopped and not-run rows. No case is dropped or rerun.

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
- **Margin.** "Above" means above by more than one round trip's costs at
  the exposure cap, summed over the compared symbols: $200 × 2 × (fee +
  slippage) per symbol, so $4.80 at base costs and $22.40 at stress over 8
  symbols. A
  smaller lead is less than what one trade per coin costs, so it is read as
  not beating that comparator.
- **Outcome.** *Pass*: at least 7 compared symbols and above every
  comparator by the margin. *Trails*: at least 7 compared symbols and not
  above some comparator by the margin; the report names which.
  *Inconclusive*: fewer than 7.
- **When a rarely trading manager's reading counts.** Always, given the
  coverage above: round one's 30-round-trip floor does not apply to the
  manager, and its round trips are reported. In a rising window a manager
  that rarely trades is judged against holding, which it must beat. In a
  falling window, holding and the rules lose, so the bar becomes a profit
  above the margin: a manager that stays out and makes a few well-chosen
  trades can pass. Staying out of a falling market is part of managing
  trades, and round three's fresh window is the guard against a lucky
  pass. A manager that never trades, or trades for a few cents, cannot
  pass. *Strongest option rejected:* a minimum number of round trips. It
  would block a pass earned by correctly staying out, and any count on five
  weeks is arbitrary. Five weeks can show that the manager fails to beat
  these comparators, not prove skill.

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
there. Each ledger call records its account (symbol and cost case) and
decision time. For each listed account, the read reports ledger calls
beside journaled turns. A surplus is re-asked uncommitted bars, each of
which the report explains by its crash or halt. Ledger calls for an account
outside the runs file, fewer calls than turns (a turn charged to another
ledger), and an account whose decision times go back to an earlier bar (a
second run, since a resume only asks the same bar again or a later one) are
listed as breaches. The read also lists every model string that answered;
more than one (two dated snapshots, say) is reported beside the bar.

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
- **A recurring halt** (run procedure, step 4) is a run-path defect.
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
million. The rulebook column assumes plain text; a rulebook full of
characters that JSON escapes (quotes, line breaks, non-ASCII) could reach
about 48,000 bytes and about $1.53 a turn. Each call is still reserved from
its own bytes, so the spend stop holds either way.

**Turns for fills:** each bar with a fill adds at most one turn. The
estimate allows 90 fill turns per account (up to 45 round trips per coin,
more than twice round one's busiest rule, about 19), so at most 300 turns
per account and 4,800 in all.

**The cap's ceiling is about $4,540**, by the decision record's formula,
maximum turns × (input bound × input price + `max_tokens` × output price):
4,800 × (14,634 × $10 + 16,000 × $50) per million *(derived)*. The card's
cap is at most that. The stop reserves each call at the cache-write rate,
about $0.98, and replaces the reservation with the measured cost once the
call replies, so a cap stops the arm only when real spend nears it.

**Expected spend is far lower:** at an *assumed* 3,000 output tokens and the
mean input bound, a turn costs about $0.26: about $860 for the 3,360 cadence
turns, and about $1,230 if every allowed fill turn is used *(derived; real
input tokens are likely well under the byte bound)*. A test account is
$1,000, so model spend would swamp any trading result there; at a larger
account size it would not. The card says so plainly.

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

The bar is easier in a falling window than in a rising one, and these
handicaps are inherited from round one's rails:
- Every manager entry carries a stop. At 1% stop risk, the full 20%
  exposure needs a stop within about 5% of the price at base costs, or about
  3.7% at stress costs; a wider stop means a smaller position.
- A position expires after at most 720 hours, against an 840-hour window.
- Hold-the-coin has neither limit.

So in a rising window, matching holding is structurally hard. The
comparator rules share the same rails.
