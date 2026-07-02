# GPT-Trader Agent Review Pipeline

---
status: current
---

This document defines the recurring project-review pipeline for GPT-Trader. The
pipeline is intentionally staged: frequent scouts produce candidate findings,
and only validated, deduped findings are promoted to GitHub issues for
implementation or review loops.

## Goals

- Keep a frequent agent scout close to current repo truth.
- Promote only actionable, evidence-backed findings.
- Use GitHub issues as the durable project queue for implementation agents.
- Preserve GPT-Trader safety boundaries around broker access, trading
  execution, account capability, and explicit decision records.
- Feed PR review feedback back into implementation agents without broadening
  scope.

## Value Standard

A promoted issue is valuable only when an implementation agent can act on it
from the issue body alone. The packet must name the specific path, command, PR,
issue, or document that proves the problem; explain why the current state creates
repo maintenance drag, CI risk, agent handoff risk, or a trading-readiness gate;
and provide observable acceptance criteria plus a verification command.

Do not promote an issue just because the scout ran cleanly. A clean scout should
rotate into one bounded discovery lane, gather evidence, and promote only if the
lane exposes a concrete implementation target. If every checked lane is clean,
report the checked surfaces and the reason no issue was promoted.

Reject low-value packets when they depend on hidden context from the scout, use
only vague claims such as "clean up architecture" or "improve tests", cover too
many unrelated paths, require live broker/API access, or cannot be completed and
verified in one bounded PR.

## Canonical Artifacts In This Pipeline

The pipeline moves a finding through several homes. Each stage has one canonical
source; later stages may render or link the earlier packet, but they do not keep
it as a second queue.

| Stage | Canonical source | Allowed derived/display form | Validation / owner |
|-------|------------------|------------------------------|--------------------|
| Scout candidate | Local JSON packet with `schema_version: "gpt-trader.agent-finding.v1"` | Promoter dry-run body for human review | `uv run python scripts/maintenance/project_review_issue_promoter.py --packet <packet>` |
| Promoted finding | GitHub issue with the hidden `finding_id` marker and routing labels | Packet details rendered into the issue body or refresh comments | `project_review_issue_promoter.py` plus GitHub issue state |
| Implementation | GitHub issue or direct owner-routed package, then a PR | PR body links issue/finding/package source | PR checks and review state |
| Review feedback | GitHub review thread, check output, or a bounded feedback packet copied into issue/PR comments | Short fix packet for the active executor | PR review/check evidence; a follow-up issue when it becomes queue work |
| Review deliverable | Intended `review_artifacts/*.csv` or `review_artifacts/*.xlsx` file | PR body summary of provenance and purpose | `.gitignore` policy plus `git status` review |

The packet is canonical only before promotion. After promotion, the GitHub issue
is the durable queue item; local packet files are inputs or receipts, not the
ongoing source of truth.

## Stage 1: Scout

The scout is read-only and can run frequently. It should inspect current truth
before relying on memory or prior runs:

```bash
git status --short --branch
gh issue list --state open --limit 50
gh pr list --state open --limit 30
uv run agent-regenerate --verify
uv run local-ci --profile quick
```

The scout may run narrower commands when there are no relevant changes since a
recent run. It must not run real broker/API checks, canary operations, live
trading commands, production preflight, or anything that assumes account
capability unless a current decision packet or approved runbook explicitly
scopes that lane. A `decision_needed` finding is not authorization by itself; it
must first be resolved into a decision packet that scopes the command lane.

After the cheap truth pass is green, choose exactly one discovery lane for the
run. Rotate lanes across runs instead of scanning everything every hour.

| Lane | Cheap evidence | Promote only when |
| --- | --- | --- |
| Agent artifacts | `uv run agent-regenerate --verify`; inspect changed generator inputs when stale | A generated artifact or generator input has a bounded refresh/fix path |
| Local CI | `uv run local-ci --profile quick`; failing command output | A repeated failure points to repo code, tests, or docs rather than transient environment drift |
| Open queue | `gh issue list --state open --limit 50`; `gh pr list --state open --limit 30`; check whether the active branch has a PR | An issue or PR is stale, blocked, duplicated, or missing an agent-ready contract, or a repeated local review branch has no PR/issue/explicit parked blocker |
| Test hygiene | `uv run agent-dedupe --stats`; `uv run python scripts/ci/check_dedupe_manifest.py --strict` | The stats expose an actionable pending cluster, stale triage, or manifest drift |
| Architecture contracts | Read one named contract doc such as `docs/DI_POLICY.md`, `docs/ARCHITECTURE.md`, or `docs/DIRECTION.md`; compare with a narrow code path | A concrete docs/code mismatch or boundary violation has a safe verification command |
| Agent ergonomics | Inspect one agent-facing doc, generated map, workflow script, or recent scout memory | A missing or stale instruction causes repeat agent confusion, repeated no-promotion churn, or an ambiguous finish/park decision that can be fixed in one PR |

Good scout signals include stale generated artifacts, a repeated quick local-CI
failure, a stale or blocked open PR, a duplicated test cluster with a clear next
packet, a drift between docs and code, a narrow architectural boundary issue, or
an agent-facing workflow gap that has already caused a low-value run.

Poor scout signals include speculative refactor ideas, transient network
failures, broad "clean up the repo" claims, or anything that lacks a named path,
command output, existing issue/PR context, or acceptance criteria. Do not use
"already in local dirty work" as the sole no-promotion reason on repeated runs:
after one run, either identify the active branch's PR, promote a bounded
finish-or-park finding, or record the explicit blocker that makes promotion
unsafe.

## Stage 2: Normalize

Candidate findings use schema `gpt-trader.agent-finding.v1`.

The current contract authority is the in-repo promoter:

- `SCHEMA_VERSION`, `validate_packet()`, and `example_packet()` in
  `scripts/maintenance/project_review_issue_promoter.py`
- `uv run python scripts/maintenance/project_review_issue_promoter.py --print-template`
  for a starter packet
- `uv run python scripts/maintenance/project_review_issue_promoter.py --packet <packet>`
  for validation and dry-run rendering

Do not introduce a second packet shape. Extract a standalone JSON Schema only
when a second machine consumer needs it, and keep that schema generated from or
tested against the promoter validator so the two contracts cannot drift.

Required fields:

```json
{
  "schema_version": "gpt-trader.agent-finding.v1",
  "finding_id": "stable-lowercase-id",
  "title": "Short actionable issue title",
  "severity": "low",
  "category": "ci",
  "summary": "One-paragraph explanation of the actionable defect or improvement.",
  "evidence": [
    {
      "kind": "command",
      "command": "uv run agent-regenerate --verify",
      "detail": "Command failed because generated files are stale."
    },
    {
      "kind": "file",
      "path": "scripts/agents/regenerate_all.py",
      "detail": "The generator input controls the stale artifact."
    }
  ],
  "scope": {
    "paths": ["var/agents/index.json"],
    "out_of_scope": ["live trading changes"],
    "touches_trading_execution": false
  },
  "dedupe": {
    "search_terms": ["agent-regenerate", "stale var/agents"],
    "related_issues": []
  },
  "acceptance_criteria": [
    "Generated agent artifacts verify cleanly."
  ],
  "suggested_verification": [
    "uv run agent-regenerate --verify"
  ],
  "routing": {
    "candidate_for": ["implementation"],
    "decision_needed": false,
    "blocked_by": []
  }
}
```

Required evidence is anchored. Each item must include `detail` plus at least one
of `command`, `path`, or `url` so the issue body remains useful outside the
scout run.

Allowed severities are `low`, `medium`, `high`, and `critical`.

Allowed categories are `bug`, `ci`, `docs`, `architecture`, `tests`,
`cleanup`, `security`, `trading-readiness`, and `tooling`.

If `scope.touches_trading_execution` is true, the finding must either be
docs/test-only and clearly execution-free, or `routing.decision_needed` must be
true. Use `docs/DIRECTION.md` for anything that touches
execution automation, broker adapters, venue support, account capability, or
AI-assisted execution, and include enough context for a decision agent to
recommend the next policy and implementation route.

## Stage 3: Promote

Promotion creates or updates a GitHub issue from a validated packet. Promote at
most one finding per scout run unless the project owner explicitly requests a
bulk pass.

Dry-run first:

```bash
uv run python scripts/maintenance/project_review_issue_promoter.py \
  --packet path/to/finding.json
```

Create or update the GitHub issue:

```bash
uv run python scripts/maintenance/project_review_issue_promoter.py \
  --packet path/to/finding.json \
  --create-issue \
  --create-labels
```

The promoter searches for the hidden `finding_id` marker before creating a new
issue. When a matching open issue exists, it comments with the refreshed packet
instead of creating a duplicate.

## Stage 4: Queue

GitHub issues are the durable queue. Labels mark exceptions only (the full
model is owned by [CONTRIBUTING.md](../../CONTRIBUTING.md#issue-labels)); the
promoter emits:

| Label | Meaning |
| --- | --- |
| `agent-review` | Finding came from the recurring GPT-Trader review lane |
| `agent-ready` | Finding passed promotion gates, has no decision/blocker gate, and is ready for implementation |
| `decision-needed` | Requires an explicit decision packet and agent recommendation before implementation or execution |
| `blocked` | The packet names `routing.blocked_by` dependencies |

The promoter can create missing labels with `--create-labels`. If labels are
missing and that flag is not used, it keeps routing in the issue body and omits
unknown labels from the GitHub call.

## Stage 5: Implement

Implementation agents should treat each promoted issue as the implementation
contract:

- work only the acceptance criteria in the issue
- preserve out-of-scope boundaries
- open a PR that links the issue
- run the smallest meaningful verification plus repo-required checks
- leave any broader discovery as a new finding instead of scope creep

The PR body should follow `.github/pull_request_template.md`. For
issue-backed work, include `Closes #<issue>`.

### Direct Package Cycles

When the project owner or the active agent workspace explicitly routes a
build/package cycle, a promoted GitHub issue is useful but not required. In that
path, the direct request, the observed repo evidence, and the PR body form the
implementation contract.

Use this path only when the work is already bounded and safe to verify locally:

- name the package source in the PR body, such as `owner-routed package`,
  `agent-review finding`, or a specific doc/receipt
- preserve the same trading boundaries as issue-backed work
- run the smallest meaningful verification plus repo-required checks
- push the branch and open the PR once the package is clean
- do not stop at "ready for PR decision" unless a concrete blocker or explicit
  park instruction exists

Merge remains a separate later pipeline decision. Opening a review PR is part
of packaging; merging is not.

## Stage 6: Review Feedback

When the ChatGPT-Codex-Connector review bot or GitHub checks produce feedback,
convert each actionable item into a bounded fix packet for the active executor:

- PR URL and branch
- review comment or check URL
- exact file/line when available
- required behavior change
- verification command
- stop condition

If the feedback contradicts the issue acceptance criteria or crosses an
undecided policy boundary, label the issue or PR `decision-needed` and route a
decision packet before implementation continues.

Review-feedback packets are routing packets, not a new durable planning system.
Keep them attached to the PR, the linked issue, or the active executor handoff.
Use a structured JSON contract only if repeated review-feedback routing needs
machine validation; until then, the bullet contract above is the source.

## Stage 7: Merge And Closeout

Merge is a separate, later decision — not part of packaging (see Stage 5). When
the change is explicitly routed/approved for merge and all review threads are
resolved, merge per `AGENTS.md`:

```bash
gh pr merge --squash --delete-branch
```

After merge, confirm the issue is closed or comment with the merged PR and any
follow-up. New work gets a new finding packet.

## Review Artifacts Convention (from #968, run goal-pipeline-20260626-001-gpt-trader-clean-discovery-scout)

Review and analysis artifacts (spreadsheets, CSVs, reports) produced by agent review lanes:

- Commit only intended root-level `review_artifacts/*.csv` and
  `review_artifacts/*.xlsx` files under the existing `.gitignore` exceptions.
- Put non-durable temps in `review_artifacts/tmp/` (still ignored).
- Treat committed CSV/XLSX files as review deliverables, not canonical planning
  state. The queue remains GitHub issues; project status remains `docs/STATUS.md`.
- Name durable files with a stable subject and date or run id, and include enough
  context in the PR body to explain provenance, source command, and intended
  reviewer.
- Avoid committing large datasets, secrets, runtime databases, or broad generated
  CSVs. If a review needs those locally, keep them ignored.
- Verification: after creating an artifact, `git status --short review_artifacts
  .gitignore` should show only the intended review files; broad generated CSVs
  outside remain ignored.

This supports repeatable review workflow without polluting commits or losing handoff data.

## Cadence

Cadence is owner-managed: the project owner decides how often the scout runs and
may run it on demand. There is no required automation. If a recurring schedule is
set up, keep the interval loose and tighten it only after the issue promotion
rate is low, dedupe is reliable, and implementation agents are not receiving
stale or duplicate work.
