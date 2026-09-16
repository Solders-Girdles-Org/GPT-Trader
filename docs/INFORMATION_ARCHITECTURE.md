# Information Architecture

---
status: current
---

State each fact once; everywhere else links to it. Prefer derived truth (code,
tests, GitHub) over authored prose, and author prose only for what cannot be
derived: decisions and direction. A restated fact is a second copy that
drifts.

## Where facts live

| Fact | Home | Not in |
| --- | --- | --- |
| Destination, autonomy ladder, execution gates | `docs/DIRECTION.md` | STATUS or README prose |
| A decision, made or open | `docs/decisions/<slug>.md`; `status` carries the lifecycle; the index is generated | Open-question tables, an issue body as the only packet |
| Shipped state | Code and tests, with pointers in `docs/STATUS.md` | ARCHITECTURE prose, README status lists |
| Work to do | GitHub issues (labels only for exceptions) | Roadmap queues, STATUS "next" lists |
| How the code is structured | `docs/ARCHITECTURE.md` plus the code | Hand-maintained module inventories |
| Env vars, metrics, events, CLI flags, schemas | The code (`rg -n` under `src/gpt_trader/`) and `config/environments/.env.template` | Reference tables that must be kept in sync |
| Agent rules and gates | `AGENTS.md` (`CLAUDE.md` links to it) | A second copy under `docs/` |
| Deprecations and compatibility commitments | `docs/DEPRECATIONS.md` | Prose elsewhere |
| Config profiles | `config/profiles/*.yaml` and the typed config in `src/gpt_trader/app/config/` | Docs tables as canonical config, untracked `.env` |
| Runtime state and operational evidence | `runtime_data/<profile>/`, `runtime_data/experiments/<name>/`, `var/`; all ignored | Committed docs, issue bodies |
| Review CSV/XLSX handoffs | `review_artifacts/*.csv` or `*.xlsx`, curated | `review_artifacts/tmp/`, `data/` |
| Per-task plans, audits, scratch | `work/` (ignored); promote only the issue or decision it produces | Anywhere under `docs/` |

Formats follow the fact: Markdown for prose and decision records, YAML for
tracked config and decision frontmatter, JSON for machine contracts, SQLite for
runtime stores (JSONL only as an append-only log or import fallback).

## Rules

1. Restating another doc's fact is a bug; replace it with a link.
2. Ephemeral work never lands in `docs/`.
3. Retire by deleting. Git history is the archive; `docs/archive/` and
   version-suffixed docs are banned. Fix inbound links after a deletion.
4. A pending decision is a `status: proposed` decision file, not a list to
   prune.
5. Every doc under `docs/` carries a `status` frontmatter field and is
   reachable from `docs/README.md`. A new doc needs a row above; if none fits,
   it is probably an issue or a decision.

## Enforcement

| Check | Guards |
| --- | --- |
| `scripts/maintenance/docs_reachability_check.py` | Reachability from `docs/README.md`, a valid `status`, no `docs/archive/` |
| `scripts/maintenance/docs_link_audit.py` | No dangling links or repo paths |
| `scripts/maintenance/docs_currency_scan.py --fail-on missing,stale` | Commands, paths, env vars and modules named in docs still exist; genuine false positives go in `config/agents/docs_currency_suppressions.yaml`, which fails when an entry stops matching |
| `scripts/maintenance/generate_decision_index.py --check` | The decisions index matches the files |

The first three run in `uv run local-ci` and the `Docs Link Audit` CI job.
