# Held-out benchmark protocol (committed before any data is fetched)

This protocol was committed with #1315 phase A, before any price after
2026-09-05 was downloaded. The authority is the "Held-out protocol and
predeclared reading" section of
[the decision record](../../../docs/decisions/held-out-benchmark-before-reasoning-proposer.md).
This file adds mechanics and settles wording; it loosens no threshold. If the
two ever differ, the record wins.

Machine-readable parameters live in [protocol.json](protocol.json): symbols,
window, settings, cost cases and the coverage floors (7 of 8 symbols scored,
30 pooled round trips). `gpt-trader experiment read` takes its thresholds from
that file and applies the reading in
[reading.py](../../../src/gpt_trader/features/experiment/reading.py).

## Run procedure (phase B, only after RJ's go)

1. For each of the 8 symbols, into a new empty directory:
   `uv run gpt-trader experiment fetch-input --protocol config/experiments/held-out-2026-09-06/protocol.json --symbol <SYMBOL> --out <evidence>/<SYMBOL>`.
   This is the only network step: public Coinbase hourly candles, no
   credentials. It writes `capture.json` (every request and raw response, with
   the fetch time) and one input per rule and cost case. Each input's source
   label carries the capture's SHA-256 and fetch time, so they are part of the
   run identity. A gap, conflicting duplicate or short window refuses the build
   and writes nothing; the symbol is then reported as not scored. Nothing is
   filled or re-fetched to repair it.
2. Run each of the 32 inputs once into its own new directory:
   `uv run gpt-trader experiment run --input <input> --root <run>`.
   Before reading, rebuild each symbol's inputs and the inputs its runs are
   bound to from that symbol's capture, and refuse any difference:
   `uv run gpt-trader experiment verify-input --capture <evidence>/<SYMBOL>/capture.json --input <input> ... --root <run> ...`.
   A refused input or run is reported, not repaired or re-fetched.
3. Pause/resume check: run one case with `--max-bars` partway, resume it, and
   confirm its report equals the uninterrupted run of the same input.
4. Re-derive every balance independently in Decimal from the journal fills,
   without the engine's reconciliation.
5. Write a runs file mapping each rule and cost case to its run directories
   and run `uv run gpt-trader experiment read --protocol <protocol.json> --runs <runs.json> --format json`.
   Post every case, including no-trade, loss, halt and open-at-cutoff rows.
   No case is dropped or re-run.

## Mechanics this file adds

- **Rule channels.** `baseline` is the moving-average crossover. `mean-reversion`
  is the existing `MeanReversionStrategy` (default `MeanReversionConfig`)
  through the snapshot-strategy wrapper. Neither is tuned. Each decision sees
  the 53 bars ending at the decision bar. Exits follow each rule's structured
  plan (stop, target, expiry). Mean reversion's live z-score exit is not part
  of the snapshot contract, so it does not apply here.
- **Price precision.** Both rules quantize plan levels to the proposers'
  default $0.01 precision, a modelling assumption. A signal whose levels
  collapse at that precision is not traded. Baseline drops it; mean reversion
  records it as "signal refused" in the journal. Either way the run continues.
- **Scored window.** Inputs start at 2026-09-03T00:00Z with
  `scored_from` 2026-09-06T00:00Z. Before that bar, every decision is
  "warm-up bar: indicator history only". The first decision is taken at the
  close of the first scored bar.
- **End valuation.** At the last scored close, anything held is valued at
  `quantity × close × (1 − slippage) × (1 − fee)`. Open positions are still
  reported as open.
- **Hold-the-coin.** The account buys at the first scored bar's open plus
  slippage. The quantity is the smaller of cash ÷ (price × (1 + fee)) and
  cash × 20% ÷ price, rounded down to the quantity increment. It pays the entry
  fee and uses the same end valuation. The full-size price move is reported as
  context only.
- **Round trips.** A round trip is an exit fill (stop, target or expiry) before
  the cutoff. A position still open at the cutoff is not a round trip.
- **Costs as shares.** Fees and slippage include the liquidation exit of an open
  position. They are shown as shares of the gain before costs only when that
  gain is positive.
- **Scored symbol.** A symbol is scored when its run is complete over all 912
  bars, its `scored_from` matches, and its last bar ends at the cutoff.

## Settled wording (from the PR #1316 review)

1. **Survives at RJ's fee counts as survives.** For action A and for the
   forward-test bar, a rule that passes the separately approved exact-fee run
   counts as surviving (`survives-at-rj-fee` in `reading.py`).
2. **Inconclusive is "was not shown to survive".** If action D is reached
   because a rule stayed inconclusive or cost-dependent after its follow-up,
   the report says that rule "was not shown to survive", with its coverage
   limits. It does not say "does not survive" (`not-shown-to-survive` in
   `reading.py`).
3. **A declined follow-up is the follow-up.** If RJ declines the follow-up run
   offered for a rule, that counts as the rule's one follow-up. Its verdict is
   then resolved as in point 2.
