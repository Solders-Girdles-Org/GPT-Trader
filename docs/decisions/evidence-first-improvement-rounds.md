# Evidence first, in improvement rounds, toward a real-money autonomous bot

---
status: accepted
date: 2026-10-17
deciders: rj
supersedes:
superseded-by:
---

> **In plain language.** RJ still wants a fully autonomous bot trading real
> money. The way there is a series of experiments: test, read the result by
> rules written down beforehand, fix or improve, and test again on prices the
> change has never seen. A weak result starts the next round; it does not end
> the project. Every run that writes evidence, every model spend, every account
> access and every real-money step still needs its own go from RJ.

## Context

[#1340](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1340) asked RJ
to choose GPT-Trader's long-term destination from three options, after the
[held-out benchmark decision](held-out-benchmark-before-reasoning-proposer.md)
sequenced one bounded test before any reasoning proposer. RJ answered on
2026-10-17 (the `rj-answer` comment on #1340), and in the same session gave the
go for the held-out run on
[#1315](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1315). RJ's
words: "I still want to have a fully autonomous bot trading real money. That is
still the simplified end goal." and "We can try it as an experiment, but likely
with the expectation that we will need to go through several rounds of changes,
fixes, upgrades, and improvements until we get it right."

## Options

- **1 — Inspectable trading laboratory, then earned autonomy.** Establish
  credible results, test AI decisions forward, then consider bounded real-money
  operation. Each investment buys useful learning.
- **2 — Forward AI paper trader first.** Answers sooner whether reasoning adds
  value, but commits engineering and model spend before the benchmark exists.
  The strongest alternative; rejected because it spends before learning the bar.
- **3 — Recorded-data laboratory as the final product.** Lowest ongoing burden,
  but gives up the autonomous destination RJ wants.

## Decision

**Option 1, run as improvement rounds.** The
[Direction](../DIRECTION.md#destination) destination is unchanged and
reaffirmed: a bot that trades real money autonomously inside machine-enforced
limits. The path is a series of rounds, each with these properties:

1. **Predeclared reading.** A round's test, data window and reading rules are
   committed before its data is fetched, and its result is read by those
   rules only.
2. **Unseen data.** A round tests on market data the change was not built or
   tuned on. Once a window has been read, it is in-sample for every change
   designed after that reading; the next round's test uses a later window.
3. **A weak result opens the next round.** The agent reports what the result
   shows and proposes the next round: what to change and how to test it
   fairly. Stopping alpha investment remains a choice RJ can make, but it is
   not the default recommendation after a weak result.
4. **Separate gates per round.** Each evidence-writing run, model spend,
   account access and real-money step needs its own go from RJ. This decision
   grants only #1315's run, which RJ approved separately.
5. **Plain-language cards.** Because RJ has been away from the project, every
   card asking RJ for a go explains the current state from scratch.

The stages, in order:

| Stage | Done when |
| --- | --- |
| 1 — Record the approved path | This record, [Direction](../DIRECTION.md) and [STATUS](../STATUS.md) are merged, #1252 is reconciled with #1315, and the Phase B procedure is checked against the merged code without fetching data (#1340) |
| 2 — Held-out benchmark | #1315's run follows the committed [protocol](../../config/experiments/held-out-2026-09-06/PROTOCOL.md) unchanged: capture hashes verified, independent Decimal balances and pause/resume agree, all 32 cases accounted for, and `experiment read` yields the predeclared A–D action |
| 3 — Next round | Proposed from stage 2's reading under the rules above; if it is a forward reasoning test, it starts default-off with full model, input and output provenance, bounded spend and a predeclared window read against the [forward-test bar](held-out-benchmark-before-reasoning-proposer.md#held-out-protocol-and-predeclared-reading) |
| 4 — Earned operational autonomy | Independent review of the [measured-outcome gates](adopt-measured-outcome-rubric.md), account and execution capability, loss breakers, kill switch and rollback, then RJ's explicit grants; software completion alone earns no promotion |

## Consequences

- The held-out decision's predeclared reading and actions A–D are unchanged.
  What follows action D changes: the agent brings RJ a proposed next round
  rather than a fund-or-stop recommendation.
- The reasoning proposer
  ([#1252](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1252))
  stays sequenced after #1315's result; it is one candidate for stage 3, not a
  commitment.
- Stage 3 and later work is filed as issues when stage 2's reading names it.

## Safety boundary

This record authorizes no broker or account call, model call, credential,
spend, service, schedule, order, money movement or autonomy change, and starts
no run. All [Direction](../DIRECTION.md) gates are unchanged.
