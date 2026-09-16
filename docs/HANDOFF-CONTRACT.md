---
status: current
last-updated: 2026-09-16
---

# Cross-agent handoff contract

One packet shape for every handoff between Codex, Claude Code, Hermes and
RJ, in any direction. RJ decides scope and acceptance; RJ does not carry
text between agents.

## Packet

| Field | Content |
| --- | --- |
| goal | The outcome wanted, in one or two sentences. |
| context | Paths, PR or issue numbers, current state, prior decisions. Links, not copies. |
| constraints | Authority limits, forbidden actions, project gates that apply. |
| deliverable | The exact artifact and where it goes. |
| return path | Where the result will be read from: PR review, file path, or task. |
| verification | What was already checked, and what the receiver must still check. |

Send the minimum sufficient context. Never send a whole transcript,
credentials, private communications or unrelated personal material.

## Code handoffs are the pull request

1. The implementer (usually Codex) opens the PR. The PR body carries the
   packet. Verification lists the checks already run.
2. The reviewer (usually Claude Code) reads the PR with `gh`, inspects the
   diff in a temporary detached worktree that it removes when done, and
   posts findings as a PR review with file and line evidence. Reviews are
   read-only. No review worktree outlives the review.
3. The implementer consumes the review from GitHub (Codex:
   `gh-address-comments`), replies on the thread, and re-requests review.
4. Merge follows the owning project's rules. Standing merge approvals in
   project files still apply.

## Non-code handoffs

Write `HANDOFF.md` with the six fields in the project's scratch convention
(`work/<task-id>/` unless the project defines another). The return path
names the file or task where the result lands. When a result is promoted,
the closeout names the canonical destination.

## Consultations

Codex consults Claude through `call-claude`; Claude consults Codex through
`call-codex`. One bounded consultation by default. The consulted model's
output is evidence, not authority. Verify material claims against live
files and tests before acting on them.

## What this does not change

Project `AGENTS.md` files, evidence contracts, merge gates, and the
Personal Automations scheduled-stage subagent prohibition all govern as
before. This contract only fixes the shape of the handoff.
