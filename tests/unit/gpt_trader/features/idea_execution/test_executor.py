"""Lane-contract tests for the paper idea executor skeleton.

These pin the structural guarantees from issue #1144 ahead of any execution
logic: the paper-only broker boundary and the APPROVED/unexpired admission
rule. Later execution PRs build on top of these tests, not around them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.brokerages.paper import HybridPaperBroker
from gpt_trader.features.idea_execution import (
    PAPER_BROKER_TYPES,
    IdeaNotExecutableError,
    PaperIdeaExecutor,
    PaperOnlyLaneError,
)
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AutonomyMode,
    Confidence,
    ConfidenceLabel,
    EntryZone,
    MaxLoss,
    ProductType,
    SizingRecommendation,
    TimeHorizon,
    TradeDirection,
    TradeIdea,
    TradeIdeaService,
    UnknownTradeIdeaError,
)

_NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def _build_idea(decision_id: str, *, expires_at: datetime) -> TradeIdea:
    return TradeIdea(
        decision_id=decision_id,
        autonomy_mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
        thesis="Executor lane test: eligible fixture record",
        instrument="BTC-USD",
        product_type=ProductType.SPOT,
        direction=TradeDirection.LONG,
        entry_zone=EntryZone(lower=Decimal("60000"), upper=Decimal("61500")),
        invalidation="Daily close below 58000",
        target_exit="Take profit at 67000 or exit after 10 trading days",
        max_loss=MaxLoss(
            amount=Decimal("250"),
            percent_of_account=Decimal("1.5"),
            assumptions=("Fill at zone midpoint",),
        ),
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("0.1"),
            notional=Decimal("6075"),
            rationale="Fixture sizing",
        ),
        time_horizon=TimeHorizon(expected_hold="3-10 days", expires_at=expires_at),
        data_used=("test:fixture:no-market-data",),
        confidence=Confidence(label=ConfidenceLabel.MEDIUM, rationale="fixture"),
        failure_mode="Not applicable in tests",
        do_not_trade_if=("fixture record",),
    )


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    trade_idea_service = TradeIdeaService(tmp_path, now_factory=lambda: _NOW)
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


def _approved_idea(service: TradeIdeaService, decision_id: str) -> None:
    idea = _build_idea(decision_id, expires_at=_NOW + timedelta(days=7))
    service.propose(idea, actor_id="test-proposer")
    service.approve(decision_id, actor_id="test-operator", reason="test approval")


class TestPaperOnlyBrokerBoundary:
    def test_accepts_deterministic_broker(self, service: TradeIdeaService) -> None:
        broker = DeterministicBroker()
        executor = PaperIdeaExecutor(service, broker)
        assert executor.broker is broker

    def test_accepts_hybrid_paper_broker(self, service: TradeIdeaService) -> None:
        broker = HybridPaperBroker(client=object())  # type: ignore[arg-type]
        executor = PaperIdeaExecutor(service, broker)
        assert executor.broker is broker

    def test_rejects_duck_typed_lookalike(self, service: TradeIdeaService) -> None:
        class LooksLikeABroker:
            """Implements broker-shaped methods but is not an allowed type."""

            def place_order(self, *args: object, **kwargs: object) -> None: ...

            def list_positions(self) -> list[object]:
                return []

            def list_balances(self) -> list[object]:
                return []

        with pytest.raises(PaperOnlyLaneError):
            PaperIdeaExecutor(service, LooksLikeABroker())  # type: ignore[arg-type]

    def test_rejects_subclass_of_allowed_broker(self, service: TradeIdeaService) -> None:
        class SneakyBroker(DeterministicBroker):
            """A subclass could reroute fills; exact-type matching refuses it."""

        with pytest.raises(PaperOnlyLaneError):
            PaperIdeaExecutor(service, SneakyBroker())

    def test_allowlist_is_exactly_the_two_paper_brokers(self) -> None:
        assert set(PAPER_BROKER_TYPES) == {DeterministicBroker, HybridPaperBroker}


class TestApprovedIdeaAdmission:
    def _executor(
        self,
        service: TradeIdeaService,
        *,
        now: datetime = _NOW,
    ) -> PaperIdeaExecutor:
        return PaperIdeaExecutor(service, DeterministicBroker(), now_factory=lambda: now)

    def test_admits_approved_unexpired_idea(self, service: TradeIdeaService) -> None:
        _approved_idea(service, "trade-20260702-exec-001")
        view = self._executor(service).resolve_approved_idea("trade-20260702-exec-001")
        assert view.idea.decision_id == "trade-20260702-exec-001"

    def test_refuses_proposed_idea(self, service: TradeIdeaService) -> None:
        idea = _build_idea("trade-20260702-exec-002", expires_at=_NOW + timedelta(days=7))
        service.propose(idea, actor_id="test-proposer")
        with pytest.raises(IdeaNotExecutableError, match="state is proposed"):
            self._executor(service).resolve_approved_idea("trade-20260702-exec-002")

    def test_refuses_rejected_idea(self, service: TradeIdeaService) -> None:
        idea = _build_idea("trade-20260702-exec-003", expires_at=_NOW + timedelta(days=7))
        service.propose(idea, actor_id="test-proposer")
        service.reject("trade-20260702-exec-003", actor_id="test-operator", reason="no")
        with pytest.raises(IdeaNotExecutableError, match="state is rejected"):
            self._executor(service).resolve_approved_idea("trade-20260702-exec-003")

    def test_refuses_submitted_idea_to_prevent_double_execution(
        self, service: TradeIdeaService
    ) -> None:
        _approved_idea(service, "trade-20260702-exec-004")
        service.record_submission(
            "trade-20260702-exec-004",
            actor_type=ActorType.HUMAN,
            actor_id="test-operator",
            venue="manual",
            external_order_id="EXT-1",
            reason="test submission",
        )
        with pytest.raises(IdeaNotExecutableError, match="state is submitted"):
            self._executor(service).resolve_approved_idea("trade-20260702-exec-004")

    def test_refuses_expired_approved_idea(self, service: TradeIdeaService) -> None:
        _approved_idea(service, "trade-20260702-exec-005")
        after_expiry = _NOW + timedelta(days=8)
        with pytest.raises(IdeaNotExecutableError, match="expired at"):
            self._executor(service, now=after_expiry).resolve_approved_idea(
                "trade-20260702-exec-005"
            )

    def test_missing_idea_error_propagates(self, service: TradeIdeaService) -> None:
        with pytest.raises(UnknownTradeIdeaError, match="trade-20260702-exec-404"):
            self._executor(service).resolve_approved_idea("trade-20260702-exec-404")
