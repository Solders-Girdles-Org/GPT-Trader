from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    Confidence,
    ConfidenceLabel,
    MaxLoss,
    RiskBudget,
    TimeHorizon,
    TradeDirection,
    TradeIdeaListQuery,
    TradeIdeaListSortKey,
    TradeIdeaService,
)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def test_list_view_result_filters_sorts_and_paginates(service: TradeIdeaService) -> None:
    low_loss = build_trade_idea(
        decision_id="trade-20260612-btc-low",
        instrument="BTC-USD",
        confidence=Confidence(label=ConfidenceLabel.MEDIUM, rationale="Constructive setup"),
        max_loss=MaxLoss(amount=Decimal("150"), percent_of_account=Decimal("1")),
    )
    high_loss = build_trade_idea(
        decision_id="trade-20260612-btc-high",
        instrument="BTC-USD",
        confidence=Confidence(label=ConfidenceLabel.HIGH, rationale="Strong confirmation"),
        max_loss=MaxLoss(amount=Decimal("400"), percent_of_account=Decimal("4")),
    )
    other = build_trade_idea(
        decision_id="trade-20260612-eth-short",
        instrument="ETH-USD",
        direction=TradeDirection.SHORT,
        confidence=Confidence(label=ConfidenceLabel.LOW, rationale="Weak confirmation"),
        max_loss=MaxLoss(amount=Decimal("100"), percent_of_account=Decimal("0.5")),
    )
    service.propose(low_loss, actor_id="idea-generator-v1")
    service.propose(high_loss, actor_id="idea-generator-v1")
    service.propose(other, actor_id="idea-generator-v1")

    result = service.list_view_result(
        TradeIdeaListQuery(
            instrument="btc-usd",
            direction=TradeDirection.LONG,
            min_confidence=ConfidenceLabel.MEDIUM,
            sort_by=TradeIdeaListSortKey.MAX_LOSS_PCT,
            descending=True,
            limit=1,
            offset=0,
        )
    )

    assert result.total_count == 2
    assert result.returned_count == 1
    assert result.has_more is True
    assert [view.idea.decision_id for view in result.views] == ["trade-20260612-btc-high"]


def _horizon(expires_at: datetime) -> TimeHorizon:
    return TimeHorizon(expected_hold="3-10 days", expires_at=expires_at)


def test_queue_status_counts_pending_states_and_upcoming_expirations(
    service: TradeIdeaService,
) -> None:
    soon = build_trade_idea(
        decision_id="trade-20260612-soon",
        instrument="BTC-USD",
        time_horizon=_horizon(datetime(2026, 6, 12, 12, 0, tzinfo=UTC)),
    )
    change = build_trade_idea(
        decision_id="trade-20260612-change",
        instrument="ETH-USD",
        time_horizon=_horizon(datetime(2026, 6, 12, 14, 0, tzinfo=UTC)),
    )
    later = build_trade_idea(
        decision_id="trade-20260612-later",
        instrument="SOL-USD",
        time_horizon=_horizon(datetime(2026, 6, 14, 10, 0, tzinfo=UTC)),
    )
    approved = build_trade_idea(
        decision_id="trade-20260612-approved",
        instrument="DOGE-USD",
        time_horizon=_horizon(datetime(2026, 6, 12, 11, 0, tzinfo=UTC)),
    )
    service.propose(soon, actor_id="idea-generator-v1")
    service.propose(change, actor_id="idea-generator-v1")
    service.request_changes(change.decision_id, actor_id="rj", reason="Tighten risk")
    service.propose(later, actor_id="idea-generator-v1")
    service.propose(approved, actor_id="idea-generator-v1")
    attest_account_equity(service)
    service.approve(approved.decision_id, actor_id="rj", reason="Risk verified")

    status = service.queue_status(warning_window_hours=6)

    assert status.proposed_count == 2
    assert status.needs_changes_count == 1
    assert status.pending_total == 3
    assert [expiration.decision_id for expiration in status.upcoming_expirations] == [
        "trade-20260612-soon",
        "trade-20260612-change",
    ]
    assert status.upcoming_expirations[0].deadline_type == "time_horizon"
    assert status.upcoming_expirations[0].seconds_until_expiry == 7200
    assert status.to_dict()["counts"] == {
        "proposed": 2,
        "needs_changes": 1,
        "pending_total": 3,
        "upcoming_expirations": 2,
    }


def test_queue_status_reports_review_latency_deadline(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea(
        decision_id="trade-20260612-review-latency",
        time_horizon=_horizon(datetime(2035, 6, 19, 16, 0, tzinfo=UTC)),
    )
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

    status = service.queue_status(warning_window_hours=3)

    assert status.upcoming_expiration_count == 1
    expiration = status.upcoming_expirations[0]
    assert expiration.decision_id == "trade-20260612-review-latency"
    assert expiration.deadline_type == "review_latency"
    assert expiration.expires_at == datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    assert expiration.seconds_until_expiry == 7200


def test_queue_status_reports_overdue_review_latency_deadline(tmp_path: Path) -> None:
    current_time = [datetime(2026, 6, 12, 10, 0, tzinfo=UTC)]
    service = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: current_time[0],
    )
    idea = build_trade_idea(
        decision_id="trade-20260612-overdue-review",
        time_horizon=_horizon(datetime(2035, 6, 19, 16, 0, tzinfo=UTC)),
    )
    service.propose(idea, actor_id="idea-generator-v1")
    service.update_budget(
        RiskBudget.from_dict(
            {
                **DEFAULT_RISK_BUDGET.to_dict(),
                "version": 2,
                "max_review_latency_hours": 1,
            }
        ),
        actor_type=ActorType.HUMAN,
        actor_id="rj",
    )
    current_time[0] = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)

    status = service.queue_status(warning_window_hours=3)

    assert status.upcoming_expiration_count == 1
    expiration = status.upcoming_expirations[0]
    assert expiration.decision_id == "trade-20260612-overdue-review"
    assert expiration.deadline_type == "review_latency"
    assert expiration.expires_at == datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
    assert expiration.seconds_until_expiry == 0


def test_queue_status_reports_earlier_overdue_deadline(tmp_path: Path) -> None:
    current_time = [datetime(2026, 6, 12, 10, 0, tzinfo=UTC)]
    service = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: current_time[0],
    )
    idea = build_trade_idea(
        decision_id="trade-20260612-overdue-horizon",
        time_horizon=_horizon(datetime(2026, 6, 12, 11, 0, tzinfo=UTC)),
    )
    service.propose(idea, actor_id="idea-generator-v1")
    service.update_budget(
        RiskBudget.from_dict(
            {
                **DEFAULT_RISK_BUDGET.to_dict(),
                "version": 2,
                "max_review_latency_hours": 4,
            }
        ),
        actor_type=ActorType.HUMAN,
        actor_id="rj",
    )
    current_time[0] = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)

    status = service.queue_status(warning_window_hours=6)

    assert status.upcoming_expiration_count == 1
    expiration = status.upcoming_expirations[0]
    assert expiration.decision_id == "trade-20260612-overdue-horizon"
    assert expiration.deadline_type == "time_horizon"
    assert expiration.expires_at == datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
    assert expiration.seconds_until_expiry == 0


def test_queue_status_empty_queue_is_noop(service: TradeIdeaService) -> None:
    status = service.queue_status()

    assert status.pending_total == 0
    assert status.upcoming_expirations == ()


def test_queue_status_rejects_negative_warning_window(service: TradeIdeaService) -> None:
    with pytest.raises(ValidationError, match="warning_window_hours must be non-negative"):
        service.queue_status(warning_window_hours=-1)
