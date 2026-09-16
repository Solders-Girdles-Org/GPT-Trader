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
