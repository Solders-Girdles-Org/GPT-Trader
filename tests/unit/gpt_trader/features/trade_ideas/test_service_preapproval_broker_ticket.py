from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.trade_ideas import (
    BrokerTicket,
    PreApprovalBrokerTicketError,
    TicketStatus,
    TicketVenue,
    TradeIdeaService,
    TradeIdeaState,
)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def test_propose_accepts_omitted_broker_ticket(service: TradeIdeaService) -> None:
    view = service.propose(build_trade_idea(), actor_id="idea-generator-v1")

    assert view.state is TradeIdeaState.PROPOSED
    assert view.idea.broker_ticket == BrokerTicket()


def test_propose_accepts_explicit_default_broker_ticket(service: TradeIdeaService) -> None:
    idea = build_trade_idea(
        broker_ticket=BrokerTicket(
            venue=TicketVenue.NONE,
            status=TicketStatus.NOT_CREATED,
        )
    )

    view = service.propose(idea, actor_id="idea-generator-v1")

    assert view.state is TradeIdeaState.PROPOSED
    assert view.idea.broker_ticket == idea.broker_ticket


@pytest.mark.parametrize(
    "broker_ticket",
    [
        BrokerTicket(venue=TicketVenue.COINBASE, status=TicketStatus.NOT_CREATED),
        BrokerTicket(venue=TicketVenue.MANUAL, status=TicketStatus.NOT_CREATED),
        BrokerTicket(venue=TicketVenue.ROBINHOOD, status=TicketStatus.NOT_CREATED),
        BrokerTicket(venue=TicketVenue.NONE, status=TicketStatus.DRAFTED),
        BrokerTicket(venue=TicketVenue.NONE, status=TicketStatus.APPROVED),
        BrokerTicket(venue=TicketVenue.NONE, status=TicketStatus.SUBMITTED),
        BrokerTicket(venue=TicketVenue.NONE, status=TicketStatus.CANCELLED),
        BrokerTicket(venue=TicketVenue.COINBASE, status=TicketStatus.SUBMITTED),
    ],
)
def test_propose_rejects_preapproval_broker_ticket_before_mutation(
    tmp_path: Path,
    broker_ticket: BrokerTicket,
) -> None:
    root = tmp_path / "trade_ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    idea = build_trade_idea(broker_ticket=broker_ticket)

    with pytest.raises(PreApprovalBrokerTicketError) as exc_info:
        service.propose(idea, actor_id="idea-generator-v1")

    assert exc_info.value.context["field"] == "broker_ticket"
    assert exc_info.value.context["value"] == broker_ticket.to_dict()
    assert not (root / "records").exists()
    assert not (root / "audit.jsonl").exists()


def test_resubmit_accepts_explicit_default_broker_ticket(service: TradeIdeaService) -> None:
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.request_changes(idea.decision_id, actor_id="rj", reason="Tighten the invalidation")
    revised = build_trade_idea(
        invalidation="Daily close below 59000",
        broker_ticket=BrokerTicket(
            venue=TicketVenue.NONE,
            status=TicketStatus.NOT_CREATED,
        ),
    )

    view = service.resubmit(revised, actor_id="idea-generator-v1")

    assert view.state is TradeIdeaState.PROPOSED
    assert view.idea.broker_ticket == revised.broker_ticket


@pytest.mark.parametrize(
    "broker_ticket",
    [
        BrokerTicket(venue=TicketVenue.COINBASE, status=TicketStatus.NOT_CREATED),
        BrokerTicket(venue=TicketVenue.MANUAL, status=TicketStatus.NOT_CREATED),
        BrokerTicket(venue=TicketVenue.ROBINHOOD, status=TicketStatus.NOT_CREATED),
        BrokerTicket(venue=TicketVenue.NONE, status=TicketStatus.DRAFTED),
        BrokerTicket(venue=TicketVenue.NONE, status=TicketStatus.APPROVED),
        BrokerTicket(venue=TicketVenue.NONE, status=TicketStatus.SUBMITTED),
        BrokerTicket(venue=TicketVenue.NONE, status=TicketStatus.CANCELLED),
        BrokerTicket(venue=TicketVenue.MANUAL, status=TicketStatus.CANCELLED),
    ],
)
def test_resubmit_rejects_preapproval_broker_ticket_before_mutation(
    tmp_path: Path,
    broker_ticket: BrokerTicket,
) -> None:
    root = tmp_path / "trade_ideas"
    service = TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    idea = build_trade_idea()
    service.propose(idea, actor_id="idea-generator-v1")
    service.request_changes(idea.decision_id, actor_id="rj", reason="Tighten the invalidation")
    latest_path = root / "records" / idea.decision_id / "latest.json"
    audit_path = root / "audit.jsonl"
    original_latest = latest_path.read_text(encoding="utf-8")
    original_audit = audit_path.read_text(encoding="utf-8")
    revised = build_trade_idea(
        invalidation="Daily close below 59000",
        broker_ticket=broker_ticket,
    )

    with pytest.raises(PreApprovalBrokerTicketError) as exc_info:
        service.resubmit(revised, actor_id="idea-generator-v1")

    assert exc_info.value.context["field"] == "broker_ticket"
    assert exc_info.value.context["value"] == broker_ticket.to_dict()
    assert latest_path.read_text(encoding="utf-8") == original_latest
    assert audit_path.read_text(encoding="utf-8") == original_audit
    view = service.get(idea.decision_id)
    assert view.state is TradeIdeaState.NEEDS_CHANGES
    assert view.idea.broker_ticket == BrokerTicket()
