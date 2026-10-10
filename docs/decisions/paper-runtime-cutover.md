# Paper runtime cutover

---
status: accepted
date: 2026-09-05
deciders: RJ
---

## Observed trigger

At 20:56 UTC on September 5, the loaded macOS job
`com.gpt-trader.stage1-cycle` invoked `scripts/ops/stage2_cycle_turn.sh` hourly
at :05 from the mutable canonical checkout. It targeted the legacy store at
`var/data/trade_ideas`, with both paper auto gates enabled. Its last observed
successful manifest row completed at 04:16 UTC; twelve later rows failed with
`StateMigrationRequired`. The checkout was `404aef1e`, not the newer remote
source. This is a dated read-only observation, not continuing monitoring.

Its July 4 autonomy grant was paper-only bounded autonomy. There were seven
open filled ideas in the last successful report. A scalar historical simulated
P&L total is not a reconciled opening cash/position baseline for another system.
Original audit/control records and every available/unknown outcome must survive.

## Options and recommendation

**Recommend quiescing the failed hourly job and retaining its evidence while RJ
uses the local experiment product.** That ends repeated failing work and avoids
spending more operator attention on the old workflow before proving the new
product useful. It does not cancel external orders, close real positions, delete
history or claim the seven simulated positions are settled.

If continued forward paper collection is valuable, the alternative is a pinned
compatibility deployment: migrate a copy under the accepted storage contract,
validate it and deliberately resume the old paper job from an immutable source
revision. It remains explicitly compatibility/evidence collection, not the
architecture for new product work.

## Reviewable execution sequence after RJ chooses

1. Reinspect job/process state and current manifest; identify every writer and
   exact source revision. Unload the hourly job only after authorization and
   record its previous launchd definition/loaded state for rollback.
2. Preserve the original store, manifests, snapshots, control grants and logs.
   Hash/copy a quiescent snapshot to a separate dated evidence directory; leave
   original files and source history intact. Reconcile seven observed open
   positions and every uncertain outcome from current evidence; never assume
   this dated count is still current or invent missing marks/receipts.
3. If choosing retirement, leave the job unloaded, record the retained evidence
   location, and remove its default onboarding/operating role. Source deletion
   is a later reviewed change after confirming unique semantics are preserved.
4. If choosing compatibility operation, pin a reviewed commit outside the
   writable development checkout. Follow
   [transactional migration](transactional-trade-state.md#adoption-and-rollback)
   to a new store; validate all lineage, receipts, reductions and unknowns.
   Point the job at that explicit revision/root only after verification, then
   perform one authorized paper turn and compare expected reconciliation.
5. Resume a schedule only after that turn is accepted. Rollback means stop all
   writers, export and validate the *current* state, then select the previously
   verified deployment; never restore an old baseline over new writes.

## Boundary

The redesign goal forbids changing live services/schedules or migrating active
runtime. This proposal has not been executed. No new grant, broker call, order,
credential use or schedule change accompanies source integration. A Git merge
must not be followed by an automatic pull into the canonical scheduled checkout.

The original choice was between **quiesce and retain** (recommended) and
**restore pinned compatibility paper operation**.

## Decision

RJ accepted **quiesce and retain** (recorded on 2026-10-03 in the workspace
ledger entry `gpt-trader-cleanup`, now
[Work board item #28](https://github.com/Solders-Girdles/workspace-operations/issues/28)).
The hourly job stays unloaded, its evidence is retained, and it has no
onboarding or operating role. Restoring forward paper collection is a new
decision that follows step 4 above; it is not a rollback of this one.

## Execution record (2026-10-09)

Steps 1–3 were executed read-only against the store; no file under
`var/data/trade_ideas` was written, and no broker, account or credential was used.

- **Job state.** `launchctl print-disabled` reports
  `"com.gpt-trader.stage1-cycle" => disabled`; `launchctl list` has no
  GPT-Trader entry; no cycle process is running. The plist remains at
  `~/Library/LaunchAgents/com.gpt-trader.stage1-cycle.plist` (it invoked
  `scripts/ops/stage2_cycle_turn.sh` from the canonical checkout with both
  Stage-2 gates on), and a copy is in the snapshot for rollback reference.
- **Quiescence.** `cycle/manifest.jsonl` was last written 2026-09-05 22:05
  local; its newest row is a `StateMigrationRequired` failure at
  2026-09-06 05:05 UTC. No store file is newer than the manifest. 42 of 1,457
  manifest rows failed; the last completed turn is
  `cycle-20260905T040522Z-69d986` (finished 04:16 UTC on September 5).
- **Retained location.** A dated snapshot of `var/data/trade_ideas` and
  `var/logs` lives outside the repository on RJ's Mac at
  `~/Archives/GPT-Trader/stage1-cycle-paper-evidence-2026-10-09/`:
  `trade_ideas-and-logs.tar.gz` (SHA-256 `589fadab…bf587a`), per-file
  `SOURCE-SHA256SUMS.txt` (3,373 files, SHA-256 `7201f392…636d`), the plist,
  the `launchctl` outputs, and `ARCHIVE-SHA256SUMS.txt`. The extracted
  archive matched every source hash, and the originals were re-hashed
  unchanged afterwards. The original store stays in place.
- **Reconciliation of the seven open ideas.** The audit log holds 242 proposed
  ideas, 221 filled, 21 expired before fill and 3 auto-approval skips; 235
  have closeout attributions (101 invalidation, 78 expiry, 56 thesis target).
  The seven filled ideas without a closeout are exactly the
  `open_filled_decision_ids` of the last completed report. Every one is a
  simulated `paper` venue fill against a `MOCK_*` order id, so there is no
  external order or position to cancel or close. All seven passed their
  `expires_at` after the job stopped evaluating exits, so each stays
  **open in evidence with outcome unknown**: no exit mark, closeout or P&L
  is recorded or invented here.

| Decision id | Instrument | Fill (price × qty) | Notional | Expired at (UTC) |
| --- | --- | --- | --- | --- |
| `trade-20260903-ethusd-0125f496` | ETH-USD long | 2494.13 × 0.00196692 | 4.91 | 2026-09-05 16:05 |
| `trade-20260904-mean-reversion-avax-usd-417d75f9` | AVAX-USD long | 7.327 × 0.67204301 | 4.92 | 2026-09-06 13:05 |
| `trade-20260904-mean-reversion-btc-usd-384eb289` | BTC-USD long | 79362.21 × 0.00006231 | 4.95 | 2026-09-06 13:05 |
| `trade-20260904-mean-reversion-link-usd-7ab024dd` | LINK-USD long | 11.64 × 0.42480884 | 4.94 | 2026-09-06 13:05 |
| `trade-20260904-mean-reversion-sol-usd-c8284a4a` | SOL-USD long | 101.66 × 0.04872819 | 4.95 | 2026-09-06 13:05 |
| `trade-20260904-mean-reversion-xrp-usd-3529f8a2` | XRP-USD long | 1.4136 × 3.52112676 | 4.98 | 2026-09-06 13:05 |
| `trade-20260905-dotusd-a1aef407` | DOT-USD long | 0.8928 × 5.49450549 | 4.91 | 2026-09-07 03:05 |

Simulated open notional totals 34.56 against the $1,000 attested paper equity.
Any later attempt to resolve these ideas must use recorded post-fill market
data under the compatibility path in step 4, never the archive alone.

Remaining, deliberately not done here: deleting the plist, the Stage-1/2
wrapper scripts or the store. Source deletion stays a later reviewed change.
