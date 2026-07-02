from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas import TradeIdeaService, TradeIdeaState


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(built)
    return built


def test_manual_venue_submission_and_fill_remain_supported(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Thesis and risk verified")
    service.record_submission(idea.decision_id, actor_id="operator", venue="manual")

    view = service.record_fill(idea.decision_id, actor_id="operator", venue="manual")

    assert view.state is TradeIdeaState.FILLED
    assert [event.venue for event in view.events[-2:]] == ["manual", "manual"]


def test_record_submission_rejects_unsupported_venue_before_audit_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "trade_ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(service)
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Thesis and risk verified")
    audit_path = root / "audit.jsonl"
    original_audit = audit_path.read_text(encoding="utf-8")

    with pytest.raises(ValidationError, match="Unsupported trade-idea venue") as exc_info:
        service.record_submission(idea.decision_id, actor_id="executor", venue="robinhood")

    assert exc_info.value.context["field"] == "venue"
    assert exc_info.value.context["value"] == "robinhood"
    assert audit_path.read_text(encoding="utf-8") == original_audit
    assert service.get(idea.decision_id).state is TradeIdeaState.APPROVED


def test_record_fill_rejects_unsupported_venue_before_audit_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "trade_ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(service)
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Thesis and risk verified")
    service.record_submission(idea.decision_id, actor_id="executor", venue="coinbase")
    audit_path = root / "audit.jsonl"
    original_audit = audit_path.read_text(encoding="utf-8")

    with pytest.raises(ValidationError, match="Unsupported trade-idea venue") as exc_info:
        service.record_fill(idea.decision_id, actor_id="executor", venue="robinhood")

    assert exc_info.value.context["field"] == "venue"
    assert exc_info.value.context["value"] == "robinhood"
    assert audit_path.read_text(encoding="utf-8") == original_audit
    assert service.get(idea.decision_id).state is TradeIdeaState.SUBMITTED
