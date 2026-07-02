from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import (
    Confidence,
    ConfidenceLabel,
    MaxLoss,
    TimeHorizon,
    TradeDirection,
    TradeIdeaService,
    TradeIdeaStore,
)
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


def _propose(
    capsys: pytest.CaptureFixture[str], root: Path, payload: dict[str, Any], filename: str
) -> None:
    attest_ideas_root(root)
    path = _write_idea(root.parent / filename, payload)
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


def test_list_empty_store_and_state_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(capsys, ["ideas", "list", *_root_args(root)])
    assert exit_code == 0
    assert response["data"]["ideas"] == []
    assert response["metadata"]["was_noop"] is True

    first = _idea_payload(decision_id="trade-20350612-proposed")
    second = _idea_payload(decision_id="trade-20350612-approved")
    _propose(capsys, root, first, "first.json")
    _propose(capsys, root, second, "second.json")
    _run_json(
        capsys,
        [
            "ideas",
            "approve",
            *_root_args(root),
            "--actor",
            "rj",
            second["decision_id"],
            "--reason",
            "Risk verified",
        ],
    )

    exit_code, response = _run_json(
        capsys, ["ideas", "list", *_root_args(root), "--state", "proposed"]
    )
    assert exit_code == 0
    assert [idea["decision_id"] for idea in response["data"]["ideas"]] == [
        "trade-20350612-proposed"
    ]


def test_list_advanced_filters_sort_and_pagination_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    low_loss = _idea_payload(
        decision_id="trade-20350612-btc-low",
        instrument="BTC-USD",
        confidence=Confidence(label=ConfidenceLabel.MEDIUM, rationale="Constructive setup"),
        max_loss=MaxLoss(amount=Decimal("150"), percent_of_account=Decimal("1")),
    )
    high_loss = _idea_payload(
        decision_id="trade-20350612-btc-high",
        instrument="BTC-USD",
        confidence=Confidence(label=ConfidenceLabel.HIGH, rationale="Strong confirmation"),
        max_loss=MaxLoss(amount=Decimal("400"), percent_of_account=Decimal("4")),
    )
    other = _idea_payload(
        decision_id="trade-20350612-eth-short",
        instrument="ETH-USD",
        direction=TradeDirection.SHORT,
        confidence=Confidence(label=ConfidenceLabel.LOW, rationale="Weak confirmation"),
        max_loss=MaxLoss(amount=Decimal("100"), percent_of_account=Decimal("0.5")),
    )
    _propose(capsys, root, low_loss, "low.json")
    _propose(capsys, root, high_loss, "high.json")
    _propose(capsys, root, other, "other.json")

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "list",
            *_root_args(root),
            "--instrument",
            "btc-usd",
            "--direction",
            "long",
            "--min-confidence",
            "medium",
            "--sort-by",
            "max_loss_pct",
            "--descending",
            "--limit",
            "1",
            "--offset",
            "0",
        ],
    )

    assert exit_code == 0
    assert [idea["decision_id"] for idea in response["data"]["ideas"]] == [
        "trade-20350612-btc-high"
    ]
    assert response["data"]["total_count"] == 2
    assert response["data"]["returned_count"] == 1
    assert response["data"]["offset"] == 0
    assert response["data"]["limit"] == 1
    assert response["data"]["has_more"] is True


def test_list_rejects_inverted_confidence_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "list",
            *_root_args(root),
            "--min-confidence",
            "high",
            "--max-confidence",
            "low",
        ],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.VALIDATION_ERROR.value
    assert response["errors"][0]["details"]["field"] == "confidence"


def test_list_default_text_output_keeps_table_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload(decision_id="trade-20350612-default-text")
    _propose(capsys, root, payload, "default-text.json")

    exit_code = cli.main(["ideas", "list", "--ideas-root", str(root)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "DECISION_ID  STATE  INSTRUMENT  DIRECTION  MAX_LOSS%  EXPIRES_AT" in output
    assert "trade-20350612-default-text  proposed  BTC-USD  long  1.5" in output
    assert "total_count" not in output


def test_show_unknown_id_and_show_with_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(capsys, ["ideas", "show", *_root_args(root), "missing"])
    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.IDEA_NOT_FOUND.value

    payload = _idea_payload()
    _propose(capsys, root, payload, "show.json")
    exit_code, response = _run_json(
        capsys, ["ideas", "show", *_root_args(root), payload["decision_id"], "--events"]
    )
    assert exit_code == 0
    assert response["data"]["decision_id"] == payload["decision_id"]
    assert response["data"]["state"] == "proposed"
    assert response["data"]["events"][0]["action"] == "proposed"


def test_show_rejects_absolute_decision_id_before_store_lookup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    outside_dir = tmp_path / "outside-record"
    outside_dir.mkdir()
    payload = _idea_payload(decision_id="trade-20350612-outside")
    (outside_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    exit_code, response = _run_json(
        capsys,
        ["ideas", "show", *_root_args(root), str(outside_dir)],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "decision_id"


def test_list_and_show_reject_orphaned_record_without_audit_trail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    idea = build_trade_idea(
        decision_id="trade-20350612-orphaned",
        time_horizon=_future_horizon(),
    )
    TradeIdeaStore(root / "records").save(idea)

    exit_code, response = _run_json(capsys, ["ideas", "list", *_root_args(root)])
    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.OPERATION_FAILED.value
    assert "has no audit trail" in response["errors"][0]["message"]

    exit_code, response = _run_json(capsys, ["ideas", "show", *_root_args(root), idea.decision_id])
    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.OPERATION_FAILED.value
    assert "has no audit trail" in response["errors"][0]["message"]


def test_list_show_and_reject_refuse_unaudited_interrupted_resubmit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    idea = build_trade_idea(
        decision_id="trade-20350612-interrupted-resubmit",
        time_horizon=_future_horizon(),
    )
    service = TradeIdeaService(root)
    service.propose(idea, actor_id="idea-generator-v1")
    service.request_changes(idea.decision_id, actor_id="rj", reason="Need tighter risk")
    audit_path = root / "audit.jsonl"
    original_audit = audit_path.read_text(encoding="utf-8")
    unaudited_revision = build_trade_idea(
        decision_id=idea.decision_id,
        invalidation="Daily close below 59000",
        time_horizon=_future_horizon(),
    )
    TradeIdeaStore(root / "records").save(unaudited_revision)

    for argv in (
        ["ideas", "list", *_root_args(root)],
        ["ideas", "show", *_root_args(root), idea.decision_id],
        [
            "ideas",
            "reject",
            *_root_args(root),
            "--actor",
            "rj",
            idea.decision_id,
            "--reason",
            "Reject stale revision",
        ],
    ):
        exit_code, response = _run_json(capsys, argv)
        assert exit_code == 1
        assert response["errors"][0]["code"] == CliErrorCode.OPERATION_FAILED.value
        assert "does not match latest audit record_hash" in response["errors"][0]["message"]

    assert audit_path.read_text(encoding="utf-8") == original_audit


def test_audit_verify_ok_and_tampered_line_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    payload = _idea_payload()
    _propose(capsys, root, payload, "audit.json")

    exit_code, response = _run_json(capsys, ["ideas", "audit", "verify", *_root_args(root)])
    assert exit_code == 0
    assert response["data"]["event_count"] == 1

    with (root / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")

    exit_code, response = _run_json(capsys, ["ideas", "audit", "verify", *_root_args(root)])
    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.OPERATION_FAILED.value


def test_help_lists_ideas_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["ideas", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "propose" in output
    assert "request-changes" in output
    assert "report" in output
    assert "export-ticket" in output
    assert "replay" in output
    assert "closeout" in output
    assert "mark-submitted" in output
    assert "budget" in output
    assert "audit" in output
