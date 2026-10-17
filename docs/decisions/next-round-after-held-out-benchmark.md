# Next improvement round after the held-out benchmark

---
status: proposed
date: 2026-10-17
deciders: rj
supersedes:
superseded-by:
---

> **In plain language.** Round one is done. Both of GPT-Trader's existing
> fixed trading rules lost money after fees on five weeks of prices they had
> never seen, and simply buying and holding the coins did better. This is the
> first round of an improvement series, not the end. The proposal for round
> two is to build the AI trade-proposer the project has been heading toward
> and test it forward against the same bar. Building it costs nothing beyond
> agent time; before it makes a single paid model call, RJ gets a separate ask
> for the API key and a spending cap.

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

Pending RJ on [#1344](https://github.com/Solders-Girdles-Org/GPT-Trader/issues/1344). The agent recommends Option A.

## Consequences

If RJ chooses A: #1252 becomes the round-two build, starting with W1 (a
default-off skeleton with recorded evidence and no model dependency), and its
forward window and reading are committed before any model call. Separate RJ
cards follow for the credential, the spending cap and the forward run. If RJ
chooses B, the agent commits a new protocol for the 2026-10-11 to 2026-11-15
window before fetching it, and asks for that run's go. Whichever option is
chosen, the next round is filed as issues.

## Safety boundary

This record authorizes no broker or account call, model call, credential,
spend, service, schedule, order, money movement or autonomy change, and starts
no run. All [Direction](../DIRECTION.md) gates are unchanged.
