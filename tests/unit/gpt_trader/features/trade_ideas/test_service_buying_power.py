"""Service-level cash-account buying-power aggregation and enforcement (#1231)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.features.trade_ideas import (
    ActorType,
    CloseoutResolution,
    PolicyViolationError,
    SizingRecommendation,
    TradeIdea,
    TradeIdeaState,
)
from gpt_trader.features.trade_ideas.service import TradeIdeaService


def configure_buying_power(
    service: TradeIdeaService,
    pct: Decimal | None = Decimal("100"),
) -> None:
    """Version the cash-account buying-power lever onto the current budget."""
    current = service.current_budget()
    service.update_budget(
        replace(
            current,
            version=current.version + 1,
            max_equity_buying_power_pct=pct,
            reason="Configure cash-account buying power for equities",
        ),
        ActorType.HUMAN,
        "rj",
    )


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(
        tmp_path / "trade_ideas",
        now_factory=lambda: datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )
    attest_account_equity(built, equity=Decimal("10000"))
    configure_buying_power(built)
    return built


def equity_idea(decision_id: str, instrument: str, **overrides: Any) -> TradeIdea:
    fields: dict[str, Any] = {
        "decision_id": decision_id,
        "instrument": instrument,
        "thesis": "Earnings momentum with sector breadth confirmation",
        "data_used": (f"provider:candles:{instrument}:1d:2026-06-11",),
    }
    fields.update(overrides)
    return build_trade_idea(**fields)


def test_open_equity_exposure_blocks_a_second_equity_idea(
    service: TradeIdeaService,
) -> None:
    first = equity_idea("trade-20260612-open-aapl", "AAPL")
    service.propose(first, actor_id="idea-generator-v1")
    service.approve(first.decision_id, actor_id="rj", reason="Risk verified")
    candidate = equity_idea("trade-20260612-candidate-msft", "MSFT")
    service.propose(candidate, actor_id="idea-generator-v1")

    # 6075 open + 6075 candidate = 121.5% of the attested 10000 equity: the
    # configured 100% cash-account lever refuses it beside the notional check.
    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "max_equity_buying_power_pct budget breached" in violation
        and "open_equity_notional=6075" in violation
        for violation in exc_info.value.violations
    )
    assert service.get(candidate.decision_id).state is TradeIdeaState.PROPOSED


def test_same_day_equity_sale_proceeds_are_unsettled_buying_power(
    service: TradeIdeaService,
) -> None:
    closed = equity_idea("trade-20260612-closed-aapl", "AAPL")
    service.propose(closed, actor_id="idea-generator-v1")
    service.approve(closed.decision_id, actor_id="rj", reason="Risk verified")
    service.record_submission(closed.decision_id, actor_id="executor", venue="manual")
    service.record_fill(
        closed.decision_id,
        actor_id="executor",
        venue="manual",
        external_order_id="paper-1",
    )
    service.record_closeout_attribution(
        closed.decision_id,
        actor_id="rj",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=Decimal("100"),
        evidence=("paper-statement:paper-1",),
    )
    candidate = equity_idea("trade-20260612-candidate-msft", "MSFT")
    service.propose(candidate, actor_id="idea-generator-v1")

    context = service.approval_budget_context(exclude_decision_id=candidate.decision_id)
    # T+1: today's sale proceeds (6075 entry + 100 realized) are not settled
    # cash yet, so the 6075 candidate cannot be funded from 10000 equity.
    assert context.unsettled_equity_proceeds == Decimal("6175")
    assert context.open_equity_notional == Decimal("0")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "max_equity_buying_power_pct budget breached" in violation
        and "unsettled_equity_proceeds=6175" in violation
        for violation in exc_info.value.violations
    )


def test_crypto_exposure_never_counts_toward_equity_buying_power(
    service: TradeIdeaService,
) -> None:
    crypto = build_trade_idea(decision_id="trade-20260612-open-btc")
    service.propose(crypto, actor_id="idea-generator-v1")
    service.approve(crypto.decision_id, actor_id="rj", reason="Risk verified")

    context = service.approval_budget_context()

    assert context.open_notional == Decimal("6075")
    assert context.open_equity_notional == Decimal("0")
    assert context.open_equity_notional_unavailable_count == 0
    assert context.unsettled_equity_proceeds == Decimal("0")


def test_crypto_approval_is_unchanged_with_the_lever_configured(
    service: TradeIdeaService,
) -> None:
    # The fixture configures max_equity_buying_power_pct=100; a crypto-spot
    # idea inside the notional cap must approve exactly as before #1231.
    crypto = build_trade_idea(decision_id="trade-20260612-btc-approves")
    service.propose(crypto, actor_id="idea-generator-v1")

    approved = service.approve(crypto.decision_id, actor_id="rj", reason="Risk verified")

    assert approved.state is TradeIdeaState.APPROVED


def test_equity_idea_without_notional_fails_closed_on_buying_power(
    service: TradeIdeaService,
) -> None:
    candidate = equity_idea(
        "trade-20260612-no-notional",
        "AAPL",
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("10"),
            notional=None,
            rationale="Missing notional cannot prove buying-power compliance",
        ),
    )
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "sizing_recommendation.notional is required to verify "
        "max_equity_buying_power_pct budget exposure" in violation
        for violation in exc_info.value.violations
    )


def test_open_idea_with_unclassifiable_instrument_counts_unavailable(
    service: TradeIdeaService,
) -> None:
    # An unclassifiable instrument approved before the lever was configured
    # (lever set to None, the pre-#1231 posture) must poison later equity
    # buying-power verification instead of being skipped silently.
    lever_off = service.current_budget()
    service.update_budget(
        replace(
            lever_off,
            version=lever_off.version + 1,
            max_equity_buying_power_pct=None,
            reason="Model a budget log from before the buying-power lever",
        ),
        ActorType.HUMAN,
        "rj",
    )
    odd = build_trade_idea(
        decision_id="trade-20260612-odd-instrument",
        instrument="BRK.B",
        data_used=("provider:candles:BRK.B:1d:2026-06-11",),
    )
    service.propose(odd, actor_id="idea-generator-v1")
    service.approve(odd.decision_id, actor_id="rj", reason="Risk verified")
    lever_on = service.current_budget()
    service.update_budget(
        replace(
            lever_on,
            version=lever_on.version + 1,
            max_equity_buying_power_pct=Decimal("100"),
            reason="Configure cash-account buying power for equities",
        ),
        ActorType.HUMAN,
        "rj",
    )

    context = service.approval_budget_context()

    assert context.open_equity_notional_unavailable_count == 1

    candidate = equity_idea("trade-20260612-candidate-msft", "MSFT")
    service.propose(candidate, actor_id="idea-generator-v1")

    with pytest.raises(PolicyViolationError) as exc_info:
        service.approve(candidate.decision_id, actor_id="rj", reason="Risk verified")

    assert any(
        "equity notional cannot be verified" in violation for violation in exc_info.value.violations
    )
