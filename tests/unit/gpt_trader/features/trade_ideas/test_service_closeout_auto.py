"""Auto-attribution of never-filled terminal ideas (M4/W2, issue #1214).

An ``EXPIRED`` idea can only be reached from a pre-fill state (``SUBMITTED``
cannot expire), so no position was ever opened and realized P&L is genuinely
unavailable. Recording an ``EXPIRY`` closeout with an unavailable reason keeps
``attribution_coverage`` honest and at 100% without inventing a market outcome.
"""

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
    CloseoutResolution,
    TimeHorizon,
    TradeIdeaService,
    TradeIdeaState,
)

_LIVE_HORIZON = TimeHorizon(
    expected_hold="3-10 days",
    expires_at=datetime(2026, 6, 25, 16, 0, tzinfo=UTC),
)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(built)
    return built


def _propose_and_expire(service: TradeIdeaService, decision_id: str) -> None:
    idea = build_trade_idea(decision_id=decision_id)
    service.propose(idea, actor_id="idea-generator-v1")
    service.expire(decision_id)


def test_auto_attribute_records_expiry_closeout_for_expired_idea(
    service: TradeIdeaService,
) -> None:
    _propose_and_expire(service, "trade-20260620-001")

    recorded = service.auto_attribute_expired_ideas()

    assert [record.decision_id for record in recorded] == ["trade-20260620-001"]
    closeout = recorded[0]
    assert closeout.resolution is CloseoutResolution.EXPIRY
    assert closeout.actor_type == ActorType.SYSTEM.value
    assert closeout.realized_profit_loss_amount is None
    assert closeout.realized_profit_loss_percent is None
    assert closeout.realized_profit_loss_unavailable_reason
    view = service.get("trade-20260620-001")
    assert view.state is TradeIdeaState.EXPIRED
    assert view.closeout_attribution == closeout
    # terminal_event_id must anchor to the idea's own terminal (expiry) event.
    assert closeout.terminal_event_id == view.events[-1].event_id


def test_auto_attribute_is_idempotent(service: TradeIdeaService) -> None:
    _propose_and_expire(service, "trade-20260620-001")

    first = service.auto_attribute_expired_ideas()
    second = service.auto_attribute_expired_ideas()

    assert len(first) == 1
    assert second == []
    assert service.get_closeout_attribution("trade-20260620-001") is not None


def test_auto_attribute_skips_open_and_filled_ideas(service: TradeIdeaService) -> None:
    # An open (proposed) idea has no terminal outcome yet.
    open_idea = build_trade_idea(
        decision_id="trade-20260620-open", instrument="ETH-USD", time_horizon=_LIVE_HORIZON
    )
    service.propose(open_idea, actor_id="idea-generator-v1")

    # A filled idea is terminal but *did* open a position; its realized P&L needs
    # an exit model (issue #1218), so auto-attribution must not touch it.
    filled = build_trade_idea(
        decision_id="trade-20260620-filled", instrument="SOL-USD", time_horizon=_LIVE_HORIZON
    )
    service.propose(filled, actor_id="idea-generator-v1")
    service.approve("trade-20260620-filled", actor_id="rj", reason="verified")
    service.record_submission("trade-20260620-filled", actor_id="executor", venue="coinbase")
    service.record_fill("trade-20260620-filled", actor_id="coinbase", venue="coinbase")

    recorded = service.auto_attribute_expired_ideas()

    assert recorded == []
    assert service.get_closeout_attribution("trade-20260620-open") is None
    assert service.get_closeout_attribution("trade-20260620-filled") is None


def test_auto_attribute_preserves_existing_manual_closeout(
    service: TradeIdeaService,
) -> None:
    _propose_and_expire(service, "trade-20260620-001")
    manual = service.record_closeout_attribution(
        "trade-20260620-001",
        actor_id="rj",
        resolution=CloseoutResolution.INVALIDATION,
        realized_profit_loss_amount=Decimal("-40"),
    )

    recorded = service.auto_attribute_expired_ideas()

    assert recorded == []
    # The human's attribution is authoritative and untouched.
    assert service.get_closeout_attribution("trade-20260620-001") == manual


def test_auto_attribute_backfills_multiple_expired_ideas(
    service: TradeIdeaService,
) -> None:
    for index in range(3):
        _propose_and_expire(service, f"trade-20260620-{index:03d}")

    recorded = service.auto_attribute_expired_ideas()

    assert {record.decision_id for record in recorded} == {
        "trade-20260620-000",
        "trade-20260620-001",
        "trade-20260620-002",
    }
    assert all(record.resolution is CloseoutResolution.EXPIRY for record in recorded)
