from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import TimeHorizon
from tests.support.trade_state_files import corrupt_state_file, read_state_file, state_file_exists
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


@pytest.mark.parametrize("field_name", ["amount", "percent_of_account"])
def test_propose_negative_max_loss_returns_invalid_argument_without_side_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field_name: str
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload()
    payload["max_loss"][field_name] = "-1"
    path = _write_idea(tmp_path / f"negative-{field_name}.json", payload)

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
    assert f"max_loss.{field_name} must be non-negative" in response["errors"][0]["message"]
    assert not (root / "records").exists()
    assert not state_file_exists(root / "audit.jsonl")
    assert not state_file_exists(root / "risk_budget.jsonl")


@pytest.mark.parametrize("field_name", ["amount", "percent_of_account"])
def test_approve_negative_max_loss_record_returns_validation_error_without_side_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field_name: str
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id=f"trade-20350612-negative-{field_name}")
    payload["max_loss"][field_name] = "-1"
    record_dir = root / "records" / payload["decision_id"]
    record_dir.mkdir(parents=True)
    latest_path = record_dir / "latest.json"
    corrupt_state_file(latest_path, json.dumps(payload, sort_keys=True))
    original_latest = read_state_file(latest_path)

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
            "Risk verified",
        ],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.VALIDATION_ERROR.value
    assert "requires validated migration" in response["errors"][0]["message"]
    assert read_state_file(latest_path) == original_latest
    assert not state_file_exists(root / "audit.jsonl")
    assert not state_file_exists(root / "risk_budget.jsonl")
