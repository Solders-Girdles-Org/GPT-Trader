from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    InvalidTransitionError,
    TradeIdeaService,
    TradeIdeaState,
)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(built)
    return built


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def test_robinhood_ticket_export_is_render_only_and_does_not_mutate_state(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea(decision_id="trade-robinhood-render-only")
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    before = service.get(idea.decision_id)

    payload = service.export_broker_ticket_payload(
        idea.decision_id,
        venue="robinhood",
        venue_order_type="operator_selected",
        time_in_force="operator_selected",
    )

    after = service.get(idea.decision_id)
    assert _mapping(payload["broker_ticket"])["exported"] == {
        "venue": "robinhood",
        "status": "approved",
    }
    venue_request = _mapping(payload["venue_request"])
    assert _text(venue_request["client_order_id"]).startswith(
        "gpt-trader-robinhood-trade-robinhood-render-only-"
    )
    assert _mapping(payload["venue_payload"])["venue"] == "robinhood"
    assert before.idea.to_dict() == after.idea.to_dict()
    assert before.events == after.events
    assert after.state is TradeIdeaState.APPROVED


def test_robinhood_ticket_export_refuses_submitted_state(
    service: TradeIdeaService,
) -> None:
    idea = build_trade_idea(decision_id="trade-robinhood-no-submitted-state")
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(idea.decision_id, actor_id="operator", venue="manual")

    with pytest.raises(InvalidTransitionError, match="render-only Robinhood") as exc_info:
        service.export_broker_ticket_payload(
            idea.decision_id,
            venue="robinhood",
            venue_order_type="operator_selected",
            time_in_force="operator_selected",
        )

    assert exc_info.value.context["field"] == "after_state"
    assert exc_info.value.context["value"] == "submitted"
