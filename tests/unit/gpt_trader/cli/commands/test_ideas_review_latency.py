from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import TimeHorizon
from tests.support.trade_state_files import corrupt_state_file, read_state_file
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


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
    return response


def _backdate_proposed_event(root: Path, *, hours: int) -> None:
    audit_path = root / "audit.jsonl"
    lines = read_state_file(audit_path).splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    event["timestamp"] = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    corrupt_state_file(audit_path, json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def test_review_latency_budget_blocks_approval_and_sweep_for_far_future_idea(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-20350612-review-latency")
    _propose(capsys, root, payload)
    _backdate_proposed_event(root, hours=2)

    exit_code, budget_response = _run_json(
        capsys,
        [
            "ideas",
            "budget",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--max-review-latency-hours",
            "1",
            "--reason",
            "Tighten review queue",
        ],
    )
    assert exit_code == 0
    assert budget_response["data"]["max_review_latency_hours"] == 1

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
            "Review lagged but thesis still valid",
        ],
    )
    assert exit_code == 1
    assert approve_response["errors"][0]["code"] == CliErrorCode.POLICY_VIOLATION.value
    assert any(
        "review deadline expired" in violation
        for violation in approve_response["data"]["violations"]
    )

    exit_code, sweep_response = _run_json(
        capsys,
        [
            "ideas",
            "expire",
            *_root_args(root),
            "--actor",
            "rj",
            "--sweep",
        ],
    )

    assert exit_code == 0
    assert sweep_response["data"]["expired"] == [payload["decision_id"]]
