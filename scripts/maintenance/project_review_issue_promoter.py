#!/usr/bin/env python3
"""Promote a GPT-Trader agent finding packet to a GitHub issue."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_REPOSITORY = "Solders-Girdles/GPT-Trader"
SCHEMA_VERSION = "gpt-trader.agent-finding.v1"

SEVERITIES = {"low", "medium", "high", "critical"}
CATEGORIES = {
    "architecture",
    "bug",
    "ci",
    "cleanup",
    "docs",
    "security",
    "tests",
    "tooling",
    "trading-readiness",
}
CANDIDATES = {"implementation", "codex-review", "decision"}
EVIDENCE_ANCHOR_FIELDS = ("command", "path", "url")

CUSTOM_LABELS = {
    "agent-review": {
        "color": "5319e7",
        "description": "Produced by the recurring GPT-Trader agent review lane",
    },
    "agent-ready": {
        "color": "0e8a16",
        "description": "Validated and ready for agent implementation",
    },
    "decision-needed": {
        "color": "d876e3",
        "description": "Requires an explicit decision packet and agent recommendation",
    },
    "blocked": {
        "color": "b60205",
        "description": "Blocked on a dependency or decision",
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, help="Finding packet JSON path, or '-' for stdin.")
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY, help="GitHub repository owner/name.")
    parser.add_argument(
        "--create-issue",
        action="store_true",
        help="Create or update the GitHub issue. Omit for dry-run output.",
    )
    parser.add_argument(
        "--create-labels",
        action="store_true",
        help="Create missing routing labels before creating/updating the issue.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable output for the planned or completed action.",
    )
    parser.add_argument(
        "--print-template",
        action="store_true",
        help="Print an example finding packet and exit.",
    )
    return parser.parse_args(argv)


def read_packet(path: Path | None) -> dict[str, Any]:
    if path is None:
        raise ValueError("--packet is required unless --print-template is used")
    if str(path) == "-":
        return json.loads(sys.stdin.read())
    return json.loads(path.read_text(encoding="utf-8"))


def example_packet() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "finding_id": "agent-artifacts-stale-example",
        "title": "Refresh stale generated agent artifacts",
        "severity": "low",
        "category": "ci",
        "summary": "The generated agent context no longer verifies cleanly.",
        "evidence": [
            {
                "kind": "command",
                "command": "uv run agent-regenerate --verify",
                "detail": "Verify reported stale generated files.",
            }
        ],
        "scope": {
            "paths": ["var/agents"],
            "out_of_scope": ["live trading changes"],
            "touches_trading_execution": False,
        },
        "dedupe": {
            "search_terms": ["agent-regenerate", "stale generated agent artifacts"],
            "related_issues": [],
        },
        "acceptance_criteria": ["`uv run agent-regenerate --verify` passes."],
        "suggested_verification": ["uv run agent-regenerate --verify"],
        "routing": {
            "candidate_for": ["implementation"],
            "decision_needed": False,
            "blocked_by": [],
        },
    }


def validate_packet(packet: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if packet.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")

    finding_id = packet.get("finding_id")
    if not isinstance(finding_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_.-]{2,80}", finding_id
    ):
        errors.append(
            "finding_id must be 3-81 lowercase letters, digits, dots, underscores, or hyphens"
        )

    for field_name in ("title", "summary"):
        if not isinstance(packet.get(field_name), str) or not packet[field_name].strip():
            errors.append(f"{field_name} must be a non-empty string")

    if packet.get("severity") not in SEVERITIES:
        errors.append(f"severity must be one of {sorted(SEVERITIES)}")

    if packet.get("category") not in CATEGORIES:
        errors.append(f"category must be one of {sorted(CATEGORIES)}")

    evidence = packet.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must be a non-empty list")
    else:
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                errors.append(f"evidence[{index}] must be an object")
                continue
            if not isinstance(item.get("kind"), str) or not item["kind"].strip():
                errors.append(f"evidence[{index}].kind must be a non-empty string")
            if not isinstance(item.get("detail"), str) or not item["detail"].strip():
                errors.append(f"evidence[{index}].detail must be a non-empty string")
            if not any(
                isinstance(item.get(field), str) and item[field].strip()
                for field in EVIDENCE_ANCHOR_FIELDS
            ):
                errors.append(
                    f"evidence[{index}] must include at least one anchor: "
                    f"{', '.join(EVIDENCE_ANCHOR_FIELDS)}"
                )

    scope = packet.get("scope")
    if not isinstance(scope, dict):
        errors.append("scope must be an object")
        scope = {}
    paths = scope.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
        errors.append("scope.paths must be a non-empty list of strings")
    out_of_scope = scope.get("out_of_scope")
    if out_of_scope is not None and not isinstance(out_of_scope, list):
        errors.append("scope.out_of_scope must be a list when present")
    if not isinstance(scope.get("touches_trading_execution"), bool):
        errors.append("scope.touches_trading_execution must be true or false")

    dedupe = packet.get("dedupe")
    if not isinstance(dedupe, dict):
        errors.append("dedupe must be an object")
        dedupe = {}
    search_terms = dedupe.get("search_terms")
    if not isinstance(search_terms, list) or not all(
        isinstance(term, str) and term.strip() for term in search_terms
    ):
        errors.append("dedupe.search_terms must be a non-empty list of strings")

    for field_name in ("acceptance_criteria", "suggested_verification"):
        values = packet.get(field_name)
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value.strip() for value in values
        ):
            errors.append(f"{field_name} must be a non-empty list of strings")

    routing = packet.get("routing")
    if not isinstance(routing, dict):
        errors.append("routing must be an object")
        routing = {}
    candidates = routing.get("candidate_for")
    normalized_candidates: set[str] = set()
    if not isinstance(candidates, list) or not candidates:
        errors.append("routing.candidate_for must be a non-empty list")
    else:
        non_string_candidates = [
            candidate for candidate in candidates if not isinstance(candidate, str)
        ]
        if non_string_candidates:
            errors.append("routing.candidate_for must contain only strings")
            unknown_candidates: list[str] = []
        else:
            normalized_candidates = set(candidates)
            unknown_candidates = sorted(normalized_candidates - CANDIDATES)
        if unknown_candidates:
            errors.append(f"routing.candidate_for contains unknown values: {unknown_candidates}")
    if not isinstance(routing.get("decision_needed"), bool):
        errors.append("routing.decision_needed must be true or false")
    elif "decision" in normalized_candidates and not routing.get("decision_needed"):
        errors.append(
            "routing.candidate_for includes decision requires routing.decision_needed=true"
        )

    if scope.get("touches_trading_execution") and not routing.get("decision_needed"):
        errors.append("scope.touches_trading_execution=true requires routing.decision_needed=true")

    blocked_by = routing.get("blocked_by", [])
    if "blocked_by" in routing and not isinstance(blocked_by, list):
        errors.append("routing.blocked_by must be a list when present")
    elif isinstance(blocked_by, list) and not all(
        isinstance(blocker, str) and blocker.strip() for blocker in blocked_by
    ):
        errors.append("routing.blocked_by must contain only non-empty strings")

    return errors


def marker_for(finding_id: str) -> str:
    return f"gpt-trader-agent-finding-id: {finding_id}"


def packet_labels(packet: dict[str, Any]) -> list[str]:
    labels = {"agent-review"}

    routing = packet.get("routing", {})
    if isinstance(routing, dict):
        blocked_by = routing.get("blocked_by", [])
        candidates = routing.get("candidate_for", [])
        decision_candidate = isinstance(candidates, list) and "decision" in candidates
        if blocked_by:
            labels.add("blocked")
        if not routing.get("decision_needed") and not blocked_by and not decision_candidate:
            labels.add("agent-ready")
        if routing.get("decision_needed") or decision_candidate:
            labels.add("decision-needed")

    return sorted(labels)


def run_gh(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def existing_labels(repository: str) -> set[str]:
    result = run_gh(["label", "list", "--repo", repository, "--limit", "300", "--json", "name"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to list GitHub labels")
    data = json.loads(result.stdout)
    return {item["name"] for item in data}


def ensure_labels(repository: str, labels: list[str]) -> None:
    present = existing_labels(repository)
    for label in labels:
        if label in present or label not in CUSTOM_LABELS:
            continue
        metadata = CUSTOM_LABELS[label]
        result = run_gh(
            [
                "label",
                "create",
                label,
                "--repo",
                repository,
                "--color",
                metadata["color"],
                "--description",
                metadata["description"],
            ]
        )
        if result.returncode != 0 and "already exists" not in result.stderr:
            raise RuntimeError(result.stderr.strip() or f"failed to create label {label}")


def usable_labels(
    repository: str, labels: list[str], create_labels: bool
) -> tuple[list[str], list[str]]:
    if create_labels:
        ensure_labels(repository, labels)
    present = existing_labels(repository)
    selected = [label for label in labels if label in present]
    missing = [label for label in labels if label not in present]
    return selected, missing


def find_existing_issue(repository: str, finding_id: str) -> dict[str, Any] | None:
    marker = marker_for(finding_id)
    result = run_gh(
        [
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            "open",
            "--search",
            f'"{marker}" in:body',
            "--limit",
            "5",
            "--json",
            "number,title,url",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to search existing issues")
    matches = json.loads(result.stdout)
    return matches[0] if matches else None


def render_issue_body(packet: dict[str, Any], labels: list[str], missing_labels: list[str]) -> str:
    scope = packet["scope"]
    dedupe = packet["dedupe"]
    routing = packet["routing"]
    evidence_lines = []
    for item in packet["evidence"]:
        command = item.get("command")
        path = item.get("path")
        url = item.get("url")
        if command:
            prefix = f"- `{command}`: "
        elif path:
            prefix = f"- `{path}`: "
        elif url:
            prefix = f"- {url}: "
        else:
            prefix = f"- {item['kind']}: "
        evidence_lines.append(prefix + item["detail"])

    body = [
        f"<!-- {marker_for(packet['finding_id'])} -->",
        "",
        "## Summary",
        packet["summary"].strip(),
        "",
        "## Evidence",
        *evidence_lines,
        "",
        "## Scope",
        "Affected paths:",
        *[f"- `{path}`" for path in scope["paths"]],
        "",
        "Out of scope:",
        *[f"- {item}" for item in scope.get("out_of_scope", [])],
        "",
        f"Touches trading execution: `{str(scope['touches_trading_execution']).lower()}`",
        "",
        "## Acceptance Criteria",
        *[f"- [ ] {item}" for item in packet["acceptance_criteria"]],
        "",
        "## Suggested Verification",
        *[f"- `{item}`" for item in packet["suggested_verification"]],
        "",
        "## Routing",
        f"- Candidate for: {', '.join(routing['candidate_for'])}",
        f"- Decision needed: `{str(routing['decision_needed']).lower()}`",
        f"- Blocked by: {', '.join(routing.get('blocked_by', [])) or 'none'}",
        "",
        "## Dedupe",
        *[f"- {term}" for term in dedupe["search_terms"]],
        "",
        "## Labels",
        f"- Applied/planned: {', '.join(labels) if labels else 'none'}",
    ]
    if missing_labels:
        body.extend(["", f"- Missing in repository: {', '.join(missing_labels)}"])
    return "\n".join(body).rstrip() + "\n"


def create_or_update_issue(
    repository: str,
    packet: dict[str, Any],
    labels: list[str],
    missing_labels: list[str],
) -> dict[str, Any]:
    body = render_issue_body(packet, labels, missing_labels)
    existing = find_existing_issue(repository, packet["finding_id"])
    if existing:
        result = run_gh(
            [
                "issue",
                "comment",
                str(existing["number"]),
                "--repo",
                repository,
                "--body",
                body,
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "failed to comment on existing issue")
        if labels:
            edit_result = run_gh(
                [
                    "issue",
                    "edit",
                    str(existing["number"]),
                    "--repo",
                    repository,
                    "--add-label",
                    ",".join(labels),
                ]
            )
            if edit_result.returncode != 0:
                raise RuntimeError(edit_result.stderr.strip() or "failed to update issue labels")
        return {"action": "commented_existing", "issue": existing, "missing_labels": missing_labels}

    command = [
        "issue",
        "create",
        "--repo",
        repository,
        "--title",
        packet["title"],
        "--body",
        body,
    ]
    for label in labels:
        command.extend(["--label", label])
    result = run_gh(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to create issue")
    url = result.stdout.strip()
    return {"action": "created", "issue": {"url": url}, "missing_labels": missing_labels}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.print_template:
        print(json.dumps(example_packet(), indent=2))
        return 0

    try:
        packet = read_packet(args.packet)
        errors = validate_packet(packet)
        if errors:
            for error in errors:
                print(f"error: {error}", file=sys.stderr)
            return 2

        planned_labels = packet_labels(packet)
        if args.create_issue:
            labels, missing_labels = usable_labels(args.repo, planned_labels, args.create_labels)
            result = create_or_update_issue(args.repo, packet, labels, missing_labels)
        else:
            result = {
                "action": "dry_run",
                "repository": args.repo,
                "finding_id": packet["finding_id"],
                "title": packet["title"],
                "planned_labels": planned_labels,
                "body": render_issue_body(packet, planned_labels, []),
            }

        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif result["action"] == "dry_run":
            print(result["body"])
        else:
            issue = result["issue"]
            print(f"{result['action']}: {issue.get('url') or issue}")
            if result.get("missing_labels"):
                print(f"missing labels: {', '.join(result['missing_labels'])}")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
