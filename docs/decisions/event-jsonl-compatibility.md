# Event JSONL: accepted fallback or import-only historical data

---
status: accepted
date: 2026-07-02
deciders: rj
supersedes:
superseded-by:
---

## Context

The event store historically accepted a JSONL fallback shape. As compatibility
shims are collapsed (see `docs/DEPRECATIONS.md`), the project needs to decide
whether JSONL remains a supported runtime fallback or becomes import-only
historical data.

## Options

- **A — Keep JSONL as an accepted fallback** for runtime event storage.
- **B — Make JSONL import-only historical data** — readable for backfill/analysis
  but no longer a supported write path, removing the compatibility surface.

## Decision

**Option B — JSONL is import-only historical data.** Accepted 2026-07-02 by rj.

The precondition (no live path depends on JSONL writes) was verified at
acceptance: `EventStore` (`src/gpt_trader/persistence/event_store.py`) persists
exclusively via SQLite (`events.db`) with an in-memory cache — no JSONL write
path exists. The only remaining JSONL surface on the event side is a read-only
fallback in `src/gpt_trader/monitoring/daily_report/loaders.py`, used solely
when a storage directory predates `events.db` — which is exactly the
import-only-historical shape this option prescribes.

## Consequences

No code change required; this ratifies the current state and closes the open
question in the compatibility inventory (`docs/DEPRECATIONS.md`, which records
JSONL as import-only). Any future JSONL write path would need a new decision.

## Safety boundary

No execution or account impact; storage-format decision only.
