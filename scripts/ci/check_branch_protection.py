#!/usr/bin/env python3
"""Check GitHub branch protection against the expected merge-gate contract.

Usage:
    uv run python scripts/ci/check_branch_protection.py
    uv run python scripts/ci/check_branch_protection.py --repo owner/name --branch main

The EXPECTED_* constants below are the machine-checkable source of truth for
what `main` branch protection must require; the prose CI contract table in
docs/DEVELOPMENT_GUIDELINES.md points here. Update them deliberately, in the
same change that updates the GitHub setting. The merge-queue migration (#1127,
2026-07-04) flipped EXPECTED_STRICT to False: the queue validates each entry
against the latest main via merge_group CI, superseding strict up-to-date.

Requires `gh` auth with admin read on the repository (the protection API is
admin-scoped), so this runs locally and in `agent-pr-ready` — not in the
PR-blocking CI lane.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any
from urllib.parse import quote

DEFAULT_REPOSITORY = "Solders-Girdles-Org/GPT-Trader"
DEFAULT_BRANCH = "main"

EXPECTED_REQUIRED_CHECKS = frozenset(
    {
        "Lint & Format",
        "Docs Link Audit",
        "Type Check",
        "Test Guardrails",
        "Unit Tests (Core)",
        "Property Tests",
        "Contract Tests",
        "Integration Tests",
    }
)
EXPECTED_STRICT = False
EXPECTED_ENFORCE_ADMINS = True
EXPECTED_CONVERSATION_RESOLUTION = True
EXPECTED_REQUIRED_APPROVING_REVIEWS = 0


def assess_protection_drift(raw: dict[str, Any] | None) -> list[str]:
    """Return human-readable drift findings; an empty list means the contract holds."""
    if not isinstance(raw, dict) or not raw:
        return ["branch protection payload is missing or empty"]

    drift: list[str] = []

    checks_block = raw.get("required_status_checks") or {}
    modern = [
        item.get("context", "")
        for item in checks_block.get("checks") or []
        if isinstance(item, dict) and item.get("context")
    ]
    legacy = [context for context in checks_block.get("contexts") or [] if isinstance(context, str)]
    live_checks = set(modern) | set(legacy)
    missing = sorted(EXPECTED_REQUIRED_CHECKS - live_checks)
    extra = sorted(live_checks - EXPECTED_REQUIRED_CHECKS)
    if missing:
        drift.append(f"required checks missing: {', '.join(missing)}")
    if extra:
        drift.append(f"unexpected required checks: {', '.join(extra)}")

    strict = bool(checks_block.get("strict", False))
    if strict != EXPECTED_STRICT:
        drift.append(f"strict up-to-date is {strict}, expected {EXPECTED_STRICT}")

    enforce_admins = bool((raw.get("enforce_admins") or {}).get("enabled", False))
    if enforce_admins != EXPECTED_ENFORCE_ADMINS:
        drift.append(f"enforce_admins is {enforce_admins}, expected {EXPECTED_ENFORCE_ADMINS}")

    conversation = bool((raw.get("required_conversation_resolution") or {}).get("enabled", False))
    if conversation != EXPECTED_CONVERSATION_RESOLUTION:
        drift.append(
            f"required conversation resolution is {conversation}, "
            f"expected {EXPECTED_CONVERSATION_RESOLUTION}"
        )

    reviews_block = raw.get("required_pull_request_reviews") or {}
    review_count = int(reviews_block.get("required_approving_review_count", 0) or 0)
    if review_count != EXPECTED_REQUIRED_APPROVING_REVIEWS:
        drift.append(
            f"required approving reviews is {review_count}, "
            f"expected {EXPECTED_REQUIRED_APPROVING_REVIEWS}"
        )

    return drift


def fetch_protection(repo: str, branch: str) -> dict[str, Any]:
    encoded_branch = quote(branch, safe="")
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/branches/{encoded_branch}/protection"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh api call for branch protection failed")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected branch protection payload shape")
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY, help="GitHub repository owner/name.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Protected branch to check.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        raw = fetch_protection(args.repo, args.branch)
    except (RuntimeError, json.JSONDecodeError, OSError) as error:
        print(f"✗ Branch protection check FAILED: {error}")
        return 1

    drift = assess_protection_drift(raw)
    if drift:
        for item in drift:
            print(f"✗ Branch protection drift: {item}")
        return 1

    print(f"✓ Branch protection matches the expected contract ({args.repo}@{args.branch})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
