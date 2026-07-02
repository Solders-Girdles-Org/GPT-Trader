"""Lane-contract and execution tests for the paper idea executor.

The contract tests pin the structural guarantees from issue #1144: the
paper-only broker boundary and the APPROVED/unexpired admission rule. The
execution tests pin the machine leg built on them: submission recorded before
the broker is touched, decision_id propagated as client_order_id, and fills
recorded only through the reconciler's guarded path.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.core import Order, OrderSide, OrderStatus, OrderType
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.brokerages.paper import HybridPaperBroker
from gpt_trader.features.idea_execution import (
    PAPER_BROKER_TYPES,
    PAPER_EXECUTION_VENUE,
    IdeaNotExecutableError,
    PaperExecutionError,
    PaperIdeaExecutor,
    PaperOnlyLaneError,
)
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
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
    TradeIdeaState,
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


def _rejected_order(symbol: str, client_id: str) -> Order:
    return Order(
        id="MOCK_REJECTED",
        client_id=client_id,
        symbol=symbol,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        price=None,
        stop_price=None,
        tif=None,
        status=OrderStatus.REJECTED,
        filled_quantity=Decimal("0"),
        avg_fill_price=None,
        submitted_at=_NOW,
        updated_at=_NOW,
    )


class TestPaperExecution:
    def _executor(
        self,
        service: TradeIdeaService,
        broker: DeterministicBroker | None = None,
    ) -> PaperIdeaExecutor:
        return PaperIdeaExecutor(
            service,
            broker or DeterministicBroker(),
            now_factory=lambda: _NOW,
        )

    def test_execute_fills_and_audits_full_lifecycle(self, service: TradeIdeaService) -> None:
        decision_id = "trade-20260702-exec-101"
        _approved_idea(service, decision_id)
        broker = DeterministicBroker()
        broker.set_mark("BTC-USD", Decimal("60750"))

        result = self._executor(service, broker).execute(decision_id)

        assert result.final_state == TradeIdeaState.FILLED.value
        assert result.client_order_id == decision_id
        assert result.symbol == "BTC-USD"
        assert result.side == "buy"
        assert result.quantity == Decimal("0.1")
        assert result.fill_price == Decimal("60750")
        assert result.reconciliation.recorded_fill is True

        view = service.get(decision_id)
        assert view.state is TradeIdeaState.FILLED
        submitted = [event for event in view.events if event.action is AuditAction.SUBMITTED]
        filled = [event for event in view.events if event.action is AuditAction.FILLED]
        assert len(submitted) == 1 and len(filled) == 1
        assert submitted[0].actor_type is ActorType.SYSTEM
        assert submitted[0].venue == PAPER_EXECUTION_VENUE
        assert submitted[0].external_order_id == decision_id
        assert filled[0].actor_type is ActorType.VENUE
        assert filled[0].venue == PAPER_EXECUTION_VENUE
        assert filled[0].external_order_id == result.order_id
        # The lifecycle must land on the tamper-evident chain, not around it:
        # verify() raises on any break and returns every chained event.
        chained_event_ids = {event.event_id for event in service.audit_log.verify()}
        assert {event.event_id for event in view.events} <= chained_event_ids

    def test_execute_propagates_decision_id_as_broker_client_id(
        self, service: TradeIdeaService
    ) -> None:
        decision_id = "trade-20260702-exec-102"
        _approved_idea(service, decision_id)
        broker = DeterministicBroker()
        captured: dict[str, object] = {}
        original_place_order = broker.place_order

        def capture_place_order(*args: object, **kwargs: object) -> Order:
            captured.update(kwargs)
            return original_place_order(*args, **kwargs)

        broker.place_order = capture_place_order  # type: ignore[method-assign]
        self._executor(service, broker).execute(decision_id)
        assert captured["client_id"] == decision_id

    def test_execute_refuses_second_attempt(self, service: TradeIdeaService) -> None:
        decision_id = "trade-20260702-exec-103"
        _approved_idea(service, decision_id)
        executor = self._executor(service)
        executor.execute(decision_id)
        with pytest.raises(IdeaNotExecutableError, match="state is filled"):
            executor.execute(decision_id)

    def test_broker_rejection_leaves_idea_submitted(self, service: TradeIdeaService) -> None:
        decision_id = "trade-20260702-exec-104"
        _approved_idea(service, decision_id)
        broker = DeterministicBroker()
        broker.place_order = (  # type: ignore[method-assign]
            lambda *args, **kwargs: _rejected_order("BTC-USD", decision_id)
        )
        with pytest.raises(PaperExecutionError, match="status is REJECTED"):
            self._executor(service, broker).execute(decision_id)
        view = service.get(decision_id)
        assert view.state is TradeIdeaState.SUBMITTED
        assert not any(event.action is AuditAction.FILLED for event in view.events)

    def test_conflicting_fill_payload_is_not_recorded(self, service: TradeIdeaService) -> None:
        decision_id = "trade-20260702-exec-105"
        _approved_idea(service, decision_id)
        broker = DeterministicBroker()
        original_place_order = broker.place_order

        def mangled_place_order(*args: object, **kwargs: object) -> Order:
            return replace(original_place_order(*args, **kwargs), symbol="ETH-USD")

        broker.place_order = mangled_place_order  # type: ignore[method-assign]
        with pytest.raises(PaperExecutionError, match="was not recorded"):
            self._executor(service, broker).execute(decision_id)
        view = service.get(decision_id)
        assert view.state is TradeIdeaState.SUBMITTED
        assert not any(event.action is AuditAction.FILLED for event in view.events)

    def test_execute_refuses_unsizable_idea_before_submission(
        self, service: TradeIdeaService
    ) -> None:
        decision_id = "trade-20260702-exec-106"
        # Notional-only sizing passes the approval budget gate but gives the
        # machine lane no base quantity to place; execution must refuse it
        # before any submission is recorded.
        idea = replace(
            _build_idea(decision_id, expires_at=_NOW + timedelta(days=7)),
            sizing_recommendation=SizingRecommendation(
                notional=Decimal("6075"),
                rationale="notional-only sizing",
            ),
        )
        service.propose(idea, actor_id="test-proposer")
        service.approve(decision_id, actor_id="test-operator", reason="test approval")
        with pytest.raises(IdeaNotExecutableError, match="quantity must be positive"):
            self._executor(service).execute(decision_id)
        view = service.get(decision_id)
        assert view.state is TradeIdeaState.APPROVED
        assert not any(event.action is AuditAction.SUBMITTED for event in view.events)

    def test_execute_refuses_non_directional_idea_before_submission(
        self, service: TradeIdeaService
    ) -> None:
        decision_id = "trade-20260702-exec-107"
        idea = replace(
            _build_idea(decision_id, expires_at=_NOW + timedelta(days=7)),
            direction=TradeDirection.SPREAD,
        )
        service.propose(idea, actor_id="test-proposer")
        service.approve(decision_id, actor_id="test-operator", reason="test approval")
        with pytest.raises(IdeaNotExecutableError, match="direction must be long or short"):
            self._executor(service).execute(decision_id)
        assert service.get(decision_id).state is TradeIdeaState.APPROVED

    def test_execute_short_idea_places_sell_order(self, service: TradeIdeaService) -> None:
        decision_id = "trade-20260702-exec-108"
        service.update_budget(
            replace(
                service.current_budget(),
                version=3,
                allow_naked_shorts=True,
                reason="test: allow short idea",
            ),
            actor_type=ActorType.HUMAN,
            actor_id="test-operator",
        )
        idea = replace(
            _build_idea(decision_id, expires_at=_NOW + timedelta(days=7)),
            direction=TradeDirection.SHORT,
        )
        service.propose(idea, actor_id="test-proposer")
        service.approve(decision_id, actor_id="test-operator", reason="test approval")
        result = self._executor(service).execute(decision_id)
        assert result.side == "sell"
        assert result.final_state == TradeIdeaState.FILLED.value
