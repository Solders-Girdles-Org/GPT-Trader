from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader.features.trade_ideas import (
    ActorType,
    AutonomyMode,
    Confidence,
    ConfidenceLabel,
    EntryZone,
    MaxLoss,
    PaperFillEvent,
    ProductType,
    SizingRecommendation,
    TimeHorizon,
    TradeDirection,
    TradeIdea,
    TradeIdeaService,
)


def build_trade_idea(**overrides: Any) -> TradeIdea:
    """Build a fully-populated, eligible trade idea; override fields per test."""
    fields: dict[str, Any] = {
        "decision_id": "trade-20260612-001",
        "autonomy_mode": AutonomyMode.HUMAN_APPROVED_EXECUTION,
        "thesis": "BTC reclaiming the 50-day average with rising spot volume",
        "instrument": "BTC-USD",
        "product_type": ProductType.SPOT,
        "direction": TradeDirection.LONG,
        "entry_zone": EntryZone(lower=Decimal("60000"), upper=Decimal("61500")),
        "invalidation": "Daily close below 58000",
        "target_exit": "Take profit at 67000 or exit after 10 trading days",
        "max_loss": MaxLoss(
            amount=Decimal("250"),
            percent_of_account=Decimal("1.5"),
            assumptions=("Fill at zone midpoint", "No slippage beyond 10 bps"),
        ),
        "sizing_recommendation": SizingRecommendation(
            quantity=Decimal("0.1"),
            notional=Decimal("6075"),
            rationale="Half-Kelly on backtested edge",
        ),
        "time_horizon": TimeHorizon(
            expected_hold="3-10 days",
            expires_at=datetime(2026, 6, 19, 16, 0, tzinfo=UTC),
        ),
        "data_used": ("coinbase:candles:BTC-USD:1d:2026-06-11",),
        "confidence": Confidence(
            label=ConfidenceLabel.MEDIUM,
            rationale="Volume confirmation present, macro calendar risk this week",
        ),
        "failure_mode": "False breakout into a macro-driven selloff",
        "do_not_trade_if": ("FOMC announcement within 24 hours",),
    }
    fields.update(overrides)
    return TradeIdea(**fields)


@pytest.fixture
def trade_idea() -> TradeIdea:
    return build_trade_idea()


def reconciliation_service(root: Path) -> TradeIdeaService:
    """Trade-idea service with a frozen clock for reconciliation tests."""
    return TradeIdeaService(
        root,
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )


def attest_account_equity(
    service: TradeIdeaService,
    equity: Decimal = Decimal("20000"),
) -> None:
    """Record operator-attested equity so the notional approval gate can verify."""
    current = service.current_budget()
    if current.account_equity is not None:
        return
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            account_equity=equity,
            reason="Operator-attested equity for tests",
        ),
        ActorType.HUMAN,
        "rj",
    )


def approved_idea(
    service: TradeIdeaService,
    *,
    decision_id: str = "trade-20260612-001",
) -> str:
    """Propose and approve a default idea, returning its decision id."""
    attest_account_equity(service)
    idea = build_trade_idea(decision_id=decision_id)
    service.propose(idea, actor_id="idea-generator-v1")
    service.approve(idea.decision_id, actor_id="rj", reason="Risk verified")
    return idea.decision_id


def submitted_idea(
    service: TradeIdeaService,
    *,
    decision_id: str = "trade-20260612-001",
    external_order_id: str = "ORDER_A",
) -> str:
    """Approve an idea and record a submission under ``external_order_id``."""
    decision = approved_idea(service, decision_id=decision_id)
    service.record_submission(
        decision,
        actor_id="paper-fill-reconciler",
        venue="manual",
        external_order_id=external_order_id,
        reason="submitted for reconciliation tests",
        actor_type=ActorType.SYSTEM,
    )
    return decision


def paper_fill_event(
    *,
    decision_id: str | None = None,
    client_order_id: str = "",
    order_id: str = "MOCK_000001",
    symbol: str = "BTC-USD",
    side: str = "buy",
) -> PaperFillEvent:
    """Build a normalized filled paper/mock event for reconciliation tests."""
    return PaperFillEvent(
        order_id=order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal("0.1"),
        price=Decimal("60750"),
        status="filled",
        decision_id=decision_id,
    )
