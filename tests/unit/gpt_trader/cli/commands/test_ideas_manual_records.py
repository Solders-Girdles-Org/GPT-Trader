from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.features.trade_ideas import TimeHorizon
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


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def _propose(capsys: pytest.CaptureFixture[str], root: Path, payload: dict[str, Any]) -> None:
    attest_ideas_root(root)
    path = _write_idea(root.parent / "idea.json", payload)
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


def test_mark_submitted_and_filled_record_manual_execution_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload()
    _propose(capsys, root, payload)
    _run_json(
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

    exit_code, submitted = _run_json(
        capsys,
        [
            "ideas",
            "mark-submitted",
            *_root_args(root),
            "--actor",
            "manual-recorder",
            payload["decision_id"],
            "--venue",
            "coinbase",
            "--external-order-id",
            "order-123",
        ],
    )
    assert exit_code == 0
    assert submitted["data"]["state"] == "submitted"

    exit_code, filled = _run_json(
        capsys,
        [
            "ideas",
            "mark-filled",
            *_root_args(root),
            "--actor",
            "coinbase",
            payload["decision_id"],
            "--venue",
            "coinbase",
            "--external-order-id",
            "order-123",
        ],
    )
    assert exit_code == 0
    assert filled["data"]["state"] == "filled"

    exit_code, audit = _run_json(capsys, ["ideas", "audit", "tail", *_root_args(root), "-n", "2"])
    assert exit_code == 0
    assert [event["action"] for event in audit["data"]["events"]] == ["submitted", "filled"]
    assert {event["external_order_id"] for event in audit["data"]["events"]} == {"order-123"}
