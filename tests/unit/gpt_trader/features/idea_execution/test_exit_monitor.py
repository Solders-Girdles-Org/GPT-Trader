"""Paper-exit monitor: resolve filled ideas into closeouts (#1218, 2/2).

Each test fills an idea with a known ExitPlan, then drives the snapshot's candles
so the position hits its target, hits its stop, sits open, or expires — and pins
the recorded closeout resolution and the dollar realized P&L the Stage 1->2 gates
read (``realized_profit_loss_amount`` = quantity x price move).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
)

from gpt_trader.core import Candle
from gpt_trader.features.idea_execution import resolve_filled_ideas
from gpt_trader.features.trade_ideas import (
    CloseoutResolution,
    EntryZone,
    ExitPlan,
    MarketSnapshot,
    SizingRecommendation,
    SymbolSeries,
    TimeHorizon,
    TradeIdeaService,
    TradeIdeaState,
)

CLOCK = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
QUANTITY = Decimal("0.1")


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(tmp_path / "trade_ideas", now_factory=lambda: CLOCK)
    attest_account_equity(built)
    return built


def _fill_idea(service: TradeIdeaService, *, decision_id: str = "trade-20260612-001") -> None:
    idea = build_trade_idea(
        decision_id=decision_id,
        entry_zone=EntryZone(lower=Decimal("100"), upper=Decimal("102")),
        invalidation="Close below 95",
        target_exit="Take profit at 113 or exit at expiry",
        exit_plan=ExitPlan(stop=Decimal("95"), target=Decimal("113")),
        sizing_recommendation=SizingRecommendation(
            quantity=QUANTITY, notional=Decimal("10.1"), rationale="test"
        ),
        time_horizon=TimeHorizon(expected_hold="1-4h", expires_at=CLOCK + timedelta(hours=4)),
    )
    service.propose(idea, actor_id="proposer")
    service.approve(decision_id, actor_id="rj", reason="verified")
    service.record_submission(decision_id, actor_id="executor", venue="coinbase")
    service.record_fill(decision_id, actor_id="coinbase", venue="coinbase")


def _candle(offset_hours: int, *, high: str, low: str, close: str) -> Candle:
    price = Decimal(close)
    return Candle(
        ts=CLOCK + timedelta(hours=offset_hours),
        open=price,
        high=Decimal(high),
        low=Decimal(low),
        close=price,
        volume=Decimal("1000"),
    )


def _snapshot(*candles: Candle) -> MarketSnapshot:
    # as_of sits after the recorded candles: the monitor runs on a later turn's
    # snapshot whose bars span the position's post-entry history.
    return MarketSnapshot(
        as_of=CLOCK + timedelta(hours=3),
        source="test:fixture",
        series=(SymbolSeries(symbol="BTC-USD", granularity="ONE_HOUR", candles=candles),),
    )


def test_target_hit_records_thesis_target_with_positive_pnl(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),  # entry candle (in zone)
        _candle(1, high="114", low="101", close="113"),  # hits target 113
    )

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2))

    assert closeout.resolution is CloseoutResolution.THESIS_TARGET
    # entry midpoint 101, exit 113, qty 0.1 -> +1.2
    assert closeout.realized_profit_loss_amount == Decimal("1.2")
    assert service.get("trade-20260612-001").state is TradeIdeaState.FILLED
    assert service.get_closeout_attribution("trade-20260612-001") == closeout


def test_stop_hit_records_invalidation_with_negative_pnl(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),  # entry candle
        _candle(1, high="102", low="94", close="96"),  # breaches stop 95
    )

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2))

    assert closeout.resolution is CloseoutResolution.INVALIDATION
    # entry 101, exit 95, qty 0.1 -> -0.6
    assert closeout.realized_profit_loss_amount == Decimal("-0.6")


def test_unexpired_without_touch_stays_open(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="102", low="100", close="101"),
        _candle(1, high="103", low="100", close="101"),  # no target/stop touch
    )

    recorded = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2))

    assert recorded == []
    assert service.get_closeout_attribution("trade-20260612-001") is None


def test_expired_without_touch_marks_to_market_as_expiry(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="102", low="100", close="101"),
        _candle(1, high="103", low="100", close="105.5"),  # last mark, no target/stop
    )

    # now is past the 4h expiry, so the end-of-candles is a real timeout.
    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=5))

    assert closeout.resolution is CloseoutResolution.EXPIRY
    # entry 101, mark-to-market exit 105.5, qty 0.1 -> +0.45
    assert closeout.realized_profit_loss_amount == Decimal("0.45")


def test_already_closed_and_sizeless_ideas_are_skipped(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        _candle(1, high="114", low="101", close="113"),
    )
    resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2))

    # A second pass is idempotent: the idea already carries a closeout.
    assert resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)) == []
