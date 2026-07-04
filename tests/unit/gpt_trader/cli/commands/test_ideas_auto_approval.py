"""CLI surface for the Stage 2 auto-approval sweep (`ideas approve --auto-sweep`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ACTOR_ID,
    AUTO_APPROVAL_ENV_VAR,
    AUTO_APPROVAL_REASON_PREFIX,
    MaxLoss,
    TimeHorizon,
)
from tests.unit.gpt_trader.cli.commands.conftest import attest_ideas_root
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _future_horizon() -> TimeHorizon:
    return TimeHorizon(
        expected_hold="3-10 days",
        expires_at=datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
    )


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
    *,
    decision_id: str,
    max_loss: MaxLoss | None = None,
) -> None:
    overrides: dict[str, Any] = {
        "decision_id": decision_id,
        "time_horizon": _future_horizon(),
    }
    if max_loss is not None:
        overrides["max_loss"] = max_loss
    payload = build_trade_idea(**overrides).to_dict()
    path = root.parent / f"{decision_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
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


def _enter_bounded_autonomy(capsys: pytest.CaptureFixture[str], root: Path) -> None:
    exit_code, _ = _run_json(
        capsys,
        [
            "ideas",
            "autonomy",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--mode",
            "bounded_autonomy",
            "--reason",
            "Test: enter bounded autonomy through the audited path",
        ],
    )
    assert exit_code == 0


def test_auto_sweep_refused_when_flag_off(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(AUTO_APPROVAL_ENV_VAR, raising=False)
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    _enter_bounded_autonomy(capsys, root)

    exit_code, response = _run_json(capsys, ["ideas", "approve", *_root_args(root), "--auto-sweep"])

    assert exit_code != 0
    assert response["errors"][0]["code"] == CliErrorCode.POLICY_VIOLATION.value
    assert any(AUTO_APPROVAL_ENV_VAR in violation for violation in response["data"]["violations"])


def test_auto_sweep_conflicts_with_decision_id_and_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    root = tmp_path / "ideas"

    exit_code, response = _run_json(
        capsys,
        ["ideas", "approve", *_root_args(root), "trade-1", "--auto-sweep"],
    )
    assert exit_code != 0
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value

    exit_code, response = _run_json(
        capsys,
        ["ideas", "approve", *_root_args(root), "--auto-sweep", "--reason", "manual"],
    )
    assert exit_code != 0
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value


def test_approve_without_decision_id_or_auto_sweep_is_missing_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(
        capsys, ["ideas", "approve", *_root_args(root), "--reason", "manual"]
    )

    assert exit_code != 0
    assert response["errors"][0]["code"] == CliErrorCode.MISSING_ARGUMENT.value


def test_auto_sweep_approves_in_budget_and_reports_skipped(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    _propose(capsys, root, decision_id="trade-cli-inbudget")
    _propose(
        capsys,
        root,
        decision_id="trade-cli-overcap",
        max_loss=MaxLoss(amount=Decimal("1800"), percent_of_account=Decimal("9")),
    )
    _enter_bounded_autonomy(capsys, root)

    exit_code, response = _run_json(capsys, ["ideas", "approve", *_root_args(root), "--auto-sweep"])

    assert exit_code == 0
    data = response["data"]
    assert data["counts"] == {"approved": 1, "skipped": 1}
    assert data["autonomy_mode"] == "bounded_autonomy"
    assert data["approved"][0]["decision_id"] == "trade-cli-inbudget"
    assert data["approved"][0]["state"] == "approved"
    skip = data["skipped"][0]
    assert skip["decision_id"] == "trade-cli-overcap"
    assert any("exceeds budget cap" in violation for violation in skip["violations"])

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "audit",
            "list",
            *_root_args(root),
            "--decision-id",
            "trade-cli-inbudget",
            "--action",
            "approved",
        ],
    )
    assert exit_code == 0
    events = response["data"]["events"]
    assert len(events) == 1
    assert events[0]["actor_type"] == "system"
    assert events[0]["actor_id"] == AUTO_APPROVAL_ACTOR_ID
    assert events[0]["reason"].startswith(AUTO_APPROVAL_REASON_PREFIX)
    assert events[0]["evidence"]


def test_auto_sweep_refused_below_bounded_autonomy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    _propose(capsys, root, decision_id="trade-cli-mode")

    exit_code, response = _run_json(capsys, ["ideas", "approve", *_root_args(root), "--auto-sweep"])

    assert exit_code != 0
    assert response["errors"][0]["code"] == CliErrorCode.POLICY_VIOLATION.value
    assert any(
        "requires audited autonomy mode" in violation
        for violation in response["data"]["violations"]
    )


def test_auto_sweep_over_empty_queue_is_noop_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTO_APPROVAL_ENV_VAR, "1")
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    _enter_bounded_autonomy(capsys, root)

    exit_code, response = _run_json(capsys, ["ideas", "approve", *_root_args(root), "--auto-sweep"])

    assert exit_code == 0
    assert response["data"]["counts"] == {"approved": 0, "skipped": 0}
