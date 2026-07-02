"""CLI tests for ``ideas execute-paper``: the machine leg of the paper lane.

The executor library's contract is pinned in
tests/unit/gpt_trader/features/idea_execution/test_executor.py; these tests
cover the thin CLI adapter: envelope shape, actor stamping, --mark plumbing,
and error mapping for refused executions.
"""

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

_DECISION_ID = "trade-20260702-paper-001"


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def _approved_idea(capsys: pytest.CaptureFixture[str], root: Path) -> None:
    attest_ideas_root(root)
    payload = build_trade_idea(
        decision_id=_DECISION_ID,
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=datetime(2036, 6, 19, 16, 0, tzinfo=UTC),
        ),
    ).to_dict()
    idea_path = root.parent / "idea.json"
    idea_path.write_text(json.dumps(payload), encoding="utf-8")
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "propose",
            *_root_args(root),
            "--actor",
            "idea-generator-v1",
            "--file",
            str(idea_path),
        ],
    )
    assert exit_code == 0 and response["success"] is True
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            _DECISION_ID,
            *_root_args(root),
            "--actor",
            "human-reviewer",
            "--reason",
            "test approval",
        ],
    )
    assert exit_code == 0 and response["success"] is True


def test_execute_paper_fills_approved_idea(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "ideas"
    _approved_idea(capsys, root)

    exit_code, response = _run_json(
        capsys,
        ["ideas", "execute-paper", _DECISION_ID, *_root_args(root), "--mark", "60750"],
    )

    assert exit_code == 0
    assert response["success"] is True
    data = response["data"]
    assert data["final_state"] == "filled"
    assert data["client_order_id"] == _DECISION_ID
    assert data["fill_price"] == "60750"
    assert data["reconciliation"]["recorded_fill"] is True

    exit_code, audit = _run_json(capsys, ["ideas", "audit", "verify", *_root_args(root)])
    assert exit_code == 0
    assert audit["success"] is True


def test_execute_paper_stamps_default_system_actor(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "ideas"
    _approved_idea(capsys, root)
    exit_code, _ = _run_json(capsys, ["ideas", "execute-paper", _DECISION_ID, *_root_args(root)])
    assert exit_code == 0

    exit_code, listed = _run_json(
        capsys,
        ["ideas", "audit", "list", *_root_args(root), "--decision-id", _DECISION_ID],
    )
    assert exit_code == 0
    events = listed["data"]["events"]
    submitted = [event for event in events if event["action"] == "submitted"]
    assert len(submitted) == 1
    assert submitted[0]["actor_id"] == "paper-idea-executor"
    assert submitted[0]["actor_type"] == "system"
    assert submitted[0]["venue"] == "paper"


def test_execute_paper_refuses_unapproved_idea(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "ideas"
    attest_ideas_root(root)
    payload = build_trade_idea(
        decision_id=_DECISION_ID,
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=datetime(2036, 6, 19, 16, 0, tzinfo=UTC),
        ),
    ).to_dict()
    idea_path = root.parent / "idea.json"
    idea_path.write_text(json.dumps(payload), encoding="utf-8")
    exit_code, _ = _run_json(
        capsys,
        [
            "ideas",
            "propose",
            *_root_args(root),
            "--actor",
            "idea-generator-v1",
            "--file",
            str(idea_path),
        ],
    )
    assert exit_code == 0

    exit_code, response = _run_json(
        capsys, ["ideas", "execute-paper", _DECISION_ID, *_root_args(root)]
    )
    assert exit_code != 0
    assert response["success"] is False
    assert "state is proposed" in response["errors"][0]["message"]


def test_execute_paper_refuses_double_execution(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "ideas"
    _approved_idea(capsys, root)
    exit_code, _ = _run_json(capsys, ["ideas", "execute-paper", _DECISION_ID, *_root_args(root)])
    assert exit_code == 0

    exit_code, response = _run_json(
        capsys, ["ideas", "execute-paper", _DECISION_ID, *_root_args(root)]
    )
    assert exit_code != 0
    assert response["success"] is False
    assert "state is filled" in response["errors"][0]["message"]
