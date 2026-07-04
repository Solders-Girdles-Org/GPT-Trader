"""Operator console routes: rendering and identity-stamped decisions.

Every assertion runs against a real TradeIdeaService over a tmp_path root —
the console is a thin adapter, so the tests exercise the same audited calls
the CLI uses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpt_trader.features.trade_ideas.service import TradeIdeaService
from gpt_trader.features.trade_ideas.workflow import TradeIdeaState
from gpt_trader.web import create_app
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

_NOW = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
_DECISION_ID = "trade-20260612-001"


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(tmp_path, now_factory=lambda: _NOW)


@pytest.fixture
def client(service: TradeIdeaService) -> TestClient:
    return TestClient(create_app(service=service, actor_id="rj"))


def _propose_default(service: TradeIdeaService) -> None:
    attest_account_equity(service)
    service.propose(build_trade_idea(), actor_id="idea-generator-v1")


def test_queue_lists_pending_idea_with_eligibility(
    service: TradeIdeaService, client: TestClient
) -> None:
    _propose_default(service)

    response = client.get("/")

    assert response.status_code == 200
    assert _DECISION_ID in response.text
    assert "eligible" in response.text
    assert "acting as" in response.text and "rj" in response.text


def test_queue_renders_headroom_and_instrumentation_when_empty(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Daily-loss headroom" in response.text
    assert "Review instrumentation" in response.text


def test_idea_detail_shows_thesis_provenance_and_audit_trail(
    service: TradeIdeaService, client: TestClient
) -> None:
    _propose_default(service)

    response = client.get(f"/ideas/{_DECISION_ID}")

    assert response.status_code == 200
    assert "BTC reclaiming the 50-day average" in response.text
    assert "coinbase:candles:BTC-USD:1d:2026-06-11" in response.text
    assert "proposed" in response.text
    assert "idea-generator-v1" in response.text


def test_unknown_idea_returns_404(client: TestClient) -> None:
    response = client.get("/ideas/no-such-idea")

    assert response.status_code == 404


def test_approve_stamps_actor_and_redirects_to_queue(
    service: TradeIdeaService, client: TestClient
) -> None:
    _propose_default(service)

    response = client.post(
        f"/ideas/{_DECISION_ID}/approve",
        data={"reason": "Risk verified against budget"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    view = service.get(_DECISION_ID)
    assert view.state is TradeIdeaState.APPROVED
    decision_event = view.events[-1]
    assert decision_event.actor_id == "rj"
    assert decision_event.reason == "Risk verified against budget"


def test_reject_and_request_changes_transition_state(
    service: TradeIdeaService, client: TestClient
) -> None:
    _propose_default(service)

    changes = client.post(
        f"/ideas/{_DECISION_ID}/request-changes",
        data={"reason": "Tighten the invalidation level"},
        follow_redirects=False,
    )
    assert changes.status_code == 303
    assert service.get(_DECISION_ID).state is TradeIdeaState.NEEDS_CHANGES

    rejected = client.post(
        f"/ideas/{_DECISION_ID}/reject",
        data={"reason": "Thesis no longer valid"},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert service.get(_DECISION_ID).state is TradeIdeaState.REJECTED


def test_blank_reason_is_refused(service: TradeIdeaService, client: TestClient) -> None:
    _propose_default(service)

    response = client.post(f"/ideas/{_DECISION_ID}/approve", data={"reason": "   "})

    assert response.status_code == 400
    assert "reason is required" in response.text.lower()
    assert service.get(_DECISION_ID).state is TradeIdeaState.PROPOSED


def test_policy_refusal_renders_error_not_approval(
    service: TradeIdeaService, client: TestClient
) -> None:
    # No attested account equity: the notional approval gate must refuse.
    service.propose(build_trade_idea(), actor_id="idea-generator-v1")

    response = client.post(
        f"/ideas/{_DECISION_ID}/approve",
        data={"reason": "Trying anyway"},
    )

    assert response.status_code == 400
    assert "refused" in response.text.lower()
    assert service.get(_DECISION_ID).state is TradeIdeaState.PROPOSED


def test_decision_on_unknown_idea_returns_404(client: TestClient) -> None:
    response = client.post("/ideas/no-such-idea/approve", data={"reason": "x"})

    assert response.status_code == 404
