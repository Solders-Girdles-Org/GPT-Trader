from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.features.trade_ideas import AuditAction, TimeHorizon
from gpt_trader.features.trade_ideas.service import TradeIdeaService
from tests.unit.gpt_trader.cli.commands.conftest import attest_account_equity
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _run_text(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, output


def _root_args(root: Path, *, output_format: str = "json") -> list[str]:
    return ["--ideas-root", str(root), "--format", output_format]


def _idea(decision_id: str, **overrides: Any) -> Any:
    return build_trade_idea(
        decision_id=decision_id,
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
        ),
        **overrides,
    )


def test_audit_export_pages_non_monotonic_timestamps_in_append_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    current_time = [datetime(2026, 6, 12, 10, 3, tzinfo=UTC)]
    service = TradeIdeaService(root, now_factory=lambda: current_time[0])
    attest_account_equity(service)
    idea = _idea("trade-audit-non-monotonic")
    service.propose(idea, actor_id="generator-a")
    current_time[0] = datetime(2026, 6, 12, 10, 1, tzinfo=UTC)
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    current_time[0] = datetime(2026, 6, 12, 10, 2, tzinfo=UTC)
    service.record_submission(idea.decision_id, actor_id="operator", venue="manual")
    current_time[0] = datetime(2026, 6, 12, 10, 4, tzinfo=UTC)
    service.record_fill(idea.decision_id, actor_id="operator", venue="manual")

    exit_code, output = _run_text(
        capsys,
        [
            "ideas",
            "audit",
            "export",
            *_root_args(root, output_format="csv"),
            "--decision-id",
            idea.decision_id,
            "--limit",
            "2",
            "--offset",
            "1",
        ],
    )

    assert exit_code == 0
    rows = list(csv.DictReader(io.StringIO(output)))
    assert [row["action"] for row in rows] == [
        AuditAction.APPROVED.value,
        AuditAction.SUBMITTED.value,
    ]
    assert [row["timestamp"] for row in rows] == [
        "2026-06-12T10:01:00+00:00",
        "2026-06-12T10:02:00+00:00",
    ]
