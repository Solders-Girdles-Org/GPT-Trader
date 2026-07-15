"""Fill-evidence and unresolved-cause behavior of the exit monitor (#1212).

SHORT-direction exits, permanently unscoreable ideas, expired fills without
candles, and corrupt fill evidence: each must either resolve from the recorded
fill facts or explain itself on the pass's ``unresolved`` record instead of
being silently retried forever. Shared builders live in conftest.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from tests.unit.gpt_trader.features.idea_execution.conftest import (
    EXIT_CLOCK as CLOCK,
)
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    EXIT_QUANTITY as QUANTITY,
)
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    exit_candle as _candle,
)
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    exit_snapshot as _snapshot,
)
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    fill_exit_idea as _fill_idea,
)
from tests.unit.gpt_trader.features.trade_ideas.conftest import build_trade_idea

from gpt_trader.features.idea_execution import resolve_filled_ideas
from gpt_trader.features.trade_ideas import (
    ActorType,
    CloseoutResolution,
    EntryZone,
    ExitPlan,
    SizingRecommendation,
    TimeHorizon,
    TradeDirection,
    TradeIdeaService,
    encode_fill_evidence,
)


def _fill_short_idea(service: TradeIdeaService, *, fill_evidence: tuple[str, ...] = ()) -> None:
    budget = service.current_budget()
    service.update_budget(
        replace(
            budget,
            version=budget.version + 1,
            allow_naked_shorts=True,
            reason="test: allow shorts",
        ),
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
    )
    idea = build_trade_idea(
        decision_id="trade-20260612-001",
        direction=TradeDirection.SHORT,
        entry_zone=EntryZone(lower=Decimal("100"), upper=Decimal("102")),
        invalidation="Close above 107",
        target_exit="Take profit at 90 or exit at expiry",
        exit_plan=ExitPlan(stop=Decimal("107"), target=Decimal("90")),
        sizing_recommendation=SizingRecommendation(
            quantity=QUANTITY, notional=Decimal("10.1"), rationale="test"
        ),
        time_horizon=TimeHorizon(expected_hold="1-4h", expires_at=CLOCK + timedelta(hours=4)),
    )
    service.propose(idea, actor_id="proposer")
    service.approve("trade-20260612-001", actor_id="rj", reason="verified")
    service.record_submission("trade-20260612-001", actor_id="executor", venue="coinbase")
    service.record_fill(
        "trade-20260612-001",
        actor_id="coinbase",
        venue="coinbase",
        evidence=fill_evidence,
    )


def test_short_fill_resolves_target_with_positive_pnl(service: TradeIdeaService) -> None:
    """SHORT exits invert both the touch sense and the P&L sign."""
    _fill_short_idea(
        service,
        fill_evidence=encode_fill_evidence(
            price=Decimal("105"), quantity=QUANTITY, filled_at=CLOCK
        ),
    )
    snapshot = _snapshot(
        _candle(0, high="106", low="103", close="104"),
        _candle(1, high="105", low="89", close="91"),  # touches target 90 from above
    )

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)).recorded

    assert closeout.resolution is CloseoutResolution.THESIS_TARGET
    # short entry 105, exit 90, qty 0.1 -> +1.5
    assert closeout.realized_profit_loss_amount == Decimal("1.5")


def test_short_fill_resolves_stop_with_negative_pnl(service: TradeIdeaService) -> None:
    _fill_short_idea(
        service,
        fill_evidence=encode_fill_evidence(
            price=Decimal("105"), quantity=QUANTITY, filled_at=CLOCK
        ),
    )
    snapshot = _snapshot(
        _candle(0, high="106", low="103", close="104"),
        _candle(1, high="108", low="103", close="107"),  # breaches stop 107 above entry
    )

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)).recorded

    assert closeout.resolution is CloseoutResolution.INVALIDATION
    # short entry 105, exit 107, qty 0.1 -> -0.2
    assert closeout.realized_profit_loss_amount == Decimal("-0.2")


def test_unscoreable_exit_levels_surface_as_unresolved(service: TradeIdeaService) -> None:
    """A permanently unscoreable idea must not be silently retried forever.

    Level extraction failures are properties of the idea (no numeric stop or
    target anywhere), so no later turn can succeed; the pass must say so
    instead of swallowing the error (#1212 curation finding).
    """
    idea = build_trade_idea(
        decision_id="trade-20260612-001",
        entry_zone=EntryZone(lower=Decimal("100"), upper=Decimal("102")),
        invalidation="close on vibes",
        target_exit="exit when it feels right",
        exit_plan=None,
        sizing_recommendation=SizingRecommendation(
            quantity=QUANTITY, notional=Decimal("10.1"), rationale="test"
        ),
        time_horizon=TimeHorizon(expected_hold="1-4h", expires_at=CLOCK + timedelta(hours=4)),
    )
    service.propose(idea, actor_id="proposer")
    service.approve("trade-20260612-001", actor_id="rj", reason="verified")
    service.record_submission("trade-20260612-001", actor_id="executor", venue="coinbase")
    service.record_fill("trade-20260612-001", actor_id="coinbase", venue="coinbase")
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        _candle(1, high="114", low="101", close="113"),
    )

    monitor_pass = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2))

    assert monitor_pass.recorded == ()
    (entry,) = monitor_pass.unresolved
    assert entry["decision_id"] == "trade-20260612-001"
    assert "not scoreable" in entry["reason"]


def test_expired_fill_without_candles_surfaces_as_unresolved(service: TradeIdeaService) -> None:
    """Past expiry, every non-closing pass must explain itself on the record."""
    _fill_idea(service)
    other_symbol_only = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        symbol="ETH-USD",
    )

    # While the position is inside its horizon, waiting quietly is normal.
    open_pass = resolve_filled_ideas(service, other_symbol_only, now=CLOCK + timedelta(hours=2))
    assert open_pass.unresolved == ()

    # Once expired, the missing candles are an evidence failure, not a wait.
    expired_pass = resolve_filled_ideas(service, other_symbol_only, now=CLOCK + timedelta(hours=5))
    assert expired_pass.recorded == ()
    (entry,) = expired_pass.unresolved
    assert entry["decision_id"] == "trade-20260612-001"
    assert "no candles" in entry["reason"]


def test_corrupt_fill_price_evidence_is_disclosed_on_the_closeout(
    service: TradeIdeaService,
) -> None:
    """Destroyed evidence must never masquerade as a by-design estimate."""
    _fill_idea(service, fill_evidence=("fill_price=12.3.4", f"fill_quantity={QUANTITY}"))
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        _candle(1, high="114", low="101", close="113"),  # hits target 113
    )

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)).recorded

    assert closeout.resolution is CloseoutResolution.THESIS_TARGET
    assert "entry_price_source=planned_zone_midpoint" in closeout.evidence
    assert "evidence_corrupt_keys=fill_price" in closeout.evidence
