---
status: accepted
date: 2026-09-04
deciders: RJ modernization authorization; implementation fleet
---

# Transactional trade state

The modernization review reproduced two independent public approvals exceeding a
one-ticket cap and a failed proposal batch deleting a different writer's audit
history. Per-log locks did not protect the policy read/check/commit operation.

The trade-idea service now owns a single SQLite transaction over record versions,
current records, workflow audit, budgets, autonomy and closeout attribution.
`BEGIN IMMEDIATE` precedes mutable admission inputs. Kernel checks are previews;
recording approval revalidates the current idea and policy in the committing
transaction. Mandatory audited autonomy down-ratchets survive a policy denial.
Other failures roll back the complete operation. SQLite replaces the writable
JSON projection; there is no dual write and no compensating whole-log rewrite.

Record and event payloads retain their JSON contracts. Versions and events reject
update/delete; current record references remain mutable. Indexed per-decision
reads avoid decoding the entire audit twice for every listed idea. The existing
pure eligibility, budget, autonomy, exposure and accounting policies remain the
authority. This change does not grant execution, change limits, or add a model
provider. It makes the existing service suitable for capable proposers without
requiring them to manage synchronization.

## Adoption and rollback

New empty roots initialize SQLite on their first mutation. Reading an empty root
creates nothing. Existing JSON stores remain readable; the upgraded service
refuses writes until explicitly migrated. Storage adapters and the runtime budget
seed discover the database at the same root. Preflight reports legacy migration
as incomplete, and verifies SQLite state using a consistent read snapshot.

Before operating the upgraded service against existing state, the deployment
owner must quiesce **every writer**, including hourly jobs, CLI sessions and the
engine. A generation hash comparison detects changes during import; it cannot
prove an old binary will never resume. Do not run old and new writers together.

From the repository environment, with new destination paths:

```bash
uv run python -m gpt_trader.features.trade_ideas.migration validate OLD_ROOT
uv run python -m gpt_trader.features.trade_ideas.migration import OLD_ROOT NEW_ROOT
uv run python -m gpt_trader.features.trade_ideas.migration validate NEW_ROOT
```

The importer validates audit sequencing, referenced hashes, latest-record
bindings, control versions, closeout attribution, database integrity, and exact
source-generation stability. It retains the original managed file bytes inside
the database and never edits OLD_ROOT. Select NEW_ROOT through the existing
`GPT_TRADER_IDEAS_ROOT` configuration only in the separately authorized deployment.
Normal broker uncertainty/reconciliation and kill-switch checks still apply;
a local state commit cannot make a broker call atomic.

After any new writes, rollback must preserve the **current** state:

```bash
uv run python -m gpt_trader.features.trade_ideas.migration export NEW_ROOT EXPORT_ROOT
uv run python -m gpt_trader.features.trade_ideas.migration validate EXPORT_ROOT
```

The exporter takes one database snapshot and includes every current version,
event, budget, autonomy grant and closeout, including post-import writes. Exact
legacy bytes are reused only when their parsed content is unchanged. It validates
the exported tree before publishing it. A rollback to an older binary requires
writers to be quiesced and the verified current export selected; restoring the
original import baseline after new writes would lose history. No runtime
migration, job change, broker call or execution enablement accompanies source
integration.
