from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import AuditAction, TradeIdeaService


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(built)
    return built


def test_audit_query_preserves_append_order_for_tied_timestamps(
    service: TradeIdeaService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_ids = iter(("evt-d", "evt-c", "evt-b", "evt-a"))
    monkeypatch.setattr(
        "gpt_trader.features.trade_ideas.service.new_event_id",
        lambda: next(event_ids),
    )
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Thesis and risk verified")
    service.record_submission(idea.decision_id, actor_id="executor", venue="manual")
    service.record_fill(idea.decision_id, actor_id="executor", venue="manual")

    page = service.list_audit_events(decision_id=idea.decision_id)

    assert [event.action for event in page.items] == [
        AuditAction.PROPOSED,
        AuditAction.APPROVED,
        AuditAction.SUBMITTED,
        AuditAction.FILLED,
    ]
    assert [event.event_id for event in page.items] == ["evt-d", "evt-c", "evt-b", "evt-a"]


def test_audit_query_pages_non_monotonic_timestamps_in_append_order(tmp_path: Path) -> None:
    current_time = [datetime(2026, 6, 12, 10, 3, tzinfo=UTC)]
    service = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: current_time[0],
    )
    attest_account_equity(service)
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    current_time[0] = datetime(2026, 6, 12, 10, 1, tzinfo=UTC)
    service.approve(idea.decision_id, actor_id="rj", reason="Thesis and risk verified")
    current_time[0] = datetime(2026, 6, 12, 10, 2, tzinfo=UTC)
    service.record_submission(idea.decision_id, actor_id="executor", venue="manual")
    current_time[0] = datetime(2026, 6, 12, 10, 4, tzinfo=UTC)
    service.record_fill(idea.decision_id, actor_id="executor", venue="manual")

    page = service.list_audit_events(decision_id=idea.decision_id, limit=2, offset=1)

    assert [event.action for event in page.items] == [
        AuditAction.APPROVED,
        AuditAction.SUBMITTED,
    ]
    assert [event.timestamp.minute for event in page.items] == [1, 2]
