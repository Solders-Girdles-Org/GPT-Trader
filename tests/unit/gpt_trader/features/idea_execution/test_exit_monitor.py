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
    RecordedFill,
    SizingRecommendation,
    SymbolSeries,
    TimeHorizon,
    TradeIdeaService,
    TradeIdeaState,
    encode_fill_evidence,
)

CLOCK = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
QUANTITY = Decimal("0.1")


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    built = TradeIdeaService(tmp_path / "trade_ideas", now_factory=lambda: CLOCK)
    attest_account_equity(built)
    return built


def _fill_idea(
    service: TradeIdeaService,
    *,
    decision_id: str = "trade-20260612-001",
    instrument: str = "BTC-USD",
    fill_evidence: tuple[str, ...] = (),
) -> None:
    idea = build_trade_idea(
        decision_id=decision_id,
        instrument=instrument,
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
    service.record_fill(
        decision_id,
        actor_id="coinbase",
        venue="coinbase",
        evidence=fill_evidence,
    )


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


def _snapshot(*candles: Candle, symbol: str = "BTC-USD") -> MarketSnapshot:
    # as_of sits after the recorded candles: the monitor runs on a later turn's
    # snapshot whose bars span the position's post-entry history.
    return MarketSnapshot(
        as_of=CLOCK + timedelta(hours=3),
        source="test:fixture",
        series=(SymbolSeries(symbol=symbol, granularity="ONE_HOUR", candles=candles),),
    )


def test_target_hit_records_thesis_target_with_positive_pnl(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),  # entry candle (in zone)
        _candle(1, high="114", low="101", close="113"),  # hits target 113
    )

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)).recorded

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

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)).recorded

    assert closeout.resolution is CloseoutResolution.INVALIDATION
    # entry 101, exit 95, qty 0.1 -> -0.6
    assert closeout.realized_profit_loss_amount == Decimal("-0.6")


def test_unexpired_without_touch_stays_open(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="102", low="100", close="101"),
        _candle(1, high="103", low="100", close="101"),  # no target/stop touch
    )

    recorded = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)).recorded

    assert recorded == ()
    assert service.get_closeout_attribution("trade-20260612-001") is None


def test_expired_without_touch_marks_to_market_as_expiry(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="102", low="100", close="101"),
        _candle(1, high="103", low="100", close="105.5"),  # last mark, no target/stop
    )

    # now is past the 4h expiry, so the end-of-candles is a real timeout.
    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=5)).recorded

    assert closeout.resolution is CloseoutResolution.EXPIRY
    # entry 101, mark-to-market exit 105.5, qty 0.1 -> +0.45
    assert closeout.realized_profit_loss_amount == Decimal("0.45")


def test_equity_idea_is_not_resolved_while_xnys_is_closed(service: TradeIdeaService) -> None:
    # Resolving an equity position against a closed session would time it out
    # or mark it against stale data; the pass must skip it loudly instead
    # (issue #1232). Saturday 2026-06-13 15:00 UTC is a closed XNYS instant.
    _fill_idea(service, instrument="AAPL")
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        _candle(1, high="114", low="101", close="113"),  # would hit target 113
        symbol="AAPL",
    )
    weekend = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)

    weekend_pass = resolve_filled_ideas(service, snapshot, now=weekend)

    assert weekend_pass.recorded == ()
    (skip,) = weekend_pass.skipped_closed_sessions
    assert skip["decision_id"] == "trade-20260612-001"
    assert skip["instrument"] == "AAPL"
    assert "market closed for session XNYS" in skip["reason"]
    assert "next open 2026-06-15T13:30:00+00:00" in skip["reason"]
    assert service.get("trade-20260612-001").state is TradeIdeaState.FILLED
    assert service.get_closeout_attribution("trade-20260612-001") is None

    # At the next open the same pass resolves against the recorded candles.
    monday_open = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
    (closeout,) = resolve_filled_ideas(service, snapshot, now=monday_open).recorded
    assert closeout.resolution is CloseoutResolution.THESIS_TARGET


def test_crypto_idea_resolves_on_a_weekend(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        _candle(1, high="114", low="101", close="113"),
    )
    weekend = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)

    weekend_pass = resolve_filled_ideas(service, snapshot, now=weekend)

    assert weekend_pass.skipped_closed_sessions == ()
    (closeout,) = weekend_pass.recorded
    assert closeout.resolution is CloseoutResolution.THESIS_TARGET


def test_unclassifiable_instrument_is_skipped_loudly(service: TradeIdeaService) -> None:
    _fill_idea(service, instrument="BTC-USD-PERP")
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        _candle(1, high="114", low="101", close="113"),
        symbol="BTC-USD-PERP",
    )

    monitor_pass = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2))

    assert monitor_pass.recorded == ()
    (skip,) = monitor_pass.skipped_closed_sessions
    assert skip["decision_id"] == "trade-20260612-001"
    assert skip["instrument"] == "BTC-USD-PERP"
    assert "not classifiable to a trading session" in skip["reason"]
    assert service.get("trade-20260612-001").state is TradeIdeaState.FILLED


def test_calendar_out_of_bounds_is_skipped_loudly(service: TradeIdeaService) -> None:
    _fill_idea(service, instrument="AAPL")
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        _candle(1, high="114", low="101", close="113"),
        symbol="AAPL",
    )
    historical_now = datetime(1980, 1, 2, 15, 0, tzinfo=UTC)

    monitor_pass = resolve_filled_ideas(service, snapshot, now=historical_now)

    assert monitor_pass.recorded == ()
    (skip,) = monitor_pass.skipped_closed_sessions
    assert "session calendar XNYS cannot evaluate" in skip["reason"]
    assert service.get("trade-20260612-001").state is TradeIdeaState.FILLED


def test_already_closed_and_sizeless_ideas_are_skipped(service: TradeIdeaService) -> None:
    _fill_idea(service)
    snapshot = _snapshot(
        _candle(0, high="103", low="100", close="102"),
        _candle(1, high="114", low="101", close="113"),
    )
    resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2))

    # A second pass is idempotent: the idea already carries a closeout.
    assert resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)).recorded == ()


def test_out_of_zone_fill_resolves_from_recorded_fill_price(service: TradeIdeaService) -> None:
    """A venue-confirmed fill outside the planned entry zone must still resolve.

    Historically the monitor re-simulated entry from the proposal's entry zone
    (issue #1212): a confirmed fill whose price never revisited the zone
    replayed as NOT_FILLED and the position stayed open indefinitely.
    """
    _fill_idea(
        service,
        fill_evidence=encode_fill_evidence(
            price=Decimal("105"),
            quantity=QUANTITY,
            filled_at=CLOCK,
        ),
    )
    snapshot = _snapshot(
        _candle(0, high="106", low="103.5", close="104"),  # never touches zone 100-102
        _candle(1, high="114", low="103", close="113"),  # hits target 113
    )

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=2)).recorded

    assert closeout.resolution is CloseoutResolution.THESIS_TARGET
    # actual fill 105, exit 113, qty 0.1 -> +0.8 (not the zone-midpoint 101 estimate)
    assert closeout.realized_profit_loss_amount == Decimal("0.8")
    assert "entry_price=105" in closeout.evidence
    assert "entry_price_source=recorded_fill" in closeout.evidence


def test_pre_fill_candles_are_not_used_for_exit_evaluation(service: TradeIdeaService) -> None:
    """Only candles at/after the recorded fill time may resolve the position."""
    _fill_idea(
        service,
        fill_evidence=encode_fill_evidence(
            price=Decimal("105"),
            quantity=QUANTITY,
            filled_at=CLOCK + timedelta(hours=2),
        ),
    )
    snapshot = _snapshot(
        _candle(0, high="114", low="100", close="104"),  # pre-fill target touch: must not exit
        _candle(2, high="108", low="103", close="106.5"),  # last post-fill mark
    )

    # Unexpired: no post-fill touch, so the position stays open.
    assert resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=3)).recorded == ()

    # Expired: mark-to-market from the actual fill, using post-fill candles only.
    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=5)).recorded
    assert closeout.resolution is CloseoutResolution.EXPIRY
    # entry 105, mark 106.5, qty 0.1 -> +0.15
    assert closeout.realized_profit_loss_amount == Decimal("0.15")


def test_legacy_fill_without_price_marks_expiry_from_zone_midpoint(
    service: TradeIdeaService,
) -> None:
    """A pre-evidence fill still resolves, disclosing the estimated entry price."""
    _fill_idea(service)  # no fill evidence recorded (legacy)
    snapshot = _snapshot(
        _candle(0, high="106", low="103.5", close="104"),  # out of zone: old replay hangs here
        _candle(1, high="108", low="103", close="107"),
    )

    (closeout,) = resolve_filled_ideas(service, snapshot, now=CLOCK + timedelta(hours=5)).recorded

    assert closeout.resolution is CloseoutResolution.EXPIRY
    # documented estimate: zone midpoint 101, mark 107, qty 0.1 -> +0.6
    assert closeout.realized_profit_loss_amount == Decimal("0.6")
    assert "entry_price_source=planned_zone_midpoint" in closeout.evidence


def test_fallback_fill_facts_repair_legacy_fills(service: TradeIdeaService) -> None:
    """Durable execution evidence (cycle manifest) supplies legacy fill facts."""
    _fill_idea(service)  # legacy: no evidence on the FILLED audit event
    snapshot = _snapshot(
        _candle(0, high="106", low="103.5", close="104"),
        _candle(1, high="114", low="103", close="113"),  # hits target 113
    )
    fallback = RecordedFill(
        filled_at=None,
        price=Decimal("105"),
        quantity=QUANTITY,
        venue="paper",
        external_order_id="MOCK_000001",
        source="cycle_manifest",
    )

    (closeout,) = resolve_filled_ideas(
        service,
        snapshot,
        now=CLOCK + timedelta(hours=2),
        fallback_fills={"trade-20260612-001": fallback},
    ).recorded

    assert closeout.resolution is CloseoutResolution.THESIS_TARGET
    assert closeout.realized_profit_loss_amount == Decimal("0.8")
    assert "entry_price_source=cycle_manifest" in closeout.evidence
