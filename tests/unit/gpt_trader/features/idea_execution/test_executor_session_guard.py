"""Session admission for the paper executor lane (issue #1232).

``GUARD_NOW`` (Thursday 2026-07-02 12:00 UTC = 08:00 ET) is a closed XNYS
instant: the guard must refuse an equity idea there, admit it during the
session (15:00 UTC = 11:00 ET the same day), and never constrain crypto —
the 24x7 session has no closed instants. Shares the lane fixtures pinned in
test_executor.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.idea_execution.test_executor import (
    _NOW as GUARD_NOW,
)
from tests.unit.gpt_trader.features.idea_execution.test_executor import (
    _approved_idea,
    _build_idea,
)

from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import IdeaNotExecutableError, PaperIdeaExecutor
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    TradeIdeaService,
    TradeIdeaState,
)


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    trade_idea_service = TradeIdeaService(tmp_path, now_factory=lambda: GUARD_NOW)
    trade_idea_service.update_budget(
        replace(
            DEFAULT_RISK_BUDGET,
            version=2,
            account_equity=Decimal("25000"),
            reason="test: attest scratch equity",
        ),
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
    )
    return trade_idea_service


def _executor(service: TradeIdeaService, *, now: datetime = GUARD_NOW) -> PaperIdeaExecutor:
    return PaperIdeaExecutor(service, DeterministicBroker(), now_factory=lambda: now)


def _approved_equity_idea(service: TradeIdeaService, decision_id: str) -> None:
    idea = replace(
        _build_idea(decision_id, expires_at=GUARD_NOW + timedelta(days=7)),
        instrument="AAPL",
    )
    service.propose(idea, actor_id="test-proposer")
    service.approve(decision_id, actor_id="test-operator", reason="test approval")


def test_refuses_equity_idea_while_xnys_is_closed(service: TradeIdeaService) -> None:
    decision_id = "trade-20260702-exec-201"
    _approved_equity_idea(service, decision_id)
    with pytest.raises(IdeaNotExecutableError, match="market closed for session XNYS"):
        _executor(service).execute(decision_id)
    # The refusal names the resumption point and leaves no submission behind:
    # the idea stays APPROVED and fills at the next open against that turn's
    # own marks (the gap is attributed, never smoothed).
    with pytest.raises(IdeaNotExecutableError, match="next open 2026-07-02T13:30:00"):
        _executor(service).resolve_approved_idea(decision_id)
    view = service.get(decision_id)
    assert view.state is TradeIdeaState.APPROVED
    assert not any(event.action is AuditAction.SUBMITTED for event in view.events)


def test_executes_equity_idea_during_the_session(service: TradeIdeaService) -> None:
    decision_id = "trade-20260702-exec-202"
    _approved_equity_idea(service, decision_id)
    session_now = datetime(2026, 7, 2, 15, 0, tzinfo=UTC)
    result = _executor(service, now=session_now).execute(decision_id)
    assert result.final_state == TradeIdeaState.FILLED.value
    assert result.symbol == "AAPL"


def test_crypto_idea_executes_on_a_weekend(service: TradeIdeaService) -> None:
    decision_id = "trade-20260702-exec-203"
    _approved_idea(service, decision_id)
    weekend = datetime(2026, 7, 4, 15, 0, tzinfo=UTC)  # Saturday
    result = _executor(service, now=weekend).execute(decision_id)
    assert result.final_state == TradeIdeaState.FILLED.value


def test_refuses_unclassifiable_instrument(service: TradeIdeaService) -> None:
    decision_id = "trade-20260702-exec-204"
    idea = replace(
        _build_idea(decision_id, expires_at=GUARD_NOW + timedelta(days=7)),
        instrument="BTC-USD-PERP",
    )
    service.propose(idea, actor_id="test-proposer")
    service.approve(decision_id, actor_id="test-operator", reason="test approval")
    with pytest.raises(IdeaNotExecutableError, match="not classifiable"):
        _executor(service).execute(decision_id)
    assert service.get(decision_id).state is TradeIdeaState.APPROVED


def test_refuses_calendar_out_of_bounds_as_typed_error(service: TradeIdeaService) -> None:
    decision_id = "trade-20260702-exec-205"
    _approved_equity_idea(service, decision_id)
    historical_now = datetime(1980, 1, 2, 15, 0, tzinfo=UTC)

    with pytest.raises(IdeaNotExecutableError, match="calendar XNYS cannot evaluate"):
        _executor(service, now=historical_now).execute(decision_id)

    assert service.get(decision_id).state is TradeIdeaState.APPROVED
