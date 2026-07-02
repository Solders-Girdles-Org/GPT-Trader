from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import TimeHorizon, canonical_ticket_json
from tests.unit.gpt_trader.cli.commands.conftest import attest_ideas_root
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _future_horizon() -> TimeHorizon:
    return TimeHorizon(
        expected_hold="3-10 days",
        expires_at=datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
    )


def _idea_payload(**overrides: Any) -> dict[str, Any]:
    return build_trade_idea(time_horizon=_future_horizon(), **overrides).to_dict()


def _write_idea(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    return exit_code, output


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code, output = _run(capsys, argv)
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def _assert_ticket_hash_matches_canonical_payload(ticket: dict[str, Any]) -> None:
    ticket_without_hash = dict(ticket)
    ticket_hash = ticket_without_hash.pop("ticket_hash")
    expected_hash = hashlib.sha256(
        canonical_ticket_json(ticket_without_hash).encode("utf-8")
    ).hexdigest()
    assert ticket_hash == expected_hash


def _stable_ticket_payload(ticket: dict[str, Any]) -> dict[str, Any]:
    stable = json.loads(json.dumps(ticket))
    stable.pop("ticket_hash")
    stable["policy_budget_snapshot"]["evaluated_at"] = "<export-evaluation-time>"
    return stable


def _propose_and_approve(
    capsys: pytest.CaptureFixture[str],
    root: Path,
    payload: dict[str, Any],
) -> None:
    attest_ideas_root(root)
    path = _write_idea(root.parent / f"{payload['decision_id']}.json", payload)
    exit_code, proposed = _run_json(
        capsys,
        [
            "ideas",
            "propose",
            *_root_args(root),
            "--actor",
            "idea-generator-v1",
            "--file",
            str(path),
        ],
    )
    assert exit_code == 0
    assert proposed["success"] is True

    exit_code, approved = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--reason",
            "Risk verified",
        ],
    )
    assert exit_code == 0
    assert approved["data"]["state"] == "approved"


def _export_args(root: Path, decision_id: str) -> list[str]:
    return [
        "ideas",
        "export-ticket",
        "--ideas-root",
        str(root),
        "--decision-id",
        decision_id,
        "--venue",
        "coinbase",
        "--venue-order-type",
        "limit",
        "--time-in-force",
        "GTC",
    ]


def test_export_ticket_defaults_to_raw_json_for_approved_idea(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-export-approved")
    _propose_and_approve(capsys, root, payload)

    exit_code, output = _run(capsys, _export_args(root, payload["decision_id"]))

    assert exit_code == 0
    assert output.endswith("}\n")
    assert not output.endswith("\n\n")
    ticket = json.loads(output)
    assert "success" not in ticket
    assert ticket["schema_version"] == "gpt-trader.trade_idea_ticket.v1"
    assert ticket["decision_metadata"]["instrument"] == "BTC-USD"
    assert ticket["risk_sizing_snapshot"]["max_loss"]["percent_of_account"] == "1.5"
    assert ticket["timing_invalidation_constraints"]["invalidation"] == ("Daily close below 58000")
    assert ticket["venue_request"] == {
        "venue": "coinbase",
        "venue_order_type": "limit",
        "time_in_force": "GTC",
        "client_order_id": ticket["venue_request"]["client_order_id"],
    }
    assert ticket["record_hash"]
    assert ticket["ticket_hash"]


def test_export_ticket_rejects_unapproved_idea_with_json_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-export-proposed")
    path = _write_idea(tmp_path / "proposed.json", payload)
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "propose",
            *_root_args(root),
            "--actor",
            "idea-generator-v1",
            "--file",
            str(path),
        ],
    )
    assert exit_code == 0
    assert response["success"] is True

    exit_code, response = _run_json(capsys, _export_args(root, payload["decision_id"]))

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.VALIDATION_ERROR.value
    assert response["errors"][0]["details"] == {
        "field": "after_state",
        "value": "proposed",
    }


def test_export_ticket_error_does_not_write_to_out_artifact_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    output_path = tmp_path / "ticket.json"
    output_path.write_text("keep-existing-ticket", encoding="utf-8")
    payload = _idea_payload(decision_id="trade-export-error-file")
    path = _write_idea(tmp_path / "proposed.json", payload)
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "propose",
            *_root_args(root),
            "--actor",
            "idea-generator-v1",
            "--file",
            str(path),
        ],
    )
    assert exit_code == 0
    assert response["success"] is True

    exit_code, response = _run_json(
        capsys,
        [*_export_args(root, payload["decision_id"]), "--out", str(output_path)],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.VALIDATION_ERROR.value
    assert output_path.read_text(encoding="utf-8") == "keep-existing-ticket"


def test_export_ticket_stdout_json_is_stable_except_evaluation_time(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-export-deterministic")
    _propose_and_approve(capsys, root, payload)

    exit_code, first = _run(capsys, _export_args(root, payload["decision_id"]))
    assert exit_code == 0
    exit_code, second = _run(capsys, _export_args(root, payload["decision_id"]))
    assert exit_code == 0

    first_ticket = json.loads(first)
    second_ticket = json.loads(second)
    _assert_ticket_hash_matches_canonical_payload(first_ticket)
    _assert_ticket_hash_matches_canonical_payload(second_ticket)
    assert _stable_ticket_payload(first_ticket) == _stable_ticket_payload(second_ticket)


def test_export_ticket_writes_raw_json_to_out_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    output_path = tmp_path / "tickets" / "trade-ticket.json"
    payload = _idea_payload(decision_id="trade-export-file")
    _propose_and_approve(capsys, root, payload)

    exit_code, output = _run(
        capsys,
        [*_export_args(root, payload["decision_id"]), "--out", str(output_path)],
    )

    assert exit_code == 0
    assert output == ""
    ticket = json.loads(output_path.read_text(encoding="utf-8"))
    assert ticket["decision_id"] == payload["decision_id"]
    assert ticket["venue_request"]["venue"] == "coinbase"


def test_export_ticket_text_out_path_confirms_written_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    output_path = tmp_path / "tickets" / "trade-ticket.json"
    payload = _idea_payload(decision_id="trade-export-text-file")
    _propose_and_approve(capsys, root, payload)

    exit_code, output = _run(
        capsys,
        [
            *_export_args(root, payload["decision_id"]),
            "--format",
            "text",
            "--out",
            str(output_path),
        ],
    )

    assert exit_code == 0
    assert "ideas export-ticket OK" in output
    assert f"written_to: {output_path}" in output
    ticket = json.loads(output_path.read_text(encoding="utf-8"))
    assert ticket["decision_id"] == payload["decision_id"]


def test_export_ticket_out_path_write_failure_returns_operation_failed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    output_path = tmp_path / "ticket-output-directory"
    output_path.mkdir()
    payload = _idea_payload(decision_id="trade-export-write-failure")
    _propose_and_approve(capsys, root, payload)

    exit_code, response = _run_json(
        capsys,
        [*_export_args(root, payload["decision_id"]), "--out", str(output_path)],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.OPERATION_FAILED.value
    assert "Could not write ticket artifact" in response["errors"][0]["message"]
    assert response["errors"][0]["details"]["path"] == str(output_path)
