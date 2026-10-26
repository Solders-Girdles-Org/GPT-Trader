#!/usr/bin/env python3
"""Validate findings and plan promotion through the authenticated native Linear connector."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

LINEAR_TEAM_ID = "6dac4998-d0f1-4eb9-8739-179db1379d55"
LINEAR_PROJECT_ID = "b621cc17-1650-432b-802d-4575507d3a5b"
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, help="Finding packet JSON path, or '-' for stdin.")
    parser.add_argument(
        "--linear-snapshot",
        type=Path,
        help="Fresh native Linear search export, including terminal and archived matches.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a native connector action plan.")
    parser.add_argument("--print-template", action="store_true", help="Print an example packet.")
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
        "finding_id": "docs-link-audit-example",
        "title": "Fix dangling links reported by the docs link audit",
        "severity": "low",
        "category": "docs",
        "summary": "The docs link audit reports links to paths that no longer exist.",
        "evidence": [
            {
                "kind": "command",
                "command": "uv run python scripts/maintenance/docs_link_audit.py",
                "detail": "The audit reported dangling repo-path links.",
            }
        ],
        "scope": {
            "paths": ["docs"],
            "out_of_scope": ["live trading changes"],
            "touches_trading_execution": False,
        },
        "dedupe": {
            "search_terms": ["docs link audit", "dangling links"],
            "related_issues": [],
        },
        "acceptance_criteria": ["`uv run python scripts/maintenance/docs_link_audit.py` passes."],
        "suggested_verification": ["uv run python scripts/maintenance/docs_link_audit.py"],
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
        marker_for(packet["finding_id"]),
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
        "## Routing hints",
        f"- Hints (not Linear labels): {', '.join(labels) if labels else 'none'}",
    ]
    if missing_labels:
        body.extend(["", f"- Missing in repository: {', '.join(missing_labels)}"])
    return "\n".join(body).rstrip() + "\n"


def promotion_plan(packet: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Plan only; an agent executes through its existing native Linear connection.

    Search results are fresh inputs, not an ongoing queue or credentials. Never
    create from a failed, truncated, wrong-destination or active-only search.
    """
    marker = marker_for(packet["finding_id"])
    search = {
        "team": LINEAR_TEAM_ID,
        "project": LINEAR_PROJECT_ID,
        "query": marker,
        "includeArchived": True,
        "fields": ["id", "description", "url", "statusType", "archivedAt", "teamId", "projectId"],
    }
    result: dict[str, Any] = {
        "finding_id": packet["finding_id"],
        "title": packet["title"],
        "body": render_issue_body(packet, packet_labels(packet), []),
        "search": {"tool": "list_issues", "arguments": search},
    }
    if snapshot is None:
        return {**result, "action": "search_required"}
    expected = {
        "team_id": LINEAR_TEAM_ID,
        "project_id": LINEAR_PROJECT_ID,
        "query": marker,
        "include_archived": True,
        "has_next_page": False,
    }
    if any(key not in snapshot or snapshot[key] != value for key, value in expected.items()):
        raise ValueError(
            "snapshot must be a complete all-state, archived-inclusive native search for this finding and destination"
        )
    issues = snapshot.get("issues")
    if not isinstance(issues, list):
        raise ValueError("snapshot.issues must be a list")
    matches = []
    pattern = re.compile(r"^\s*(?:<!--\s*)?" + re.escape(marker) + r"(?:\s*-->)?\s*$", re.MULTILINE)
    for issue in issues:
        if not isinstance(issue, dict) or not isinstance(issue.get("description"), str):
            raise ValueError("search results must include issue descriptions")
        if "(truncated, use `get_issue` for full description)" in issue["description"]:
            raise ValueError(
                "hydrate truncated search descriptions with native get_issue before planning"
            )
        if issue.get("teamId") != LINEAR_TEAM_ID or issue.get("projectId") != LINEAR_PROJECT_ID:
            raise ValueError("search result is outside the registered Linear destination")
        if pattern.search(issue["description"]):
            if not issue.get("id") or not issue.get("url") or "archivedAt" not in issue:
                raise ValueError("matching issue lacks identity, URL or archive state")
            if issue.get("statusType") not in {
                "triage",
                "backlog",
                "unstarted",
                "started",
                "completed",
                "canceled",
            }:
                raise ValueError("matching issue has unknown statusType")
            matches.append(issue)
    if len(matches) > 1:
        raise ValueError("multiple exact finding_id matches; reconcile on Linear before promotion")
    if matches:
        issue = matches[0]
        if issue["statusType"] in {"completed", "canceled"} or issue["archivedAt"]:
            return {**result, "action": "matched_terminal", "issue": issue}
        return {
            **result,
            "action": "comment_existing",
            "issue": issue,
            "request": {
                "tool": "save_comment",
                "arguments": {"issueId": issue["id"], "body": result["body"]},
            },
        }
    return {
        **result,
        "action": "create",
        "request": {
            "tool": "save_issue",
            "arguments": {
                "team": LINEAR_TEAM_ID,
                "project": LINEAR_PROJECT_ID,
                "title": packet["title"],
                "description": result["body"],
                "state": "Backlog",
                "labels": ["decision" if packet["routing"]["decision_needed"] else "work"],
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.print_template:
        print(json.dumps(example_packet(), indent=2))
        return 0

    try:
        packet = read_packet(args.packet)
        if not isinstance(packet, dict):
            raise ValueError("packet must be an object")
        errors = validate_packet(packet)
        if errors:
            for error in errors:
                print(f"error: {error}", file=sys.stderr)
            return 2

        snapshot = (
            json.loads(args.linear_snapshot.read_text(encoding="utf-8"))
            if args.linear_snapshot
            else None
        )
        if snapshot is not None and not isinstance(snapshot, dict):
            raise ValueError("snapshot must be an object")
        result = promotion_plan(packet, snapshot)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(result["body"])
            print(f"Native Linear action: {result['action']} (no remote write performed)")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
