# Information Architecture — Where Every Fact Lives

---
status: current
---

This is the map of **where each kind of project knowledge lives, and the one
rule that keeps it from sprawling**. If you are an agent or a human deciding
where to write something down, this doc is the authority. When in doubt, follow
the routing table below rather than creating a new doc.

## The one rule

> **State each fact exactly once. Everywhere else links to it.**
> Prefer **derived truth** (code, tests, generated inventories, GitHub issues)
> over **authored prose**. Author prose only for what cannot be derived:
> **decisions** and **direction**.

A doc that restates a fact owned by another source is not "extra documentation"
— it is a second copy that will drift, and drift is the thing that forces
expensive "where are we actually?" audits. If you catch yourself summarizing
the current state, the backlog, or a decision that already has a home, **link
instead of copy**.

## Source classes

| Class | What it holds | Home | Kept honest by |
|------|---------------|------|----------------|
| **Durable** | Things that change rarely and must be authored: direction, decisions, architecture invariants, naming/policy, standards | `docs/` (and root `AGENTS.md`/`CLAUDE.md`) | Human review + the doc scanners |
| **Living** | Things that track reality and change often: current state pointers and the work queue | `docs/STATUS.md` (pointers only) + **GitHub Issues** | The scanners + live issue/PR state |
| **Generated** | Inventories, schemas, maps, and catalogs derived from source/config | `var/agents/**` | `uv run agent-regenerate --verify` |
| **Review deliverable** | Human-reviewable CSV/XLSX exports intentionally kept as handoff artifacts | root-level `review_artifacts/*.csv` and `review_artifacts/*.xlsx` | `.gitignore` exceptions + `git status` scope review |
| **Runtime** | Per-run local state, readiness inputs, metrics, reports, SQLite stores, JSONL fallbacks | `runtime_data/` and ignored runtime paths | Runtime/readiness commands; not committed |
| **Ephemeral** | Per-task plans, regrounding audits, scratch packets, temporary exports | `work/` and `review_artifacts/tmp/` | Discarded or promoted only as a decision, issue, or review deliverable |

The failure mode this repo kept hitting: ephemeral planning artifacts and
living-state summaries leaked into `docs/`, where they became durable-looking
but went stale. Ephemeral work goes in `work/` or `review_artifacts/tmp/`.
Living state stays small and points at the source.

## Canonical Source Matrix

Use this table before creating a file, converting a file to another format, or
copying a fact into a second home.

| Artifact family | Canonical source | Allowed derived/display formats | Validation command | Update trigger | Forbidden alternate homes |
|-----------------|------------------|---------------------------------|--------------------|----------------|---------------------------|
| Project direction, autonomy boundary, execution gates | `docs/DIRECTION.md` plus accepted/proposed records in `docs/decisions/` | Markdown links from README, status, readiness, and operations docs | `uv run python scripts/maintenance/docs_link_audit.py`; `uv run python scripts/maintenance/generate_decision_index.py --check` | Destination, gate, or execution-boundary policy changes | STATUS prose, README summaries, readiness docs as policy owners, GitHub-only decisions |
| Current shipped state | Code/tests/generated inventories, with a small pointer snapshot in `docs/STATUS.md` | Markdown pointers only; issue and PR links for volatile details | `uv run python scripts/maintenance/docs_currency_scan.py`; `uv run python scripts/maintenance/docs_link_audit.py`; `uv run python scripts/maintenance/docs_reachability_check.py` | A capability moves between missing, partial, and done | Architecture prose, roadmap tables, README status lists, durable scratch logs |
| Backlog and implementation queue | GitHub issues and labels | PR links, issue comments, and labels; docs link to the tracker instead of copying it | `gh issue list --state open --limit 50`; promoter dry-run for agent findings | A bounded build/fix/review item is accepted for work | Markdown roadmap queues, STATUS "next work" lists, review spreadsheets as planning truth |
| Decisions and open questions | One Markdown ADR per file in `docs/decisions/*.md`, with lifecycle in frontmatter | Generated decision index in `docs/decisions/README.md` | `uv run python scripts/maintenance/generate_decision_index.py --check`; docs link/reachability checks | A durable choice is opened, accepted, rejected, superseded, or deprecated | Separate open-question tables, GitHub issue body as the only durable packet, duplicated "accepted direction" sections |
| Architecture and standards | Authored docs for invariants (`docs/ARCHITECTURE.md`, `docs/DI_POLICY.md`, `docs/naming.md`, `docs/testing.md`) plus source code | Markdown prose for invariants; generated maps under `var/agents/**` for inventories | Docs scanners; `uv run agent-regenerate --verify` when generated maps are involved | A durable invariant or standard changes | Hand-maintained module inventories, generated maps copied into prose |
| Agent scout finding before promotion | A JSON object with `schema_version: "gpt-trader.agent-finding.v1"` | The promoter's rendered dry-run body for review | `uv run python scripts/maintenance/project_review_issue_promoter.py --packet <packet>` | A scout has one evidence-backed finding worth promoting | Markdown backlog tables, review artifacts, ad hoc JSON shapes |
| Promoted agent finding and implementation work | GitHub issue created or updated from the validated packet | Issue labels/comments and linked PRs | `gh issue view <number>`; PR checks and review state | The finding enters implementation, decision, or review routing | Local packet files as the ongoing queue, STATUS next-work lists |
| PR readiness and review feedback | GitHub PR, checks, review threads, and explicit repair/request comments | Bounded review-feedback packets copied into issue/PR comments when needed | `gh pr view <number> --json ...`; repo PR checks | A check or review produces actionable feedback | Durable docs, broad scratch plans, unlinked local notes |
| Generated inventories and machine schemas | Source code, generator scripts, and config inputs | Generated JSON/Markdown/DOT under `var/agents/**` | `uv run agent-regenerate --verify` | Code/config/generator inputs change | Hand-maintained reference tables, edited generated files without changing the source |
| Config profiles and templates | Tracked YAML/JSON config under `config/**`, `.env.template`, and typed config code | Generated env/config inventories in `var/agents/**`; docs pointers | Config tests plus `uv run agent-regenerate --verify` when inventories change | Profile, env var, or schema behavior changes | Untracked `.env`, docs tables as canonical config, runtime profile dumps |
| Runtime state and operational evidence | Per-profile runtime stores under `runtime_data/<profile>/`, including SQLite databases and generated reports | JSONL fallback/import streams and text/JSON reports for the same run | Readiness/preflight/report commands named by the operational doc | A local run, readiness check, or diagnostic command executes | Committed docs, review artifacts, issue bodies as raw runtime stores |
| Review CSV/XLSX deliverables | Intentional root-level `review_artifacts/*.csv` or `review_artifacts/*.xlsx` handoff files | CSV/XLSX only; PR body summarizes purpose and provenance | `git status --short review_artifacts .gitignore`; human review for size/secrets | A review lane produces a durable handoff artifact | `data/`, `runtime_data/`, `review_artifacts/tmp/`, broad CSV unignore rules, hidden planning truth |
| Temporary worksheets, scratch packets, and local exports | `work/` or `review_artifacts/tmp/` | Any local format that remains ignored | `git status --short --ignored work review_artifacts/tmp` when auditing | A task needs temporary state | `docs/`, GitHub issues unless promoted, committed review artifacts unless intentionally curated |

### Format selection rules

- **Markdown** is for durable prose, decision records, status pointers, and
  human-readable indexes. Do not convert stable prose to JSON just because it can
  be structured.
- **JSON** is for machine contracts: agent packets, generated schemas,
  inventories, and API-style exports that need validation or repeat routing.
- **YAML** is for tracked configuration/profile inputs and decision frontmatter,
  not for backlog or status mirrors.
- **CSV/XLSX** is for bounded human review deliverables under
  `review_artifacts/`. It is not a hidden planning database.
- **SQLite** is for runtime persistence such as event/order stores. Keep it in
  ignored runtime paths.
- **JSONL** is acceptable for append-only runtime logs, imports, and legacy
  fallbacks; when a SQLite store is documented as canonical, JSONL stays a
  fallback/display path.
- **GitHub issues/PRs** own the living queue and implementation review state.
  Docs may point to them; docs should not copy their changing contents.

## Where does X go?

| You want to record… | It goes in… | Never in… |
|---------------------|-------------|-----------|
| A decision — made **or** still open | `docs/decisions/<slug>.md` (status field carries the lifecycle) | STATUS, a roadmap "Needs Decision" table, a framework "Accepted Direction" section |
| What is built / current state | `docs/STATUS.md` (small, pointer-only) + the code/tests/`var/agents/**` it points to | ARCHITECTURE prose, rubric stage tables, README |
| A unit of work / something to build or fix | A **GitHub issue** (labels only for exceptions: `agent-ready`, `decision-needed`, `blocked`) | A roadmap "Ready Queue", a STATUS "spine" list |
| Where we are going / the gates to get there | `docs/DIRECTION.md` | Multiple parallel rubric/framework/readiness docs |
| How the code is structured | `docs/ARCHITECTURE.md` + generated maps in `var/agents/**` | Hand-maintained module inventories |
| API / CLI / env-var / metric inventories | Generated `var/agents/**` (run `agent-regenerate`) | Hand-written reference tables that must be kept in sync |
| How agents work / repo rules | `AGENTS.md` (canonical); `.claude/CLAUDE.md` points at it | Duplicated process prose across several docs |
| A per-task plan, audit, or scout finding | `work/` (gitignored); promote only the **decision or issue** it produces | Anywhere under `docs/` |
| A validated scout finding packet | JSON packet using `gpt-trader.agent-finding.v1`, then a GitHub issue after promotion | STATUS, roadmap docs, review spreadsheets |
| A review CSV/XLSX handoff | Root-level `review_artifacts/*.csv` or `review_artifacts/*.xlsx` | `review_artifacts/tmp/`, `data/`, `runtime_data/`, broad CSV unignore rules |
| Standards (naming, testing, security) | Their existing single home under `docs/` | A second "guidelines" doc |

## Anti-bloat rules

1. **State once, link elsewhere.** Restating another doc's fact is a bug. Replace
   the restatement with a link to the owner.
2. **Prefer derived over authored.** If a fact can be produced from code, tests,
   `var/agents/**`, or the issue tracker, do not also assert it in prose.
3. **Open decisions are not backlog tables.** A pending owner decision is a
   decision file with `status: proposed`, found by querying that field — not a
   "Needs Decision" list that has to be hand-pruned.
4. **Ephemeral never lands in `docs/`.** Planning and audit artifacts live in
   `work/`. What survives a task is the issue it filed or the decision it
   recorded, not the worksheet.
5. **Retire by deleting.** Git history is the archive. `docs/archive/` is banned
   (`docs_reachability_check.py` enforces this). To retire a doc, delete it and
   fix inbound links; `git log -- <path>` recovers it.
6. **No version-suffixed docs.** A `_V2` draft beside a `V1` is two homes for one
   fact. The new version either replaces the old in place, or is a
   `status: proposed` decision until adopted.

## Enforcement (the bloat guard)

These already exist and run against every markdown file under `docs/`. They are
what makes "state once, prefer derived" mechanically checkable instead of a good
intention:

| Check | Guards |
|-------|--------|
| `scripts/maintenance/docs_reachability_check.py` | Every doc is reachable from `docs/README.md` and carries a valid `status`. Orphans and stray homes fail. |
| `scripts/maintenance/docs_link_audit.py` | No dangling links or repo paths. Catches every reference broken by a retirement. |
| `scripts/maintenance/docs_currency_scan.py --fail-on missing,stale` | Commands, paths, env vars, and modules named in docs still exist in the code. Catches stale prose. Runs in the docs audit; missing/stale references fail the build, uncertain ones stay report-only, and genuine false positives go in the scanner's narrow, self-policing suppression allowlist. |
| `scripts/maintenance/generate_decision_index.py` | The `docs/decisions/` index is generated from frontmatter, so it cannot drift. |
| `agent-regenerate --verify` | Generated `var/agents/**` inventories match source truth. |

## When you add or change something

- Adding a fact? Find its row in the routing table and use that home. If no row
  fits, you are probably about to create bloat — prefer an issue or a decision
  file, and only add a new durable doc as a last resort.
- Changing state? Update the one owner (usually an issue, the code, or
  `STATUS.md`), not the three places that mention it.
- Retiring a doc? Delete it, then run `docs_link_audit.py` and
  `docs_reachability_check.py` and fix what they flag.
