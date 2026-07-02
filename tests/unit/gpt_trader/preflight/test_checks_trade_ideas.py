from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import gpt_trader.preflight.checks.trade_ideas as trade_ideas_check_module
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    AuditEvent,
    BudgetLogEntry,
    RiskBudgetLog,
    TradeIdeaAuditLog,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaStore,
    new_event_id,
)
from gpt_trader.preflight.checks.trade_ideas import check_trade_ideas_readiness
from gpt_trader.preflight.core import PreflightCheck
from gpt_trader.preflight.validation_result import PreflightResultPayload
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea


def _failed_result(checker: PreflightCheck, message_fragment: str) -> PreflightResultPayload:
    return next(
        result
        for result in checker.results
        if result["status"] == "fail" and message_fragment in result["message"]
    )


def _seed_budget(ideas_root: Path) -> None:
    RiskBudgetLog(ideas_root / "risk_budget.jsonl").append(
        BudgetLogEntry(
            timestamp=datetime(2026, 6, 12, 9, 0, tzinfo=UTC),
            actor_type=ActorType.SYSTEM,
            actor_id="seed-defaults",
            budget=DEFAULT_RISK_BUDGET,
        )
    )


def _append_proposed_event(ideas_root: Path) -> None:
    idea = build_trade_idea()
    TradeIdeaService(ideas_root).propose(idea, actor_id="idea-generator-v1")


def test_trade_ideas_readiness_passes_with_seeded_budget_and_empty_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))
    monkeypatch.setenv("MOCK_BROKER", "1")
    monkeypatch.setenv("DRY_RUN", "1")

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is True
    assert any(str(ideas_root) in message for message in checker.successes)
    assert any("0 event(s)" in message for message in checker.successes)
    assert any("risk budget current" in message for message in checker.successes)
    assert not checker.errors


def test_trade_ideas_readiness_fails_on_corrupted_audit_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    (ideas_root / "audit.jsonl").write_text("{not json}\n", encoding="utf-8")
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    assert any("audit integrity failed" in message for message in checker.errors)


def test_trade_ideas_readiness_fails_on_non_utf8_audit_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    audit_path = ideas_root / "audit.jsonl"
    audit_path.write_bytes(b"\xff\xfe\x00")
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    result = _failed_result(checker, "audit unreadable")
    assert result["details"]["ideas_root"] == str(ideas_root)
    assert result["details"]["audit_path"] == str(audit_path)


@pytest.mark.parametrize(
    ("log_name", "message_fragment", "detail_key"),
    (
        ("audit.jsonl", "audit log is not appendable", "audit_path"),
        ("risk_budget.jsonl", "risk budget log is not appendable", "budget_path"),
    ),
)
def test_trade_ideas_readiness_fails_when_existing_log_is_not_appendable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_name: str,
    message_fragment: str,
    detail_key: str,
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    audit_path = ideas_root / "audit.jsonl"
    audit_path.write_text("", encoding="utf-8")
    readonly_path = ideas_root / log_name

    def fake_access(path: str | Path, mode: int) -> bool:
        if Path(path) == readonly_path and mode & trade_ideas_check_module.os.W_OK:
            return False
        return True

    monkeypatch.setattr(trade_ideas_check_module.os, "access", fake_access)
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    result = _failed_result(checker, message_fragment)
    assert result["details"]["ideas_root"] == str(ideas_root)
    assert result["details"][detail_key] == str(readonly_path)


def test_trade_ideas_readiness_fails_when_audit_record_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    idea = build_trade_idea(decision_id="trade-20260612-missing-record")
    TradeIdeaAuditLog(ideas_root / "audit.jsonl").append(
        AuditEvent(
            event_id=new_event_id(),
            timestamp=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
            decision_id=idea.decision_id,
            actor_type=ActorType.AI,
            actor_id="idea-generator-v1",
            action=AuditAction.PROPOSED,
            before_state=None,
            after_state=TradeIdeaState.PROPOSED,
            reason="test proposal without stored record",
            record_hash=idea.record_hash(),
        )
    )
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    result = _failed_result(checker, "audit record integrity failed")
    assert result["details"]["ideas_root"] == str(ideas_root)
    assert result["details"]["audit_path"] == str(ideas_root / "audit.jsonl")


def test_trade_ideas_readiness_fails_when_stored_record_has_no_audit_trail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    orphan = build_trade_idea(decision_id="trade-20260612-orphaned-record")
    TradeIdeaStore(ideas_root / "records").save(orphan)
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    result = _failed_result(checker, "records missing audit trail")
    assert "trade-20260612-orphaned-record" in result["message"]
    assert result["details"]["orphaned_decision_ids"] == "trade-20260612-orphaned-record"


def test_trade_ideas_readiness_fails_when_latest_record_is_tampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    service = TradeIdeaService(
        ideas_root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    idea = build_trade_idea(decision_id="trade-20260612-tampered-latest")
    service.propose(idea, actor_id="idea-generator-v1")
    # Replace latest.json with a different, unaudited (but valid) revision.
    tampered = build_trade_idea(
        decision_id=idea.decision_id,
        invalidation="Daily close below 59000",
    )
    TradeIdeaStore(ideas_root / "records").save(tampered)
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    result = _failed_result(checker, "latest record integrity failed")
    assert result["details"]["ideas_root"] == str(ideas_root)


def test_trade_ideas_readiness_fails_when_latest_record_is_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    service = TradeIdeaService(
        ideas_root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    idea = build_trade_idea(decision_id="trade-20260612-deleted-latest")
    service.propose(idea, actor_id="idea-generator-v1")
    (ideas_root / "records" / idea.decision_id / "latest.json").unlink()
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    result = _failed_result(checker, "latest record integrity failed")
    assert result["details"]["ideas_root"] == str(ideas_root)


def test_trade_ideas_readiness_fails_when_budget_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    ideas_root.mkdir()
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    assert any("risk budget not seeded" in message for message in checker.errors)


def test_trade_ideas_readiness_fails_on_malformed_budget_decimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    budget_path = ideas_root / "risk_budget.jsonl"
    budget_payload = budget_path.read_text(encoding="utf-8").replace(
        '"max_loss_per_idea_pct":"5"',
        '"max_loss_per_idea_pct":"bad"',
    )
    budget_path.write_text(budget_payload, encoding="utf-8")
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is False
    result = _failed_result(checker, "risk budget unreadable")
    assert result["details"]["ideas_root"] == str(ideas_root)
    assert result["details"]["budget_path"] == str(budget_path)


def test_trade_ideas_readiness_reports_pending_proposed_ideas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ideas_root = tmp_path / "trade_ideas"
    _seed_budget(ideas_root)
    _append_proposed_event(ideas_root)
    monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(ideas_root))

    checker = PreflightCheck(profile="dev")

    assert check_trade_ideas_readiness(checker) is True
    assert any("pending review: 1 proposed" in message for message in checker.warnings)
