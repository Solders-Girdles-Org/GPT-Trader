# Card draft: the cheap AI test (not yet posted)

This is the body of RJ's one card for step 3 of the
[cheap-proofs decision](../../../docs/decisions/cheap-proofs-before-paid-ai-test.md).
It is drafted with the [protocol](PROTOCOL.md) and is **not posted, and no
approval is inferred, by this file**. Lead or the board posts it after this
protocol merges, filling in the frozen commit, its source digest and its
merge time. Nothing here authorizes a call, credential, ledger, fetch or run.

---

**Cheap AI test: about $20, in two steps.** Approve your existing Claude API
credential and a **$25 total cap** for two steps, in order. First, a
**plumbing test** of three calls on old prices, any time. Second, **only if
that passes**, the **frozen forward test** after November 26, 02:00 UTC. Or
answer **not yet**.

**Why you:** only you can approve model spend, credential use and research
evidence.

**Recommendation:** approve. The worst case for both steps is **$19.55**,
measured offline (no call made); expected spend is about **$9**. That is
under 1% of the declined round-two cap ($4,540). The plumbing test proves
the real model connection, reply checking, the spend ledger and real token
counts before the forward test spends anything more. If the model can't
write valid rules reliably, the forward test doesn't run.

**What the AI does:** once a week it writes the trading rules for all eight
coins. The simulator follows those rules hour by hour with no model call:
ten calls in all, against about 4,800 in round two. This is **narrower than
round two's trade manager.** The AI still decides alone what to trade, when
and how much, but weekly, from fixed rule templates, not bar by bar.
**Option B instead:** the already-built trade manager at weekly cadence, no
new code, about $20–60 expected but a cap in the hundreds.

**Please confirm how a weak result is read** (your pause condition):

- **Pass:** a first reading only; the same design on a fresh window next,
  on a new card.
- **Fail:** one more fresh window (about $15, new card). If that also fails,
  this line pauses until a stronger model exists, and nothing narrower is
  built in its place.
- **Or the stricter reading:** any non-pass pauses at once.

[Approval details](#approval-details). Agent side after your go: run the
plumbing test, then wait for the window, run once, reconcile and report. No
live trades.

---

## Approval details

**Recommended answer:** approve one Claude-only simulated test in two steps,
existing API credits only, **one $25 total cap**, split before any call as
$6.50 for the smoke and $18.50 for the forward run:

1. **Plumbing smoke,** any time after the go. It makes three author calls
   on round one's old prices, into scratch marked not evidence. It passes
   when all three calls answer from the pinned model, at least two return
   valid rulebooks for every coin, real token counts sit within their
   reserved bounds, and the ledger matches the journal. If two or more
   rulebooks are invalid, refused or cut off, the forward run does not
   happen and the result comes back to you.
2. **Frozen forward run,** only if the smoke passes. It runs after
   **2026-11-26T02:00Z** (November 25, 7 p.m. Phoenix) on the window's
   recorded prices: 10 author calls, 2 cost cases, 8 coins.

Do not buy credits or enable automatic top-ups under this approval;
insufficient credit is an external halt under the protocol.

**Measured estimate (offline, no call):** at most $1.504 a call at the
frozen settings with 30 days of context; forward at most $15.04, smoke at
most $4.51, together **$19.55**. Each part of the cap leaves room for at
least one re-asked call after a crash. Expected about $9 at an assumed 3,000 output
tokens. Real token counts are measured first by the smoke. Model spend is
reported beside the trading result: about $15 over 16 simulated $1,000
accounts.

**Frozen authority:** [PROTOCOL.md](PROTOCOL.md) at the frozen commit and
the [accepted decision](../../../docs/decisions/cheap-proofs-before-paid-ai-test.md).

- Source commit: `<merge commit of the protocol PR>`; merged `<time>`,
  before the 2026-10-21T00:00Z deadline.
- Source digest: `<implementation_digest() at that commit>`.
- Scored window: 2026-10-22 00:00 UTC to 2026-11-26 00:00 UTC, after 30
  warm-up days; 1,560 hourly bars per coin. One portfolio run per cost case
  and 32 rule accounts.
- Frozen model and settings: `claude-fable-5-1`, effort `high`,
  `max_tokens` 16,000, one portfolio call a week per cost case, 30 daily
  bars of context, no tools, no model substitution. The starting rulebook is
  "stay in cash" until the first call.
- One evidence home: `runtime_data/experiments/rulebook-author-2026-10-22/`
  in the project. Two spend ledgers there, one per step:
  `smoke-spend-ledger.sqlite` ($6.50) and `spend-ledger.sqlite` ($18.50, both
  forward runs). None is created before approval. Every resume uses its
  step's ledger and cap, a cap is never raised, and no new ledger is
  substituted.

**Credential:** approve secure use of your existing Anthropic API
credential via `ANTHROPIC_API_KEY`, configured in the local secure
environment, never in GitHub, chat, evidence or logs. This card neither
inspects nor provisions it. No OpenAI credential or spend is included.

**Confirm together** (as on the round-two card, which you declined): (1)
replay of a window starting after the freeze counts as forward evidence; (2)
the experiment reading replaces the old scorecard; (3) the simulator
replaces paper-lane approval rails. These are departures from the
agentic-alpha constraints, not live-execution permission.

**Reading:** pooled trading P&L must beat cash, holding at 20% exposure,
mean reversion, and baseline if it qualifies, under both cost cases, with at
least seven compared coins and the protocol's cost margin. A case with more
than one of its five calls failed is inconclusive. Holding is structurally
hard to match in a rising window under the unchanged rails (step 1 measured
a $2.21–$18.27 gap on a rising BTC stretch); no rail is loosened.

**Weak result:** the proposed reading above. A pass leads to a fresh-window
confirmation, not live trading or proof of skill. Run-path defects follow
the protocol's void-and-refreeze rule; nothing is silently patched or rerun.

**Not yet** keeps everything default-off, makes no call, and leaves the
frozen window to lapse unrun. **Option B** means re-freezing for a later
window with the built trade manager and a new card.

**Agent continuation after your answer:** record it; prepare a clean frozen
checkout and confirm the digest; run the smoke and report it; after the
fetch time fetch once, run rules first with the resume check, run each
author case once, verify inputs and Decimal accounting, read with the one
ledger, and publish every outcome. Never start the legacy runtime or a
service. Credential and cap approval alone is not permission if you exclude
the forward run.
