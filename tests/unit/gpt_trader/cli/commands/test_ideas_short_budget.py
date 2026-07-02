from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import TimeHorizon, TradeDirection
from tests.unit.gpt_trader.cli.commands.conftest import attest_ideas_root
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

EXPECTED_SHORT_BUDGET_VIOLATIONS = ["direction short requires risk budget allow_naked_shorts=true"]


def _future_horizon() -> TimeHorizon:
    return TimeHorizon(
        expected_hold="3-10 days",
        expires_at=datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
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
) -> dict[str, Any]:
    attest_ideas_root(root)
    path = _write_idea(root.parent / "short-idea.json", payload)
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


def test_short_preview_and_approval_follow_naked_short_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(
        decision_id="trade-20350612-short",
        direction=TradeDirection.SHORT,
    )

    propose_response = _propose(capsys, root, payload)

    assert propose_response["data"]["violations"] == EXPECTED_SHORT_BUDGET_VIOLATIONS
    assert any("allow_naked_shorts" in warning for warning in propose_response["warnings"])

    exit_code, approve_response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--reason",
            "Approve short",
        ],
    )
    assert exit_code == 1
    assert approve_response["errors"][0]["code"] == CliErrorCode.POLICY_VIOLATION.value
    assert approve_response["data"]["violations"] == EXPECTED_SHORT_BUDGET_VIOLATIONS

    exit_code, budget_response = _run_json(
        capsys,
        [
            "ideas",
            "budget",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--allow-naked-shorts",
            "false",
            "--reason",
            "Keep naked shorts disabled",
        ],
    )
    assert exit_code == 0
    assert budget_response["data"]["allow_naked_shorts"] is False

    exit_code, approve_response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--reason",
            "Approve short",
        ],
    )
    assert exit_code == 1
    assert approve_response["data"]["violations"] == EXPECTED_SHORT_BUDGET_VIOLATIONS

    exit_code, budget_response = _run_json(
        capsys,
        [
            "ideas",
            "budget",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--allow-naked-shorts",
            "true",
            "--reason",
            "Permit naked shorts for this review lane",
        ],
    )
    assert exit_code == 0
    assert budget_response["data"]["allow_naked_shorts"] is True

    exit_code, approve_response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--reason",
            "Naked short accepted",
        ],
    )
    assert exit_code == 0
    assert approve_response["data"]["state"] == "approved"
