"""Per-instrument session accounting for the paper trade-idea spine (#1232)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.core.trading_calendar import get_calendar_for_instrument
from gpt_trader.features.trade_ideas import (
    ActorType,
    AutonomyMode,
    CloseoutResolution,
    PolicyViolationError,
    TimeHorizon,
    TradeIdeaService,
    TradeIdeaState,
)
from gpt_trader.features.trade_ideas.autonomy import RATCHET_ACTOR_ID


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _idea(decision_id: str, instrument: str, *, expires_at: datetime | None = None):
    return build_trade_idea(
        decision_id=decision_id,
        instrument=instrument,
        time_horizon=TimeHorizon(
            expected_hold="several sessions",
            expires_at=expires_at or datetime(2026, 7, 20, 20, 0, tzinfo=UTC),
        ),
    )


def _record_loss(
    service: TradeIdeaService,
    *,
    decision_id: str,
    instrument: str,
    loss_pct: str,
) -> None:
    idea = _idea(decision_id, instrument)
    service.propose(idea, actor_id="test-proposer")
    service.approve(decision_id, actor_id="test-operator", reason="test approval")
    service.record_submission(decision_id, actor_id="test-operator", venue="manual")
    service.record_fill(decision_id, actor_id="test-operator", venue="manual")
    service.record_closeout_attribution(
        decision_id,
        actor_id="test-operator",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_percent=Decimal(loss_pct),
    )


def _service(tmp_path: Path, clock: MutableClock) -> TradeIdeaService:
    service = TradeIdeaService(tmp_path / "ideas", now_factory=clock)
    attest_account_equity(service)
    return service


def test_daily_loss_uses_each_closeouts_own_session_window(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 10, 19, 0, tzinfo=UTC))  # Friday, XNYS open
    service = _service(tmp_path, clock)
    _record_loss(
        service,
        decision_id="trade-20260710-equity-loss",
        instrument="AAPL",
        loss_pct="-3",
    )
    clock.now = datetime(2026, 7, 10, 23, 0, tzinfo=UTC)
    _record_loss(
        service,
        decision_id="trade-20260710-crypto-loss",
        instrument="BTC-USD",
        loss_pct="-2",
    )

    clock.now = datetime(2026, 7, 11, 15, 0, tzinfo=UTC)  # Saturday
    weekend = service.approval_budget_context()
    assert weekend.same_day_realized_loss_pct == Decimal("3")
    assert weekend.daily_loss_session_dates == ("XNYS:2026-07-10",)

    clock.now = datetime(2026, 7, 13, 13, 29, tzinfo=UTC)  # Monday pre-open
    assert service.approval_budget_context().same_day_realized_loss_pct == Decimal("3")

    clock.now = datetime(2026, 7, 13, 13, 30, tzinfo=UTC)  # Monday open
    monday = service.approval_budget_context()
    assert monday.same_day_realized_loss_pct == Decimal("0")
    assert monday.daily_loss_session_dates == ()


def test_crypto_daily_loss_still_resets_at_utc_midnight(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 10, 23, 0, tzinfo=UTC))
    service = _service(tmp_path, clock)
    _record_loss(
        service,
        decision_id="trade-20260710-crypto-loss",
        instrument="BTC-USD",
        loss_pct="-2",
    )

    clock.now = datetime(2026, 7, 11, 0, 1, tzinfo=UTC)
    assert service.approval_budget_context().same_day_realized_loss_pct == Decimal("0")


def test_equity_weekend_loss_ratchets_with_truthful_session_evidence(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 10, 19, 0, tzinfo=UTC))
    service = _service(tmp_path, clock)
    _record_loss(
        service,
        decision_id="trade-20260710-equity-breach",
        instrument="AAPL",
        loss_pct="-12",
    )
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="test bounded mode",
    )
    candidate = _idea("trade-20260711-candidate", "BTC-USD")
    clock.now = datetime(2026, 7, 11, 15, 0, tzinfo=UTC)
    service.propose(candidate, actor_id="test-proposer")

    with pytest.raises(PolicyViolationError):
        service.approve(candidate.decision_id, actor_id="test-operator", reason="risk check")

    latest = service.autonomy_history()[-1]
    assert latest.actor_id == RATCHET_ACTOR_ID
    assert "session_dates=XNYS:2026-07-10" in latest.evidence[0]


def test_equity_review_latency_pauses_while_xnys_is_closed(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 10, 19, 30, tzinfo=UTC))  # 30m before close
    service = _service(tmp_path, clock)
    current = service.current_budget()
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            max_review_latency_hours=1,
            reason="test one-open-hour review window",
        ),
        ActorType.HUMAN,
        "test-operator",
    )
    idea = _idea("trade-20260710-review", "AAPL")
    service.propose(idea, actor_id="test-proposer")

    expected_deadline = datetime(2026, 7, 13, 14, 0, tzinfo=UTC)
    assert (
        service.review_deadline(
            idea,
            review_started_at=clock.now,
            budget=service.current_budget(),
        )
        == expected_deadline
    )

    clock.now = datetime(2026, 7, 13, 13, 45, tzinfo=UTC)
    queue = service.queue_status(warning_window_hours=1)
    assert queue.upcoming_expirations[0].expires_at == expected_deadline

    clock.now = expected_deadline + timedelta(minutes=1)
    with pytest.raises(PolicyViolationError, match="review deadline expired"):
        service.approve(idea.decision_id, actor_id="test-operator", reason="too late")


def test_crypto_review_latency_remains_wall_clock(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 10, 23, 30, tzinfo=UTC))
    service = _service(tmp_path, clock)
    current = service.current_budget()
    budget = replace(current, max_review_latency_hours=1)
    idea = _idea("trade-20260710-crypto-review", "BTC-USD")
    assert service.review_deadline(
        idea,
        review_started_at=clock.now,
        budget=budget,
    ) == datetime(2026, 7, 11, 0, 30, tzinfo=UTC)


def test_hard_equity_expiry_sweep_waits_for_next_open(tmp_path: Path) -> None:
    friday = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    clock = MutableClock(friday)
    service = _service(tmp_path, clock)
    idea = _idea(
        "trade-20260710-hard-expiry",
        "AAPL",
        expires_at=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
    )
    service.propose(idea, actor_id="test-proposer")

    clock.now = datetime(2026, 7, 11, 15, 0, tzinfo=UTC)
    assert service.expire_due_ideas() == []
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED
    assert service.get(idea.decision_id).idea.time_horizon.expires_at == datetime(
        2026, 7, 11, 12, 0, tzinfo=UTC
    )

    clock.now = datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
    assert [view.idea.decision_id for view in service.expire_due_ideas()] == [idea.decision_id]


def test_bounded_autonomy_still_suppresses_review_latency_expiry(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 10, 19, 30, tzinfo=UTC))
    service = _service(tmp_path, clock)
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
        reason="test bounded mode",
    )
    current = service.current_budget()
    service.update_budget(
        replace(current, version=current.version + 1, max_review_latency_hours=1),
        ActorType.HUMAN,
        "test-operator",
    )
    idea = _idea("trade-20260710-bounded", "AAPL")
    service.propose(idea, actor_id="test-proposer")

    clock.now = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)
    assert service.expire_due_ideas() == []
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED


def test_calendar_failure_refuses_review_and_never_mutates_sweep(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 10, 19, 0, tzinfo=UTC))

    def broken_resolver(_instrument: str):
        raise ValueError("calendar unavailable")

    service = TradeIdeaService(
        tmp_path / "ideas",
        now_factory=clock,
        calendar_resolver=broken_resolver,
    )
    attest_account_equity(service)
    idea = _idea("trade-20260710-calendar-failure", "AAPL")
    service.propose(idea, actor_id="test-proposer")

    with pytest.raises(PolicyViolationError, match="review deadline expired"):
        service.approve(idea.decision_id, actor_id="test-operator", reason="cannot verify")
    assert service.expire_due_ideas() == []
    assert service.get(idea.decision_id).state is TradeIdeaState.PROPOSED


def test_calendar_failure_never_undercounts_a_realized_loss(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 10, 23, 0, tzinfo=UTC))
    calendar_available = True

    def toggle_resolver(instrument: str):
        if not calendar_available:
            raise ValueError("calendar unavailable")
        return get_calendar_for_instrument(instrument)

    service = TradeIdeaService(
        tmp_path / "ideas",
        now_factory=clock,
        calendar_resolver=toggle_resolver,
    )
    attest_account_equity(service)
    _record_loss(
        service,
        decision_id="trade-20260710-unresolved-loss",
        instrument="BTC-USD",
        loss_pct="-2",
    )

    calendar_available = False
    clock.now = datetime(2026, 7, 11, 15, 0, tzinfo=UTC)
    context = service.approval_budget_context()
    assert context.same_day_realized_loss_pct == Decimal("2")
    assert context.daily_loss_session_dates == ("unresolved:2026-07-11",)


def test_unclassifiable_legacy_review_keeps_wall_clock_deadline(tmp_path: Path) -> None:
    start = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    clock = MutableClock(start)
    service = _service(tmp_path, clock)
    idea = _idea("trade-20260710-legacy", "BTC-USD-PERP")
    budget = replace(service.current_budget(), max_review_latency_hours=2)

    assert service.review_deadline(
        idea,
        review_started_at=start,
        budget=budget,
    ) == start + timedelta(hours=2)
