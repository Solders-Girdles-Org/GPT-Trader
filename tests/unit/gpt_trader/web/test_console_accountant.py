"""Accountant page: paper equity ledger and budget levers vs usage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpt_trader.features.trade_ideas.audit import ActorType
from gpt_trader.features.trade_ideas.closeout import CloseoutResolution
from gpt_trader.features.trade_ideas.service import TradeIdeaService
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


def _record_closed_trade(service: TradeIdeaService, amount: Decimal) -> None:
    attest_account_equity(service, equity=Decimal("20000"))
    service.propose(build_trade_idea(), actor_id="idea-generator-v1")
    service.approve(_DECISION_ID, actor_id="rj", reason="Risk verified")
    service.record_submission(_DECISION_ID, actor_id="paper-cycle", venue="paper")
    service.record_fill(_DECISION_ID, actor_id="paper-broker", venue="paper")
    service.record_closeout_attribution(
        _DECISION_ID,
        actor_id="rj",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=amount,
    )


def test_accountant_renders_equity_ledger_from_closeouts(
    service: TradeIdeaService, client: TestClient
) -> None:
    _record_closed_trade(service, Decimal("150"))

    response = client.get("/accountant")

    assert response.status_code == 200
    assert "$20,150.00" in response.text  # attested 20000 + realized 150
    assert "attested $20,000.00 by rj" in response.text
    assert "High-water mark" in response.text


def test_accountant_shows_drawdown_from_peak(service: TradeIdeaService, client: TestClient) -> None:
    _record_closed_trade(service, Decimal("-400"))

    response = client.get("/accountant")

    assert response.status_code == 200
    assert "$19,600.00" in response.text
    assert "drawdown $400.00 (2.00%)" in response.text
    assert "-$400.00" in response.text  # realized P&L since attestation


def test_accountant_renders_levers_vs_usage(service: TradeIdeaService, client: TestClient) -> None:
    attest_account_equity(service, equity=Decimal("20000"))

    response = client.get("/accountant")

    assert response.status_code == 200
    assert "Budget levers vs usage" in response.text
    assert "Daily loss" in response.text
    assert "Concurrent approved tickets" in response.text


def test_accountant_renders_without_any_attestation(client: TestClient) -> None:
    response = client.get("/accountant")

    assert response.status_code == 200
    assert "no attested equity yet" in response.text
    assert "incomplete:" not in response.text


def test_console_reads_do_not_seed_the_budget_log(tmp_path: Path, client: TestClient) -> None:
    # Rendering any console page on a fresh root must not create durable
    # artifacts; the budget log is seeded by decision paths, never by a GET.
    client.get("/accountant")
    client.get("/")

    assert not (tmp_path / "risk_budget.jsonl").exists()


def test_filled_trade_spanning_a_reattestation_keeps_its_realized_pnl(tmp_path: Path) -> None:
    # Fill at T1, re-attest while the position is still open at T2, close and
    # attribute at T3: the FILLED audit event is the entry fill, not the
    # close, so the closeout must fold at attribution time (after the
    # re-attestation), never get sorted before it and dropped.
    clock = {"now": _NOW}
    service = TradeIdeaService(tmp_path, now_factory=lambda: clock["now"])
    client = TestClient(create_app(service=service, actor_id="rj"))

    attest_account_equity(service, equity=Decimal("20000"))
    service.propose(build_trade_idea(), actor_id="idea-generator-v1")
    service.approve(_DECISION_ID, actor_id="rj", reason="Risk verified")
    clock["now"] = _NOW + timedelta(hours=1)
    service.record_submission(_DECISION_ID, actor_id="paper-cycle", venue="paper")
    service.record_fill(_DECISION_ID, actor_id="paper-broker", venue="paper")
    clock["now"] = _NOW + timedelta(hours=2)
    current = service.current_budget()
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            account_equity=Decimal("19000"),
            reason="Re-attested while position open",
        ),
        ActorType.HUMAN,
        "rj",
    )
    clock["now"] = _NOW + timedelta(hours=3)
    service.record_closeout_attribution(
        _DECISION_ID,
        actor_id="rj",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=Decimal("500"),
    )

    response = client.get("/accountant")

    assert response.status_code == 200
    assert "$19,500.00" in response.text  # 19000 re-attested + 500 realized after


def test_accountant_marks_daily_loss_usage_incomplete_when_evidence_is_missing(
    service: TradeIdeaService, client: TestClient
) -> None:
    # A same-day closeout without any P&L evidence cannot be priced into
    # daily-loss usage; the subset total must be marked incomplete, not exact.
    attest_account_equity(service, equity=Decimal("20000"))
    service.propose(build_trade_idea(), actor_id="idea-generator-v1")
    service.approve(_DECISION_ID, actor_id="rj", reason="Risk verified")
    service.record_submission(_DECISION_ID, actor_id="paper-cycle", venue="paper")
    service.record_fill(_DECISION_ID, actor_id="paper-broker", venue="paper")
    service.record_closeout_attribution(
        _DECISION_ID,
        actor_id="rj",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_unavailable_reason="fill evidence missing",
    )

    response = client.get("/accountant")

    assert response.status_code == 200
    assert "incomplete: 1 record without loss evidence" in response.text
