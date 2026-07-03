from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.features.trade_ideas import (
    CloseoutResolution,
    MaxLoss,
    TimeHorizon,
    TradeIdeaService,
)
from tests.unit.gpt_trader.cli.commands.conftest import attest_account_equity
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _future_horizon() -> TimeHorizon:
    return TimeHorizon(
        expected_hold="3-10 days",
        expires_at=datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
    )


def _idea(decision_id: str, **overrides: Any) -> Any:
    return build_trade_idea(
        decision_id=decision_id,
        time_horizon=_future_horizon(),
        **overrides,
    )


def _service(root: Path) -> TradeIdeaService:
    return TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _run_text(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, output


def _snapshot_files(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_report_empty_store_returns_zero_counts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(capsys, ["ideas", "report", *_root_args(root)])

    assert exit_code == 0
    assert response["success"] is True
    assert response["metadata"]["was_noop"] is True
    data = response["data"]
    assert data["proposal_volume"]["idea_count"] == 0
    assert data["proposal_volume"]["proposal_event_count"] == 0
    assert data["workflow"]["approval_rate_pct"] == "0.00"
    assert data["closeouts"]["terminal_count"] == 0
    assert data["closeouts"]["missing_closeout_count"] == 0


def test_report_empty_store_text_omits_monthly_section_and_stays_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"

    exit_code, output = _run_text(
        capsys,
        ["ideas", "report", "--ideas-root", str(root), "--format", "text"],
    )

    assert exit_code == 0
    assert "ideas report OK (0 ideas, approval_rate=0.00%, closeout_coverage=0.00%)" in output
    assert "Monthly" not in output
    assert _snapshot_files(root) == {}


def test_report_summarizes_workflow_quality_closeouts_and_profit_loss(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    service = _service(root)

    filled = _idea("trade-report-filled")
    service.propose(filled, actor_id="idea-generator-v1")
    attest_account_equity(service)
    service.approve(filled.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(filled.decision_id, actor_id="operator", venue="manual")
    service.record_fill(filled.decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        filled.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=Decimal("125.50"),
        realized_profit_loss_percent=Decimal("2.4"),
        evidence=("broker-statement:manual",),
    )

    rejected = _idea("trade-report-rejected")
    service.propose(rejected, actor_id="idea-generator-v1")
    service.request_changes(rejected.decision_id, actor_id="rj", reason="Need tighter risk")
    service.resubmit(
        _idea(rejected.decision_id, thesis="Revised BTC thesis with tighter invalidation"),
        actor_id="idea-generator-v1",
    )
    service.reject(rejected.decision_id, actor_id="rj", reason="Setup invalidated")
    service.record_closeout_attribution(
        rejected.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_unavailable_reason="Rejected before entry",
    )

    expired = _idea(
        "trade-report-expired",
        max_loss=MaxLoss(
            amount=Decimal("250"),
            percent_of_account=None,
            assumptions=("Missing percent for quality reporting",),
        ),
    )
    service.propose(expired, actor_id="idea-generator-v1")
    service.expire(expired.decision_id)

    exit_code, response = _run_json(capsys, ["ideas", "report", *_root_args(root)])

    assert exit_code == 0
    data = response["data"]
    assert data["proposal_volume"]["idea_count"] == 3
    assert data["proposal_volume"]["proposal_event_count"] == 4
    assert data["proposal_volume"]["resubmission_count"] == 1
    assert data["workflow"]["event_counts"] == {
        "approved": 1,
        "auto_approval_skipped": 0,
        "cancelled": 0,
        "expired": 1,
        "filled": 1,
        "proposed": 4,
        "rejected": 1,
        "requested_changes": 1,
        "submitted": 1,
    }
    assert data["workflow"]["current_state_counts"]["filled"] == 1
    assert data["workflow"]["current_state_counts"]["rejected"] == 1
    assert data["workflow"]["current_state_counts"]["expired"] == 1
    assert data["workflow"]["ever_approved_count"] == 1
    assert data["workflow"]["approval_rate_pct"] == "33.33"
    assert data["quality"]["missing_field_counts"]["max_loss.percent_of_account"] == 1
    assert data["quality"]["approval_ready_count"] == 2
    assert data["quality"]["confidence_counts"]["medium"] == 3
    assert data["closeouts"]["terminal_count"] == 3
    assert data["closeouts"]["with_closeout_count"] == 2
    assert data["closeouts"]["missing_closeout_count"] == 1
    assert data["closeouts"]["missing_closeout_decision_ids"] == ["trade-report-expired"]
    assert data["closeouts"]["resolution_counts"]["thesis_target"] == 1
    assert data["closeouts"]["resolution_counts"]["invalidation"] == 1
    assert data["closeouts"]["outcome_distribution"] == {
        "flat": 0,
        "loss": 0,
        "profit": 1,
        "unavailable": 1,
    }
    profit_loss = data["closeouts"]["realized_profit_loss"]
    assert profit_loss["total_amount"] == "125.50"
    assert profit_loss["average_amount"] == "125.50"
    assert profit_loss["max_loss_comparison"]["total_max_loss_amount"] == "250"
    assert profit_loss["max_loss_comparison"]["total_realized_to_max_loss_ratio"] == "0.5020"


def test_report_text_includes_monthly_trend_buckets_and_is_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    current_time = [datetime(2026, 5, 8, 12, 0, tzinfo=UTC)]
    service = TradeIdeaService(root, now_factory=lambda: current_time[0])

    filled = _idea("trade-report-may-filled")
    service.propose(filled, actor_id="idea-generator-v1")
    attest_account_equity(service)
    service.approve(filled.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(filled.decision_id, actor_id="operator", venue="manual")
    service.record_fill(filled.decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        filled.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=Decimal("125.50"),
        realized_profit_loss_percent=Decimal("2.4"),
        evidence=("broker-statement:manual",),
    )

    current_time[0] = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    rejected = _idea("trade-report-june-rejected")
    service.propose(rejected, actor_id="idea-generator-v1")
    service.reject(rejected.decision_id, actor_id="rj", reason="Setup invalidated")
    before = _snapshot_files(root)
    expected_june_line = (
        "2026-06: ideas=1, approval_rate=0.00%, " "closeout_coverage=0.00%, realized_profit_loss=0"
    )

    exit_code, output = _run_text(
        capsys,
        ["ideas", "report", "--ideas-root", str(root), "--format", "text"],
    )

    assert exit_code == 0
    assert "Monthly" in output
    assert (
        "2026-05: ideas=1, approval_rate=100.00%, "
        "closeout_coverage=100.00%, realized_profit_loss=125.50"
    ) in output
    assert expected_june_line in output
    assert _snapshot_files(root) == before


def test_report_missing_closeout_coverage_lists_terminal_ids(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    service = _service(root)
    expired = _idea("trade-report-no-closeout")
    service.propose(expired, actor_id="idea-generator-v1")
    service.expire(expired.decision_id)

    exit_code, response = _run_json(capsys, ["ideas", "report", *_root_args(root)])

    assert exit_code == 0
    closeouts = response["data"]["closeouts"]
    assert closeouts["terminal_count"] == 1
    assert closeouts["with_closeout_count"] == 0
    assert closeouts["missing_closeout_count"] == 1
    assert closeouts["coverage_rate_pct"] == "0.00"
    assert closeouts["missing_closeout_decision_ids"] == ["trade-report-no-closeout"]


def test_report_classifies_percent_only_closeout_outcomes_without_amount_totals(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    service = _service(root)
    attest_account_equity(service)

    for decision_id, realized_percent in (
        ("trade-report-percent-profit", Decimal("4.5")),
        ("trade-report-percent-loss", Decimal("-1.25")),
        ("trade-report-percent-flat", Decimal("0")),
    ):
        idea = _idea(decision_id)
        service.propose(idea, actor_id="idea-generator-v1")
        service.approve(decision_id, actor_id="rj", reason="Risk verified")
        service.record_submission(decision_id, actor_id="operator", venue="manual")
        service.record_fill(decision_id, actor_id="operator", venue="manual")
        service.record_closeout_attribution(
            decision_id,
            actor_id="rj",
            resolution=CloseoutResolution.THESIS_TARGET,
            realized_profit_loss_percent=realized_percent,
        )

    exit_code, response = _run_json(capsys, ["ideas", "report", *_root_args(root)])

    assert exit_code == 0
    closeouts = response["data"]["closeouts"]
    assert closeouts["outcome_distribution"] == {
        "flat": 1,
        "loss": 1,
        "profit": 1,
        "unavailable": 0,
    }
    profit_loss = closeouts["realized_profit_loss"]
    assert profit_loss["available_amount_count"] == 0
    assert profit_loss["total_amount"] == "0"
    assert profit_loss["average_amount"] is None
    assert profit_loss["max_loss_comparison"] == {
        "by_decision_id": [],
        "comparable_count": 0,
        "total_max_loss_amount": "0",
        "total_realized_amount": "0",
        "total_realized_to_max_loss_ratio": None,
    }


def test_report_is_read_only_and_does_not_seed_budget(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    service = _service(root)
    idea = _idea("trade-report-read-only")
    service.propose(idea, actor_id="idea-generator-v1")
    before = _snapshot_files(root)

    exit_code, response = _run_json(capsys, ["ideas", "report", *_root_args(root)])

    assert exit_code == 0
    assert response["success"] is True
    assert _snapshot_files(root) == before
    assert not (root / "risk_budget.jsonl").exists()
    assert response["data"]["proposal_volume"]["idea_count"] == 1
    assert response["data"]["workflow"]["current_state_counts"]["proposed"] == 1
