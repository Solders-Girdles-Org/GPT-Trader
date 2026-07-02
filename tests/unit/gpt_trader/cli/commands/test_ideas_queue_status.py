from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    RiskBudget,
    TimeHorizon,
    TradeIdeaService,
)
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def test_queue_status_empty_store_is_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(capsys, ["ideas", "queue-status", *_root_args(root)])

    assert exit_code == 0
    assert response["command"] == "ideas queue-status"
    assert response["metadata"]["was_noop"] is True
    assert response["data"]["counts"] == {
        "proposed": 0,
        "needs_changes": 0,
        "pending_total": 0,
        "upcoming_expirations": 0,
    }
    assert response["data"]["upcoming_expirations"] == []


def test_queue_status_reports_pending_counts_and_upcoming_expirations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    now = datetime.now(UTC)
    soon = build_trade_idea(
        decision_id="trade-20350612-soon",
        instrument="BTC-USD",
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=now + timedelta(hours=2),
        ),
    )
    change = build_trade_idea(
        decision_id="trade-20350612-change",
        instrument="ETH-USD",
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=now + timedelta(hours=4),
        ),
    )
    later = build_trade_idea(
        decision_id="trade-20350612-later",
        instrument="SOL-USD",
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=now + timedelta(days=3),
        ),
    )
    service = TradeIdeaService(root)
    service.propose(soon, actor_id="idea-generator-v1")
    service.propose(change, actor_id="idea-generator-v1")
    service.request_changes(change.decision_id, actor_id="rj", reason="Tighten risk")
    service.propose(later, actor_id="idea-generator-v1")

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "queue-status",
            *_root_args(root),
            "--warning-window-hours",
            "6",
        ],
    )

    assert exit_code == 0
    data = response["data"]
    assert data["counts"] == {
        "proposed": 2,
        "needs_changes": 1,
        "pending_total": 3,
        "upcoming_expirations": 2,
    }
    assert [entry["decision_id"] for entry in data["upcoming_expirations"]] == [
        "trade-20350612-soon",
        "trade-20350612-change",
    ]
    assert data["upcoming_expirations"][0]["state"] == "proposed"
    assert data["upcoming_expirations"][0]["deadline_type"] == "time_horizon"
    assert data["upcoming_expirations"][1]["state"] == "needs_changes"


def test_queue_status_reports_review_latency_deadline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    idea = build_trade_idea(
        decision_id="trade-20350612-review-latency",
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
        ),
    )
    service = TradeIdeaService(root)
    service.propose(idea, actor_id="idea-generator-v1")
    service.update_budget(
        RiskBudget.from_dict(
            {
                **DEFAULT_RISK_BUDGET.to_dict(),
                "version": 2,
                "max_review_latency_hours": 2,
            }
        ),
        actor_type=ActorType.HUMAN,
        actor_id="rj",
    )

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "queue-status",
            *_root_args(root),
            "--warning-window-hours",
            "3",
        ],
    )

    assert exit_code == 0
    expirations = response["data"]["upcoming_expirations"]
    assert len(expirations) == 1
    assert expirations[0]["decision_id"] == "trade-20350612-review-latency"
    assert expirations[0]["deadline_type"] == "review_latency"
