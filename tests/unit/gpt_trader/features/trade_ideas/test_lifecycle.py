"""Canonical lifecycle read classification (#1212).

One read model answers "is this idea an open position, a closed trade, or an
overdue evidence failure?" for report, scorecard, and CLI consumers. FILLED is
a terminal workflow state, so state alone conflates a legitimately open
position with a missing closeout; the classification separates them using the
existing expiry contract: an unclosed fill becomes overdue once its idea's
``expires_at`` has passed (the exit monitor would have marked it to market by
then), and any other terminal idea without attribution is overdue immediately
(auto-attribution owes it in the same turn).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    CloseoutResolution,
    LifecycleClassification,
    TimeHorizon,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaView,
    classify_lifecycle,
)

CLOCK = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(tmp_path / "ideas", now_factory=lambda: CLOCK)
    attest_account_equity(built)
    return built


def _filled_idea(service: TradeIdeaService, *, expires_at: datetime | None) -> str:
    decision_id = "trade-20260612-001"
    idea = build_trade_idea(
        decision_id=decision_id,
        time_horizon=TimeHorizon(expected_hold="1-4h", expires_at=expires_at),
    )
    service.propose(idea, actor_id="proposer")
    service.approve(decision_id, actor_id="rj", reason="verified")
    service.record_submission(decision_id, actor_id="executor", venue="paper")
    service.record_fill(decision_id, actor_id="paper", venue="paper")
    return decision_id


def test_non_terminal_idea_is_not_applicable(service: TradeIdeaService) -> None:
    idea = build_trade_idea(
        time_horizon=TimeHorizon(expected_hold="1-4h", expires_at=CLOCK + timedelta(hours=4))
    )
    service.propose(idea, actor_id="proposer")

    view = service.get(idea.decision_id)

    assert classify_lifecycle(view, now=CLOCK) is LifecycleClassification.NOT_APPLICABLE


def test_attributed_terminal_idea_is_closed(service: TradeIdeaService) -> None:
    decision_id = _filled_idea(service, expires_at=CLOCK + timedelta(hours=4))
    service.record_closeout_attribution(
        decision_id,
        actor_id="exit-monitor",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=Decimal("1.2"),
    )

    view = service.get(decision_id)

    assert classify_lifecycle(view, now=CLOCK) is LifecycleClassification.CLOSED


def test_unexpired_fill_without_closeout_is_open(service: TradeIdeaService) -> None:
    decision_id = _filled_idea(service, expires_at=CLOCK + timedelta(hours=4))

    view = service.get(decision_id)

    assert classify_lifecycle(view, now=CLOCK) is LifecycleClassification.OPEN_FILLED


def test_expired_fill_without_closeout_is_overdue(service: TradeIdeaService) -> None:
    decision_id = _filled_idea(service, expires_at=CLOCK + timedelta(hours=4))

    view = service.get(decision_id)

    late = CLOCK + timedelta(hours=5)
    assert classify_lifecycle(view, now=late) is LifecycleClassification.OVERDUE_UNATTRIBUTED


def test_fill_without_expiry_is_overdue() -> None:
    # Approval policy refuses expiry-less ideas, so this state can only come
    # from legacy/imported records; classification must still surface it.
    view = TradeIdeaView(
        idea=build_trade_idea(time_horizon=TimeHorizon(expected_hold="1-4h", expires_at=None)),
        state=TradeIdeaState.FILLED,
        events=(),
    )

    assert classify_lifecycle(view, now=CLOCK) is LifecycleClassification.OVERDUE_UNATTRIBUTED


def test_unattributed_non_filled_terminal_idea_is_overdue(service: TradeIdeaService) -> None:
    idea = build_trade_idea(
        time_horizon=TimeHorizon(expected_hold="1-4h", expires_at=CLOCK + timedelta(hours=4))
    )
    service.propose(idea, actor_id="proposer")
    service.expire(idea.decision_id)

    view = service.get(idea.decision_id)

    assert classify_lifecycle(view, now=CLOCK) is LifecycleClassification.OVERDUE_UNATTRIBUTED
