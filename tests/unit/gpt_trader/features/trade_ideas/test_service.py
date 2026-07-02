from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    AuditIntegrityError,
    BudgetIntegrityError,
    BudgetLogEntry,
    DuplicateTradeIdeaError,
    InvalidTransitionError,
    MaxLoss,
    PolicyViolationError,
    ProductType,
    RiskBudget,
    RiskBudgetLog,
    TimeHorizon,
    TradeDirection,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaStore,
    UnknownTradeIdeaError,
)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def test_propose_creates_proposed_view(service: TradeIdeaService) -> None:
    view = service.propose(build_trade_idea(), actor_id="idea-generator-v1")

    assert view.state is TradeIdeaState.PROPOSED
    assert view.events[0].actor_type is ActorType.AI
    assert view.events[0].record_hash == view.idea.record_hash()


def test_duplicate_propose_rejects_before_record_or_audit_mutation(tmp_path: Path) -> None:
    root = tmp_path / "trade_ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    latest_path = root / "records" / idea.decision_id / "latest.json"
    audit_path = root / "audit.jsonl"
    original_latest = latest_path.read_text(encoding="utf-8")
    original_audit = audit_path.read_text(encoding="utf-8")
    revised = build_trade_idea(thesis="Edited thesis that must not persist")

    with pytest.raises(DuplicateTradeIdeaError):
        service.propose(revised, actor_id="idea-generator-v1")

    assert latest_path.read_text(encoding="utf-8") == original_latest
    assert audit_path.read_text(encoding="utf-8") == original_audit
    assert service.get(idea.decision_id).idea.thesis == idea.thesis
    assert len(service.get(idea.decision_id).events) == 1


def test_full_lifecycle_to_fill(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Thesis and risk verified")
    service.record_submission(idea.decision_id, actor_id="executor", venue="coinbase")
    view = service.record_fill(
        idea.decision_id, actor_id="coinbase", venue="coinbase", external_order_id="abc-123"
    )

    assert view.state is TradeIdeaState.FILLED
    assert [event.action for event in view.events] == [
        AuditAction.PROPOSED,
        AuditAction.APPROVED,
        AuditAction.SUBMITTED,
        AuditAction.FILLED,
    ]


def test_changes_loop_keeps_every_record_version(service: TradeIdeaService) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.request_changes(idea.decision_id, actor_id="rj", reason="Tighten the invalidation")
    revised = build_trade_idea(invalidation="Daily close below 59000")
    view = service.resubmit(revised, actor_id="idea-generator-v1")

    assert view.state is TradeIdeaState.PROPOSED
    assert view.idea.invalidation == "Daily close below 59000"
    hashes = {event.record_hash for event in view.events}
    assert idea.record_hash() in hashes
    assert revised.record_hash() in hashes


def test_resubmit_rejects_orphaned_record_without_audit_mutation(tmp_path: Path) -> None:
    root = tmp_path / "trade_ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    idea = build_trade_idea(decision_id="trade-20260612-orphaned-resubmit")
    TradeIdeaStore(root / "records").save(idea)
    latest_path = root / "records" / idea.decision_id / "latest.json"
    audit_path = root / "audit.jsonl"
    original_latest = latest_path.read_text(encoding="utf-8")
    revised = build_trade_idea(
        decision_id=idea.decision_id,
        invalidation="Daily close below 59000",
    )

    with pytest.raises(InvalidTransitionError) as exc_info:
        service.resubmit(revised, actor_id="idea-generator-v1")

    assert exc_info.value.context["value"] == "none"
    assert latest_path.read_text(encoding="utf-8") == original_latest
    assert not audit_path.exists()


def test_get_rejects_orphaned_record_without_audit_trail(tmp_path: Path) -> None:
    root = tmp_path / "trade_ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    idea = build_trade_idea(decision_id="trade-20260612-orphaned-get")
    TradeIdeaStore(root / "records").save(idea)

    with pytest.raises(AuditIntegrityError, match="has no audit trail") as exc_info:
        service.get(idea.decision_id)

    assert exc_info.value.context["field"] == "decision_id"
    assert exc_info.value.context["value"] == idea.decision_id


def test_list_views_rejects_orphaned_record_without_audit_trail(tmp_path: Path) -> None:
    root = tmp_path / "trade_ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    idea = build_trade_idea(decision_id="trade-20260612-orphaned-list")
    TradeIdeaStore(root / "records").save(idea)

    with pytest.raises(AuditIntegrityError, match="has no audit trail"):
        service.list_views()


def test_approval_refused_for_budget_violation(service: TradeIdeaService) -> None:
    idea = build_trade_idea(
        max_loss=MaxLoss(amount=Decimal("900"), percent_of_account=Decimal("9"))
    )
    service.propose(idea, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(idea.decision_id, actor_id="rj", reason="Looks fine")

    assert any("exceeds budget cap" in violation for violation in exc_info.value.violations)
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED


def test_futures_approval_requires_budget_leverage_flag(service: TradeIdeaService) -> None:
    idea = build_trade_idea(product_type=ProductType.FUTURES)
    service.propose(idea, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    assert any("allow_futures_leverage" in violation for violation in exc_info.value.violations)
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED

    allowed_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "allow_futures_leverage": True,
            "account_equity": "20000",
        }
    )
    service.update_budget(allowed_budget, actor_type=ActorType.HUMAN, actor_id="rj")

    view = service.approve(idea.decision_id, actor_id="rj", reason="Futures leverage accepted")

    assert view.state is TradeIdeaState.APPROVED


def test_short_approval_requires_naked_shorts_flag(service: TradeIdeaService) -> None:
    idea = build_trade_idea(direction=TradeDirection.SHORT)
    service.propose(idea, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    assert any("allow_naked_shorts" in violation for violation in exc_info.value.violations)
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED

    disabled_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "allow_naked_shorts": False,
        }
    )
    service.update_budget(disabled_budget, actor_type=ActorType.HUMAN, actor_id="rj")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")

    assert any("allow_naked_shorts" in violation for violation in exc_info.value.violations)
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED

    allowed_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 3,
            "allow_naked_shorts": True,
            "account_equity": "20000",
        }
    )
    service.update_budget(allowed_budget, actor_type=ActorType.HUMAN, actor_id="rj")

    view = service.approve(idea.decision_id, actor_id="rj", reason="Naked short accepted")

    assert view.state is TradeIdeaState.APPROVED


def test_unknown_decision_id_is_an_error(service: TradeIdeaService) -> None:
    with pytest.raises(UnknownTradeIdeaError):
        service.approve("trade-missing", actor_id="rj", reason="?")


def test_list_views_filters_by_state(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    first = build_trade_idea(decision_id="trade-20260612-001")
    second = build_trade_idea(decision_id="trade-20260612-002")
    service.propose(first, actor_id="idea-generator-v1")
    service.propose(second, actor_id="idea-generator-v1")
    service.approve(first.decision_id, actor_id="rj", reason="Verified")

    approved = service.list_views(TradeIdeaState.APPROVED)

    assert [view.idea.decision_id for view in approved] == ["trade-20260612-001"]
    assert service.open_approved_count() == 1


def test_expire_is_a_system_action(service: TradeIdeaService) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")

    view = service.expire(idea.decision_id)

    assert view.state is TradeIdeaState.EXPIRED
    assert view.events[-1].actor_type is ActorType.SYSTEM


def test_expire_due_ideas_skips_submitted_and_continues(tmp_path: Path) -> None:
    current_time = datetime(2026, 6, 10, 10, 0, tzinfo=UTC)
    service = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: current_time,
    )
    attest_account_equity(service)
    expires_soon = TimeHorizon(
        expected_hold="1 day",
        expires_at=datetime(2026, 6, 11, 10, 0, tzinfo=UTC),
    )
    submitted = build_trade_idea(
        decision_id="trade-20260612-submitted",
        time_horizon=expires_soon,
    )
    proposed = build_trade_idea(
        decision_id="trade-20260612-proposed",
        time_horizon=expires_soon,
    )
    service.propose(submitted, actor_id="idea-generator-v1")
    service.approve(submitted.decision_id, actor_id="rj", reason="Fresh at approval time")
    service.record_submission(submitted.decision_id, actor_id="manual", venue="coinbase")
    service.propose(proposed, actor_id="idea-generator-v1")

    current_time = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    expired = service.expire_due_ideas()

    assert [view.idea.decision_id for view in expired] == [proposed.decision_id]
    assert service.get(submitted.decision_id).state is TradeIdeaState.SUBMITTED
    assert service.get(proposed.decision_id).state is TradeIdeaState.EXPIRED


def test_review_latency_budget_refuses_approval_and_sweep_expires_far_future_idea(
    tmp_path: Path,
) -> None:
    current_time = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    service = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: current_time,
    )
    idea = build_trade_idea(
        decision_id="trade-20260612-review-latency",
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=datetime(2035, 6, 19, 16, 0, tzinfo=UTC),
        ),
    )
    service.propose(idea, actor_id="idea-generator-v1")
    current_time = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    strict_budget = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "max_review_latency_hours": 1,
        }
    )
    service.update_budget(strict_budget, actor_type=ActorType.HUMAN, actor_id="rj")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(idea.decision_id, actor_id="rj", reason="Review lagged")

    assert any("review deadline expired" in violation for violation in exc_info.value.violations)
    expired = service.expire_due_ideas()

    assert [view.idea.decision_id for view in expired] == [idea.decision_id]
    assert service.get(idea.decision_id).state is TradeIdeaState.EXPIRED


@pytest.mark.parametrize(
    "field",
    [
        "sizing_capped_by_budget",
        "allow_futures_leverage",
        "allow_naked_shorts",
    ],
)
def test_persisted_budget_rejects_non_boolean_boolean_fields(
    tmp_path: Path,
    field: str,
) -> None:
    path = tmp_path / "risk_budget.jsonl"
    entry = BudgetLogEntry(
        timestamp=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
        actor_type=ActorType.SYSTEM,
        actor_id="seed-defaults",
        budget=DEFAULT_RISK_BUDGET,
    ).to_dict()
    entry["budget"][field] = "false"
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    with pytest.raises(BudgetIntegrityError, match=f"{field} must be a JSON boolean"):
        RiskBudgetLog(path).current()
