from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
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
    path = _write_idea(root.parent / f"{payload['decision_id']}.json", payload)
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


def _approve(capsys: pytest.CaptureFixture[str], root: Path, decision_id: str) -> None:
    attest_ideas_root(root)
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            decision_id,
            "--reason",
            "Thesis and risk verified",
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "approved"


def _expire(capsys: pytest.CaptureFixture[str], root: Path, decision_id: str) -> None:
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "expire",
            *_root_args(root),
            "--actor",
            "expiry-sweep",
            decision_id,
            "--reason",
            "Idea passed review deadline",
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "expired"


def _mark_filled(capsys: pytest.CaptureFixture[str], root: Path, decision_id: str) -> None:
    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "mark-submitted",
            *_root_args(root),
            "--actor",
            "manual-recorder",
            decision_id,
            "--venue",
            "manual",
            "--external-order-id",
            "order-123",
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "submitted"

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "mark-filled",
            *_root_args(root),
            "--actor",
            "manual-recorder",
            decision_id,
            "--venue",
            "manual",
            "--external-order-id",
            "order-123",
        ],
    )
    assert exit_code == 0
    assert response["data"]["state"] == "filled"


def test_closeout_record_and_show_for_filled_idea(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-closeout-filled")
    _propose(capsys, root, payload)
    _approve(capsys, root, payload["decision_id"])
    _mark_filled(capsys, root, payload["decision_id"])

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "closeout",
            "record",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--resolution",
            "thesis_target",
            "--realized-profit-loss-amount",
            "125.50",
            "--realized-profit-loss-percent",
            "2.4",
            "--evidence",
            "statement:order-123",
            "--evidence",
            "chart:target-hit",
        ],
    )

    assert exit_code == 0
    assert response["command"] == "ideas closeout record"
    closeout = response["data"]["closeout_attribution"]
    assert closeout["decision_id"] == payload["decision_id"]
    assert closeout["actor_type"] == "human"
    assert closeout["actor_id"] == "rj"
    assert closeout["resolution"] == "thesis_target"
    assert closeout["realized_profit_loss_amount"] == "125.50"
    assert closeout["realized_profit_loss_percent"] == "2.4"
    assert closeout["realized_profit_loss_unavailable_reason"] == ""
    assert closeout["max_loss"] == payload["max_loss"]
    assert closeout["evidence"] == ["statement:order-123", "chart:target-hit"]
    assert (root / "closeout_attributions.jsonl").exists()

    exit_code, show_response = _run_json(
        capsys,
        [
            "ideas",
            "closeout",
            "show",
            *_root_args(root),
            payload["decision_id"],
        ],
    )

    assert exit_code == 0
    assert show_response["command"] == "ideas closeout show"
    assert show_response["data"]["closeout_attribution"] == closeout


def test_closeout_record_accepts_unavailable_profit_loss_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-closeout-expired")
    _propose(capsys, root, payload)
    _expire(capsys, root, payload["decision_id"])

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "closeout",
            "record",
            *_root_args(root),
            "--actor",
            "expiry-sweep",
            "--actor-type",
            "system",
            payload["decision_id"],
            "--resolution",
            "expiry",
            "--realized-profit-loss-unavailable-reason",
            "Expired before any entry fill",
            "--evidence",
            "expiry-sweep:2035-06-19",
        ],
    )

    assert exit_code == 0
    closeout = response["data"]["closeout_attribution"]
    assert closeout["actor_type"] == "system"
    assert closeout["resolution"] == "expiry"
    assert closeout["realized_profit_loss_amount"] is None
    assert closeout["realized_profit_loss_percent"] is None
    assert closeout["realized_profit_loss_unavailable_reason"] == "Expired before any entry fill"
    assert closeout["evidence"] == ["expiry-sweep:2035-06-19"]


def test_closeout_record_rejects_non_terminal_idea(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-closeout-proposed")
    _propose(capsys, root, payload)

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "closeout",
            "record",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--resolution",
            "invalidation",
            "--realized-profit-loss-amount",
            "-250",
        ],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.VALIDATION_ERROR.value
    assert "must be terminal" in response["errors"][0]["message"]
    assert not (root / "closeout_attributions.jsonl").exists()


def test_closeout_record_requires_profit_loss_or_unavailable_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-closeout-missing-profit-loss")
    _propose(capsys, root, payload)
    _expire(capsys, root, payload["decision_id"])

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "closeout",
            "record",
            *_root_args(root),
            "--actor",
            "rj",
            payload["decision_id"],
            "--resolution",
            "expiry",
        ],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.MISSING_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "realized_profit_loss"
    assert not (root / "closeout_attributions.jsonl").exists()


def test_closeout_show_without_attribution_is_successful_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-closeout-missing")
    _propose(capsys, root, payload)
    _expire(capsys, root, payload["decision_id"])

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "closeout",
            "show",
            *_root_args(root),
            payload["decision_id"],
        ],
    )

    assert exit_code == 0
    assert response["success"] is True
    assert response["metadata"]["was_noop"] is True
    assert response["data"] == {
        "decision_id": payload["decision_id"],
        "closeout_attribution": None,
    }
