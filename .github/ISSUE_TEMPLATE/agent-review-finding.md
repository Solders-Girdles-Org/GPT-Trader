---
name: Agent review finding
about: Promote a validated recurring-review finding for agent implementation
labels: agent-review
---

<!-- gpt-trader-agent-finding-id: replace-with-stable-id -->

## Summary

Describe the actionable finding and why it matters.

## Evidence

- Command/file/PR evidence with at least one anchor (`command`, `path`, or `url`):

## Scope

Affected paths:
- `path/to/file`

Out of scope:
- Live trading or broker/account-capability changes unless explicitly approved

Touches trading execution: `false`

## Acceptance Criteria

- [ ] The bounded issue is fixed without widening beyond the affected paths above.
- [ ] The suggested verification command passes.

## Suggested Verification

- `uv run local-ci --profile quick`

## Routing

- Candidate for: implementation
- Decision needed: `false`
- Blocked by: none

## Dedupe

- Search terms used:
