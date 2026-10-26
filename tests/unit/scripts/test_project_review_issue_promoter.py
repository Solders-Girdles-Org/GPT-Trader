from __future__ import annotations

import json
from pathlib import Path

from scripts.maintenance import project_review_issue_promoter as promoter


def test_example_packet_is_valid() -> None:
    assert promoter.validate_packet(promoter.example_packet()) == []


def test_trading_execution_findings_require_decision_needed() -> None:
    packet = promoter.example_packet()
    packet["scope"]["touches_trading_execution"] = True
    packet["routing"]["decision_needed"] = False

    errors = promoter.validate_packet(packet)

    assert "scope.touches_trading_execution=true requires routing.decision_needed=true" in errors


def test_dry_run_renders_issue_body(tmp_path: Path, capsys) -> None:
    packet_path = tmp_path / "finding.json"
    packet_path.write_text(json.dumps(promoter.example_packet()), encoding="utf-8")

    exit_code = promoter.main(["--packet", str(packet_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "gpt-trader-agent-finding-id: docs-link-audit-example" in captured.out
    assert "## Acceptance Criteria" in captured.out
    assert "agent-ready" in captured.out


def test_invalid_blocked_by_entry_is_reported_before_rendering(tmp_path: Path, capsys) -> None:
    packet = promoter.example_packet()
    packet["routing"]["blocked_by"] = [123]
    packet_path = tmp_path / "finding.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    exit_code = promoter.main(["--packet", str(packet_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "routing.blocked_by must contain only non-empty strings" in captured.err
    assert captured.out == ""


def test_blank_blocked_by_entry_is_reported() -> None:
    packet = promoter.example_packet()
    packet["routing"]["blocked_by"] = ["  "]

    errors = promoter.validate_packet(packet)

    assert "routing.blocked_by must contain only non-empty strings" in errors


def test_dry_run_renders_valid_blocked_by_entries(tmp_path: Path, capsys) -> None:
    packet = promoter.example_packet()
    packet["routing"]["blocked_by"] = ["upstream decision packet", "review receipt"]
    packet_path = tmp_path / "finding.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    exit_code = promoter.main(["--packet", str(packet_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "- Blocked by: upstream decision packet, review receipt" in captured.out


def test_invalid_candidate_type_is_reported() -> None:
    packet = promoter.example_packet()
    packet["routing"]["candidate_for"] = [{"not": "a string"}]

    errors = promoter.validate_packet(packet)

    assert "routing.candidate_for must contain only strings" in errors


def test_evidence_requires_command_path_or_url_anchor() -> None:
    packet = promoter.example_packet()
    del packet["evidence"][0]["command"]

    errors = promoter.validate_packet(packet)

    assert "evidence[1] must include at least one anchor: command, path, url" in errors


def test_decision_needed_packet_is_not_agent_ready() -> None:
    packet = promoter.example_packet()
    packet["routing"]["decision_needed"] = True
    packet["routing"]["candidate_for"] = ["decision"]
    packet["routing"]["blocked_by"] = ["live-start control decision"]

    labels = promoter.packet_labels(packet)

    assert "agent-ready" not in labels
    assert "decision-needed" in labels


def test_decision_candidate_requires_decision_needed() -> None:
    packet = promoter.example_packet()
    packet["routing"]["candidate_for"] = ["decision"]
    packet["routing"]["decision_needed"] = False

    errors = promoter.validate_packet(packet)

    assert "routing.candidate_for includes decision requires routing.decision_needed=true" in errors


def linear_snapshot(packet, issues=None):
    return {
        "team_id": promoter.LINEAR_TEAM_ID,
        "project_id": promoter.LINEAR_PROJECT_ID,
        "query": None,
        "state": None,
        "scope": "project_all_states",
        "include_archived": True,
        "has_next_page": False,
        "issues": issues or [],
    }


def linear_issue(packet, status="started", **overrides):
    return {
        "id": "RJ-999",
        "url": "https://linear.app/rj-openclaw/issue/RJ-999",
        "description": promoter.marker_for(packet["finding_id"]),
        "statusType": status,
        "archivedAt": None,
        "teamId": promoter.LINEAR_TEAM_ID,
        "projectId": promoter.LINEAR_PROJECT_ID,
        **overrides,
    }


def test_search_required_before_any_promotion() -> None:
    plan = promoter.promotion_plan(promoter.example_packet(), None)
    assert plan["action"] == "search_required"
    assert "request" not in plan
    assert plan["search"]["arguments"]["includeArchived"] is True
    assert "state" not in plan["search"]["arguments"]
    assert "query" not in plan["search"]["arguments"]


def test_empty_complete_search_plans_only_linear_backlog_create() -> None:
    packet = promoter.example_packet()
    plan = promoter.promotion_plan(packet, linear_snapshot(packet))
    assert plan["action"] == "create"
    assert plan["request"]["tool"] == "save_issue"
    args = plan["request"]["arguments"]
    assert args["team"] == promoter.LINEAR_TEAM_ID
    assert args["project"] == promoter.LINEAR_PROJECT_ID
    assert args["state"] == "Backlog"
    assert args["labels"] == ["work"]


def test_active_match_comments_without_changing_state_or_description() -> None:
    packet = promoter.example_packet()
    existing = linear_issue(packet)
    plan = promoter.promotion_plan(packet, linear_snapshot(packet, [existing]))
    assert plan["action"] == "comment_existing"
    assert plan["request"]["tool"] == "save_comment"
    assert plan["request"]["arguments"]["issueId"] == "RJ-999"
    assert set(plan["request"]["arguments"]) == {"issueId", "body"}


def test_terminal_and_archived_matches_never_create_or_reopen() -> None:
    packet = promoter.example_packet()
    for issue in [
        linear_issue(packet, "completed"),
        linear_issue(packet, "canceled"),
        linear_issue(packet, archivedAt="2026-10-25T00:00:00Z"),
    ]:
        plan = promoter.promotion_plan(packet, linear_snapshot(packet, [issue]))
        assert plan["action"] == "matched_terminal"
        assert "request" not in plan


def test_legacy_hidden_marker_matches_but_prefix_collision_does_not() -> None:
    packet = promoter.example_packet()
    marker = promoter.marker_for(packet["finding_id"])
    issue = linear_issue(packet, description=f"<!-- {marker} -->\nsummary")
    assert (
        promoter.promotion_plan(packet, linear_snapshot(packet, [issue]))["action"]
        == "comment_existing"
    )
    issue["description"] = marker + "-different"
    assert promoter.promotion_plan(packet, linear_snapshot(packet, [issue]))["action"] == "create"


def test_incomplete_or_wrong_destination_search_cannot_create() -> None:
    import pytest

    packet = promoter.example_packet()
    for change in [
        {"has_next_page": True},
        {"include_archived": False},
        {"project_id": "wrong"},
        {"team_id": "wrong"},
        {"query": "other"},
    ]:
        with pytest.raises(ValueError, match="complete all-state"):
            promoter.promotion_plan(packet, {**linear_snapshot(packet), **change})


def test_ambiguous_matches_and_unknown_state_fail_closed() -> None:
    import pytest

    packet = promoter.example_packet()
    issue = linear_issue(packet)
    with pytest.raises(ValueError, match="multiple exact"):
        promoter.promotion_plan(packet, linear_snapshot(packet, [issue, {**issue, "id": "RJ-998"}]))
    with pytest.raises(ValueError, match="unknown statusType"):
        promoter.promotion_plan(packet, linear_snapshot(packet, [linear_issue(packet, "unknown")]))
    with pytest.raises(ValueError, match="outside"):
        promoter.promotion_plan(packet, linear_snapshot(packet, [{**issue, "projectId": "other"}]))


def test_truncated_search_descriptions_require_hydration() -> None:
    import pytest

    packet = promoter.example_packet()
    issue = linear_issue(
        packet, description="preview (truncated, use `get_issue` for full description)"
    )
    with pytest.raises(ValueError, match="hydrate truncated"):
        promoter.promotion_plan(packet, linear_snapshot(packet, [issue]))


def test_exhausted_marker_search_cannot_certify_terminal_absence() -> None:
    import pytest

    packet = promoter.example_packet()
    # The native marker query omitted completed RJ-797 even with hasNextPage=false.
    # That old read must fail rather than returning a duplicate create request.
    snapshot = linear_snapshot(packet)
    snapshot["query"] = promoter.marker_for(packet["finding_id"])
    with pytest.raises(ValueError, match="without query or state filters"):
        promoter.promotion_plan(packet, snapshot)
    snapshot["query"] = None
    snapshot["state"] = "started"
    with pytest.raises(ValueError, match="without query or state filters"):
        promoter.promotion_plan(packet, snapshot)
