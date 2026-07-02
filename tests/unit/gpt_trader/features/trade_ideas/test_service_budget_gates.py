from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    ActorType,
    AuditAction,
    AuditEvent,
    CloseoutResolution,
    MaxLoss,
    PolicyViolationError,
    RiskBudget,
    SizingRecommendation,
    TradeIdea,
    TradeIdeaState,
    new_event_id,
)
from gpt_trader.features.trade_ideas.service import TradeIdeaService


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def _append_legacy_approval(
    service: TradeIdeaService,
    idea: TradeIdea,
    *,
    reason: str,
) -> None:
    service.audit_log.append(
        AuditEvent(
            event_id=new_event_id(),
            timestamp=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
            decision_id=idea.decision_id,
            actor_type=ActorType.HUMAN,
            actor_id="rj",
            action=AuditAction.APPROVED,
            before_state=TradeIdeaState.PROPOSED,
            after_state=TradeIdeaState.APPROVED,
            reason=reason,
            record_hash=idea.record_hash(),
        )
    )


def test_approval_refused_when_same_day_loss_budget_is_exhausted(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    closed = build_trade_idea(decision_id="trade-20260612-closed")
    service.propose(closed, actor_id="idea-generator-v1")
    service.approve(closed.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(closed.decision_id, actor_id="operator", venue="manual")
    service.record_fill(closed.decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        closed.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_percent=Decimal("-9"),
    )
    candidate = build_trade_idea(decision_id="trade-20260612-candidate")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any("max_daily_loss_pct" in violation for violation in exc_info.value.violations)
    assert any(
        "projected daily loss exposure 10.5% exceeds limit 10%" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_amount_only_closeout_uses_its_own_equity_snapshot(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    closed = build_trade_idea(
        decision_id="trade-20260612-closed-amount-loss",
        max_loss=MaxLoss(amount=Decimal("100"), percent_of_account=Decimal("1")),
    )
    service.propose(closed, actor_id="idea-generator-v1")
    service.approve(closed.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(closed.decision_id, actor_id="operator", venue="manual")
    service.record_fill(closed.decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        closed.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_amount=Decimal("-950"),
    )
    candidate = build_trade_idea(decision_id="trade-20260612-after-amount-loss")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "projected daily loss exposure 11% exceeds limit 10%" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_no_trade_expiry_closeout_does_not_consume_daily_loss_budget(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    expired = build_trade_idea(decision_id="trade-20260612-expired-before-fill")
    service.propose(expired, actor_id="idea-generator-v1")
    service.expire(expired.decision_id, actor_id="expiry-sweep")
    service.record_closeout_attribution(
        expired.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.EXPIRY,
        realized_profit_loss_unavailable_reason="Expired before any venue fill",
    )
    candidate = build_trade_idea(decision_id="trade-20260612-after-no-trade-expiry")
    service.propose(candidate, actor_id="idea-generator-v1")

    view = service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert view.state is TradeIdeaState.APPROVED


def test_approval_refused_when_same_day_closeout_loss_is_unavailable(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    closed = build_trade_idea(decision_id="trade-20260612-closed-unknown-loss")
    service.propose(closed, actor_id="idea-generator-v1")
    service.approve(closed.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(closed.decision_id, actor_id="operator", venue="manual")
    service.record_fill(closed.decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        closed.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.EXPIRY,
        realized_profit_loss_unavailable_reason="Broker statement unavailable",
    )
    candidate = build_trade_idea(decision_id="trade-20260612-after-unknown-closeout")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "same-day closeout budget exposure includes 1 closeout(s) without realized profit/loss"
        in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_previous_day_closeout_does_not_consume_current_daily_loss_budget(
    tmp_path: Path,
) -> None:
    current_time = datetime(2026, 6, 11, 10, 0, tzinfo=UTC)
    service = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: current_time,
    )
    attest_account_equity(service)
    closed = build_trade_idea(decision_id="trade-20260611-closed")
    service.propose(closed, actor_id="idea-generator-v1")
    service.approve(closed.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(closed.decision_id, actor_id="operator", venue="manual")
    service.record_fill(closed.decision_id, actor_id="operator", venue="manual")
    service.record_closeout_attribution(
        closed.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_percent=Decimal("-9"),
    )
    current_time = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    candidate = build_trade_idea(decision_id="trade-20260612-candidate")
    service.propose(candidate, actor_id="idea-generator-v1")

    view = service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert view.state is TradeIdeaState.APPROVED


def test_approval_refused_when_open_notional_budget_would_be_exceeded(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    first = build_trade_idea(decision_id="trade-20260612-open-1")
    service.propose(first, actor_id="idea-generator-v1")
    service.approve(first.decision_id, actor_id="rj", reason="Risk verified")
    strict_budget = RiskBudget.from_dict(
        {
            **service.current_budget().to_dict(),
            "version": service.current_budget().version + 1,
            "max_open_notional_pct": "50",
        }
    )
    service.update_budget(strict_budget, actor_type=ActorType.HUMAN, actor_id="rj")
    candidate = build_trade_idea(decision_id="trade-20260612-open-2")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any("max_open_notional_pct" in violation for violation in exc_info.value.violations)
    assert any("projected open notional" in violation for violation in exc_info.value.violations)
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_filled_idea_without_closeout_still_consumes_open_budget_exposure(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    filled = build_trade_idea(decision_id="trade-20260612-filled-open")
    service.propose(filled, actor_id="idea-generator-v1")
    service.approve(filled.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(filled.decision_id, actor_id="operator", venue="manual")
    service.record_fill(filled.decision_id, actor_id="operator", venue="manual")
    strict_budget = RiskBudget.from_dict(
        {
            **service.current_budget().to_dict(),
            "version": service.current_budget().version + 1,
            "max_open_notional_pct": "50",
        }
    )
    service.update_budget(strict_budget, actor_type=ActorType.HUMAN, actor_id="rj")
    candidate = build_trade_idea(decision_id="trade-20260612-after-fill")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert service.get(filled.decision_id).closeout_attribution is None
    assert any("max_open_notional_pct" in violation for violation in exc_info.value.violations)
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_approval_refused_when_open_exposure_max_loss_percent_is_missing(
    service: TradeIdeaService,
) -> None:
    legacy_open = build_trade_idea(
        decision_id="trade-20260612-legacy-open-missing-risk",
        max_loss=MaxLoss(amount=Decimal("250"), percent_of_account=None),
    )
    service.propose(legacy_open, actor_id="idea-generator-v1")
    _append_legacy_approval(
        service,
        legacy_open,
        reason="Legacy approval before max-loss percent was required",
    )
    candidate = build_trade_idea(decision_id="trade-20260612-after-missing-risk")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "open budget exposure includes 1 idea(s) without max_loss.percent_of_account" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_open_notional_budget_uses_absolute_signed_notional(
    service: TradeIdeaService,
) -> None:
    attest_account_equity(service)
    signed_open = build_trade_idea(
        decision_id="trade-20260612-signed-open",
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("-0.1"),
            notional=Decimal("-6075"),
            rationale="Signed fixture notional still consumes exposure",
        ),
    )
    service.propose(signed_open, actor_id="idea-generator-v1")
    service.approve(signed_open.decision_id, actor_id="rj", reason="Risk verified")
    strict_budget = RiskBudget.from_dict(
        {
            **service.current_budget().to_dict(),
            "version": service.current_budget().version + 1,
            "max_open_notional_pct": "50",
        }
    )
    service.update_budget(strict_budget, actor_type=ActorType.HUMAN, actor_id="rj")
    candidate = build_trade_idea(decision_id="trade-20260612-signed-candidate")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any("max_open_notional_pct" in violation for violation in exc_info.value.violations)
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_approval_refused_when_candidate_notional_is_missing(
    service: TradeIdeaService,
) -> None:
    candidate = build_trade_idea(
        decision_id="trade-20260612-missing-notional",
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("0.1"),
            notional=None,
            rationale="Missing notional cannot prove exposure budget compliance",
        ),
    )
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "sizing_recommendation.notional is required" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_approval_refused_when_open_exposure_notional_is_missing(
    service: TradeIdeaService,
) -> None:
    legacy_open = build_trade_idea(
        decision_id="trade-20260612-legacy-open-missing-notional",
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("0.1"),
            notional=None,
            rationale="Legacy approved idea missing absolute notional",
        ),
    )
    service.propose(legacy_open, actor_id="idea-generator-v1")
    _append_legacy_approval(
        service,
        legacy_open,
        reason="Legacy approval before notional was required",
    )
    candidate = build_trade_idea(decision_id="trade-20260612-after-legacy-open")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "open budget exposure includes 1 idea(s) without sizing_recommendation.notional"
        in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_approval_refused_when_notional_budget_has_zero_equity_snapshot(
    service: TradeIdeaService,
) -> None:
    legacy_open = build_trade_idea(
        decision_id="trade-20260612-legacy-zero-equity",
        max_loss=MaxLoss(amount=Decimal("0"), percent_of_account=Decimal("1")),
    )
    service.propose(legacy_open, actor_id="idea-generator-v1")
    _append_legacy_approval(
        service,
        legacy_open,
        reason="Legacy approval whose max-loss pair implies zero equity",
    )
    candidate = build_trade_idea(decision_id="trade-20260612-after-zero-equity")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "account_equity_snapshot must be positive" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED
