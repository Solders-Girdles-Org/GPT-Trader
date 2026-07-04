## Summary
<!-- What and why. Keep it crisp. Link related issues. -->
Related issue / finding / routed package:

## Type of change
- [ ] Refactor
- [ ] Feature
- [ ] Bug fix
- [ ] Tests only
- [ ] CI/Docs/Tooling

## Scope & Impact
- Affected areas / components:
- Any behavior changes? If yes, describe and link tests.

## Test Plan
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Contract tests (if applicable)
- [ ] Ran locally: `pytest -m "unit and not slow"`
- [ ] Markers used appropriately (`unit`/`integration`/`slow`/`perf`)

## Quality Gates
- [ ] `uv run local-ci` passes locally (lint/format, docs audits, type check, advisory agent freshness, test guardrails, unit/property/contract/integration tests)
- [ ] `uv run mypy src/gpt_trader` clean, or new errors documented in the summary
- [ ] No `sys.path` hacks in tests; `AsyncMock` (not `MagicMock`) on `async def`
- [ ] Import boundaries respected (`scripts/ci/check_import_boundaries.py`)

## Observability & Errors
- [ ] Structured JSON logs for new/changed paths
- [ ] Errors include diagnostic context (symbol, order_id, correlation_id)

## Agent Artifacts & Config (if touched)
- [ ] If your change can affect generated `var/agents/**` context: ran `uv run agent-regenerate` and committed the refreshed artifacts (`uv run agent-regenerate --verify` clean). The exact inputs and freshness/CI contract live in `docs/DEVELOPMENT_GUIDELINES.md` and the CI path classifier
- [ ] If docs changed: `make agent-docs-links` passes

## Breaking Changes
- [ ] None
- [ ] Documented in PR body and the linked issue if any

## Screenshots / Logs (optional)
<!-- Paste sample structured logs or screenshots to aid review -->

## Checklist
- [ ] Acceptance criteria in linked issue(s), finding packet, or routed package met
- [ ] Affected paths listed in PR description
