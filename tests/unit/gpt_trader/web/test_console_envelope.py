"""Envelope page: budget lever management, audited histories, exception framing."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpt_trader.features.trade_ideas.audit import ActorType
from gpt_trader.features.trade_ideas.autonomy import RATCHET_ACTOR_ID
from gpt_trader.features.trade_ideas.models import AutonomyMode, MaxLoss
from gpt_trader.features.trade_ideas.service import TradeIdeaService
from gpt_trader.web import create_app
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

_NOW = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    return TradeIdeaService(tmp_path, now_factory=lambda: _NOW)


@pytest.fixture
def client(service: TradeIdeaService) -> TestClient:
    return TestClient(create_app(service=service, actor_id="rj"))


def _lever_form(service: TradeIdeaService, **overrides: str) -> dict[str, str]:
    """Form payload mirroring the current budget, with per-test overrides."""
    budget = service.peek_budget()
    payload = {
        "base_version": str(budget.version),
        "reason": "Operator adjustment",
        "max_loss_per_idea_pct": str(budget.max_loss_per_idea_pct),
        "max_daily_loss_pct": str(budget.max_daily_loss_pct),
        "max_open_notional_pct": str(budget.max_open_notional_pct),
        "max_concurrent_approved_tickets": str(budget.max_concurrent_approved_tickets),
        "max_review_latency_hours": str(budget.max_review_latency_hours),
        "gain_retention_floor_pct": str(budget.gain_retention_floor_pct),
        "account_equity": "" if budget.account_equity is None else str(budget.account_equity),
    }
    if budget.sizing_capped_by_budget:
        payload["sizing_capped_by_budget"] = "on"
    if budget.allow_futures_leverage:
        payload["allow_futures_leverage"] = "on"
    if budget.allow_naked_shorts:
        payload["allow_naked_shorts"] = "on"
    payload.update(overrides)
    return payload


def test_envelope_renders_current_budget_and_autonomy(
    service: TradeIdeaService, client: TestClient
) -> None:
    attest_account_equity(service, equity=Decimal("20000"))

    response = client.get("/envelope")

    assert response.status_code == 200
    assert "Budget levers" in response.text
    assert 'value="2"' in response.text  # hidden base_version after attestation bump
    assert "human_approved_execution" in response.text
    assert "Operator-attested equity for tests" in response.text  # budget history reason


def test_envelope_get_does_not_seed_logs(
    tmp_path: Path, service: TradeIdeaService, client: TestClient
) -> None:
    # A pending idea exercises the per-row violation check, which historically
    # went through the seeding current_budget()/current_autonomy() reads; the
    # console must use the peek variant so a GET never creates durable state.
    service.propose(build_trade_idea(), actor_id="idea-generator-v1")

    client.get("/envelope")
    client.get("/")

    assert not (tmp_path / "risk_budget.jsonl").exists()
    assert not (tmp_path / "autonomy_state.jsonl").exists()


def test_enacting_a_lever_change_appends_an_audited_budget_version(
    service: TradeIdeaService, client: TestClient
) -> None:
    response = client.post(
        "/envelope/budget",
        data=_lever_form(service, max_daily_loss_pct="8", reason="Tighten daily loss"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    entries = service.budget_log.history()
    assert entries[-1].budget.version == 2  # seed v1 + enacted v2
    assert entries[-1].budget.max_daily_loss_pct == Decimal("8")
    assert entries[-1].budget.reason == "Tighten daily loss"
    assert entries[-1].actor_type is ActorType.HUMAN
    assert entries[-1].actor_id == "rj"


def test_enacted_change_shows_in_budget_history_diff(
    service: TradeIdeaService, client: TestClient
) -> None:
    client.post(
        "/envelope/budget",
        data=_lever_form(service, max_daily_loss_pct="8", reason="Tighten daily loss"),
        follow_redirects=False,
    )

    response = client.get("/envelope")

    assert response.status_code == 200
    assert "max_daily_loss_pct: 10 → 8" in response.text
    assert "initial version" in response.text


def test_budget_change_requires_a_reason(service: TradeIdeaService, client: TestClient) -> None:
    response = client.post(
        "/envelope/budget",
        data=_lever_form(service, max_daily_loss_pct="8", reason="  "),
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "reason is required" in response.text
    assert service.budget_log.history() == []


def test_stale_form_version_is_refused_and_re_renders_current_levers(
    service: TradeIdeaService, client: TestClient
) -> None:
    stale = _lever_form(service, max_daily_loss_pct="8")
    attest_account_equity(service, equity=Decimal("20000"))  # budget moves to v2

    response = client.post("/envelope/budget", data=stale, follow_redirects=False)

    assert response.status_code == 409
    assert "moved to v2" in response.text
    # The conflict page must show the current levers, not echo the stale
    # submission: resubmitting stale values would silently revert v2.
    assert 'name="max_daily_loss_pct" value="10"' in response.text
    assert 'name="base_version" value="2"' in response.text
    assert service.budget_log.history()[-1].budget.version == 2


def test_non_numeric_lever_re_renders_with_a_field_named_error(
    service: TradeIdeaService, client: TestClient
) -> None:
    response = client.post(
        "/envelope/budget",
        data=_lever_form(service, max_daily_loss_pct="lots"),
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "max_daily_loss_pct must be a decimal number" in response.text
    assert service.budget_log.history() == []


def test_ratchet_entry_renders_with_its_breach_evidence(
    service: TradeIdeaService, client: TestClient
) -> None:
    service.set_autonomy_mode(
        AutonomyMode.BOUNDED_AUTONOMY,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        reason="Stage 2 graduation",
    )
    # Lowering is open to any actor: this is the shape the daily-loss ratchet
    # writes at a decision boundary.
    service.set_autonomy_mode(
        AutonomyMode.HUMAN_APPROVED_EXECUTION,
        actor_type=ActorType.SYSTEM,
        actor_id=RATCHET_ACTOR_ID,
        reason="Automatic ratchet-down: daily loss breached",
        evidence=("same_day_realized_loss_pct=12 exceeds max_daily_loss_pct=10",),
    )

    response = client.get("/envelope")

    assert response.status_code == 200
    assert "ratchet" in response.text
    assert "same_day_realized_loss_pct=12 exceeds max_daily_loss_pct=10" in response.text
    assert "Stage 2 graduation" in response.text


def test_exception_framing_separates_violations_from_inside_envelope(
    service: TradeIdeaService, client: TestClient
) -> None:
    attest_account_equity(service, equity=Decimal("20000"))
    service.propose(
        build_trade_idea(decision_id="trade-20260612-001"),
        actor_id="idea-generator-v1",
    )
    oversized = build_trade_idea(
        decision_id="trade-20260612-002",
        max_loss=MaxLoss(
            amount=Decimal("2000"),
            percent_of_account=Decimal("10"),  # above max_loss_per_idea_pct=5
            assumptions=("Fill at zone midpoint",),
        ),
    )
    service.propose(oversized, actor_id="idea-generator-v1")

    response = client.get("/envelope")

    assert response.status_code == 200
    assert "trade-20260612-002" in response.text
    assert "max_loss_per_idea_pct" in response.text  # violation text on the exception
    assert "Inside the envelope:" in response.text
    assert "trade-20260612-001" in response.text
