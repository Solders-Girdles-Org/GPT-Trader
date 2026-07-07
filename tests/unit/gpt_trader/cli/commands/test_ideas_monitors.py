"""CLI surface for the continuous portfolio monitors (#1192).

`ideas monitors` must read the same service library call as the console, so
these tests drive the real CLI entry point over a trail built through the
service and assert the snapshot payload and breach summary line.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.features.trade_ideas import CloseoutResolution, TradeIdeaService
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def _service(root: Path) -> TradeIdeaService:
    return TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def _close_with_loss(service: TradeIdeaService, decision_id: str, amount: str) -> None:
    idea = build_trade_idea(decision_id=decision_id)
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(decision_id, actor_id="operator", venue="manual")
    service.record_fill(decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_amount=Decimal(amount),
    )


def test_monitors_on_fresh_root_reads_unknown_without_seeding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(capsys, ["ideas", "monitors", *_root_args(root)])

    assert exit_code == 0
    data = response["data"]
    assert data["current_equity"] is None
    assert data["high_water_mark"] is None
    assert data["drawdown_breached"] is None
    assert data["daily_loss_breached"] is False
    # Render-only read: the budget and autonomy logs must not be seeded.
    assert not (root / "risk_budget.jsonl").exists()
    assert not (root / "autonomy_state.jsonl").exists()


def test_monitors_reports_drawdown_breach_from_the_trail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    service = _service(root)
    attest_account_equity(service)  # 20000

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "budget",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--max-drawdown-from-peak-pct",
            "2",
            "--reason",
            "Configure the drawdown-from-peak appetite",
        ],
    )
    assert exit_code == 0
    assert response["data"]["max_drawdown_from_peak_pct"] == "2"

    _close_with_loss(service, "trade-20260612-dd", "-600")

    exit_code, response = _run_json(capsys, ["ideas", "monitors", *_root_args(root)])

    assert exit_code == 0
    data = response["data"]
    assert data["high_water_mark"] == "20000"
    assert data["current_equity"] == "19400"
    assert data["drawdown_from_peak_pct"] == "3.00"
    assert data["max_drawdown_from_peak_pct"] == "2"
    assert data["drawdown_breached"] is True

    exit_code = cli.main(["ideas", "monitors", "--ideas-root", str(root), "--format", "text"])
    text = capsys.readouterr().out
    assert exit_code == 0
    assert "breaches=drawdown_from_peak" in text
