from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import MaxLoss, TimeHorizon
from tests.unit.gpt_trader.cli.commands.conftest import attest_ideas_root
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _future_horizon() -> TimeHorizon:
    return TimeHorizon(
        expected_hold="3-10 days",
        expires_at=datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
    )


def _stale_horizon() -> TimeHorizon:
    return TimeHorizon(
        expected_hold="3-10 days",
        expires_at=datetime(2020, 6, 19, 16, 0, tzinfo=UTC),
    )


def _idea_payload(**overrides: Any) -> dict[str, Any]:
    fields = {"time_horizon": _future_horizon(), **overrides}
    return build_trade_idea(**fields).to_dict()


def _write_idea(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def _propose(
    capsys: pytest.CaptureFixture[str],
    root: Path,
    payload: dict[str, Any],
    *,
    filename: str = "idea.json",
) -> dict[str, Any]:
    attest_ideas_root(root)
    path = _write_idea(root.parent / filename, payload)
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
    return response


def test_propose_from_file_persists_record_and_warns_for_non_compliant_idea(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(
        max_loss=MaxLoss(amount=Decimal("900"), percent_of_account=Decimal("9")),
        time_horizon=_stale_horizon(),
    )

    response = _propose(capsys, root, payload)

    assert response["data"]["decision_id"] == payload["decision_id"]
    assert response["data"]["state"] == "proposed"
    assert response["data"]["record_hash"]
    assert any("exceeds budget cap" in warning for warning in response["warnings"])
    assert any("expired" in violation for violation in response["data"]["violations"])
    assert (root / "records" / payload["decision_id"] / "latest.json").exists()
    events = (root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 1


def test_propose_from_stdin_returns_json_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-20350612-stdin")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    exit_code, response = _run_json(
        capsys,
        ["ideas", "propose", *_root_args(root), "--actor", "idea-generator-v1", "--stdin"],
    )

    assert exit_code == 0
    assert response["command"] == "ideas propose"
    assert response["data"]["decision_id"] == "trade-20350612-stdin"
    assert response["errors"] == []


def test_propose_missing_required_field_returns_invalid_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload()
    payload.pop("thesis")
    path = _write_idea(tmp_path / "missing.json", payload)

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

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "thesis"


def test_propose_malformed_decimal_returns_invalid_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload()
    payload["max_loss"]["percent_of_account"] = "not-a-decimal"
    path = _write_idea(tmp_path / "bad-decimal.json", payload)

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

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert "Invalid trade idea field" in response["errors"][0]["message"]


def test_approve_happy_path_records_human_actor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload()
    _propose(capsys, root, payload)

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--reason",
            "Thesis and risk verified",
        ],
    )

    assert exit_code == 0
    assert response["data"]["state"] == "approved"

    exit_code, show_response = _run_json(
        capsys, ["ideas", "show", *_root_args(root), payload["decision_id"], "--events"]
    )
    assert exit_code == 0
    approve_event = show_response["data"]["events"][-1]
    assert approve_event["actor_type"] == "human"
    assert approve_event["actor_id"] == "rj"


def test_approve_policy_violation_returns_all_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(
        max_loss=MaxLoss(amount=Decimal("900"), percent_of_account=Decimal("9")),
        time_horizon=_stale_horizon(),
    )
    _propose(capsys, root, payload)

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--reason",
            "Approve anyway",
        ],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.POLICY_VIOLATION.value
    violations = response["data"]["violations"]
    assert len(violations) >= 2
    assert any("exceeds budget cap" in violation for violation in violations)
    assert any("expired" in violation for violation in violations)


def test_approve_rejects_absolute_decision_id_before_audit_or_budget_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    outside_dir = tmp_path / "outside-record"
    outside_dir.mkdir()
    payload = _idea_payload(decision_id="trade-20350612-outside-approve")
    (outside_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            str(outside_dir),
            "--reason",
            "Risk verified",
        ],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "decision_id"
    assert not (root / "audit.jsonl").exists()
    assert not (root / "risk_budget.jsonl").exists()


def test_request_changes_resubmit_and_approve_loop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload()
    _propose(capsys, root, payload)

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "request-changes",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--reason",
            "Tighten invalidation",
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "needs_changes"

    revised = build_trade_idea(
        time_horizon=_future_horizon(), invalidation="Daily close below 59000"
    )
    revised_payload = replace(revised, decision_id=payload["decision_id"]).to_dict()
    revised_path = _write_idea(tmp_path / "revised.json", revised_payload)
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "resubmit",
            *_root_args(root),
            "--actor",
            "idea-generator-v1",
            "--file",
            str(revised_path),
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "proposed"

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--reason",
            "Revision accepted",
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "approved"


def test_reject_cancel_expire_and_sweep_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    rejected = _idea_payload(decision_id="trade-20350612-reject")
    _propose(capsys, root, rejected, filename="reject.json")
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "reject",
            *_root_args(root),
            "--actor",
            "rj",
            rejected["decision_id"],
            "--reason",
            "Bad setup",
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "rejected"

    cancelled = _idea_payload(decision_id="trade-20350612-cancel")
    _propose(capsys, root, cancelled, filename="cancel.json")
    _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            cancelled["decision_id"],
            "--reason",
            "Risk verified",
        ],
    )
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "cancel",
            *_root_args(root),
            "--actor",
            "rj",
            cancelled["decision_id"],
            "--reason",
            "Setup changed",
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "cancelled"

    single_expired = _idea_payload(decision_id="trade-20350612-expire")
    _propose(capsys, root, single_expired, filename="expire.json")
    exit_code, response = _run_json(
        capsys,
        ["ideas", "expire", *_root_args(root), single_expired["decision_id"], "--reason", "Stale"],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "expired"

    stale = _idea_payload(decision_id="trade-20350612-stale", time_horizon=_stale_horizon())
    fresh = _idea_payload(decision_id="trade-20350612-fresh")
    _propose(capsys, root, stale, filename="stale.json")
    _propose(capsys, root, fresh, filename="fresh.json")
    exit_code, response = _run_json(capsys, ["ideas", "expire", *_root_args(root), "--sweep"])
    assert exit_code == 0
    assert response["data"]["expired"] == ["trade-20350612-stale"]

    exit_code, response = _run_json(capsys, ["ideas", "expire", *_root_args(root), "--sweep"])
    assert exit_code == 0
    assert response["metadata"]["was_noop"] is True
