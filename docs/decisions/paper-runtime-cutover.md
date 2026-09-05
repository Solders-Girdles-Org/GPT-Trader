# Paper runtime cutover

---
status: proposed
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

RJ's remaining decision is whether to **quiesce and retain** (recommended) or
**restore pinned compatibility paper operation**. Either choice requires this
fresh, explicit operational execution step; the local experiment works without it.
