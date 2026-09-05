"""Actual paper execution and restart boundaries for targeted reductions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from tests.unit.gpt_trader.features.idea_execution.reduction_test_helpers import (
    proposal,
    setup_position,
)

from gpt_trader.core.order_intent import PositionOperation
from gpt_trader.features.idea_execution import IdeaNotExecutableError, PaperIdeaExecutor
from gpt_trader.features.trade_ideas import (
    TradeDirection,
    TradeIdeaService,
)
from gpt_trader.features.trade_ideas.position_operations import position

NOW = datetime(2026, 9, 5, 9, tzinfo=UTC)


@pytest.mark.parametrize("direction", [TradeDirection.LONG, TradeDirection.SHORT])
def test_actual_partial_reduce_and_close_account_once(tmp_path, monkeypatch, direction):
    service, broker, executor, price = setup_position(tmp_path, monkeypatch, direction)
    original = service.get("entry").idea
    expected = [
        (Decimal(".4"), Decimal(".6")),
        (Decimal(".2"), Decimal(".4")),
        (None, Decimal("0")),
    ]
    for i, (quantity, remaining) in enumerate(expected):
        action = "close" if quantity is None else "reduce"
        proposal(service, f"exit-{i}", quantity, action)
        price[0] = Decimal("110")
        result = executor.execute(f"exit-{i}")
        assert result.side == ("sell" if direction is TradeDirection.LONG else "buy")
        assert broker.place_order.call_args.kwargs["reduce_only"] is True
        assert position(service, "entry").remaining == remaining
        context = service.approval_budget_context(now=NOW)
        assert context.open_notional == Decimal("100") * remaining
        assert context.open_approved_at_risk_pct == original.max_loss.percent_of_account * remaining
    assert service.get("entry").closeout_attribution.realized_profit_loss_amount == (
        Decimal("10") if direction is TradeDirection.LONG else Decimal("-10")
    )
    assert len(service.query_closeout_records().items) == 1
    assert service.paper_accounting().realized_profit_loss_total == (
        Decimal("10") if direction is TradeDirection.LONG else Decimal("-10")
    )
    restarted = TradeIdeaService(tmp_path, now_factory=lambda: NOW)
    calls = broker.place_order.call_count
    PaperIdeaExecutor(restarted, broker).recover_receipts()
    assert broker.place_order.call_count == calls
    assert position(restarted, "entry").remaining == 0
    assert len(restarted.query_closeout_records().items) == 1


def test_lost_receipt_reserves_quantity_and_never_resends(tmp_path, monkeypatch):

    service, broker, executor, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce-a", ".7")
    proposal(service, "reduce-b", ".7")
    broker.place_order.side_effect = SystemExit("lost acknowledgment")
    with pytest.raises(SystemExit):
        executor.execute("reduce-a")
    restarted = TradeIdeaService(tmp_path, now_factory=lambda: NOW)
    recovered = PaperIdeaExecutor(restarted, broker, now_factory=lambda: NOW)
    assert recovered.recover_receipts()["unresolved"][0]["decision_id"] == "reduce-a"
    calls = broker.place_order.call_count
    with pytest.raises(IdeaNotExecutableError, match="unreserved"):
        recovered.execute("reduce-b")
    assert broker.place_order.call_count == calls
    projected = position(restarted, "entry")
    assert projected.remaining == 1 and projected.reserved == Decimal(".7")
    assert restarted.approval_budget_context().open_notional == 100


@pytest.mark.parametrize("boundary", ["after_receipt", "final_closeout"])
def test_crash_replays_receipt_and_final_aggregate_once(tmp_path, monkeypatch, boundary):
    service, broker, executor, price = setup_position(tmp_path, monkeypatch)
    proposal(service, "close", None, "close")
    price[0] = Decimal("90")
    if boundary == "after_receipt":
        monkeypatch.setattr(executor, "_reconcile_receipt", Mock(side_effect=SystemExit("crash")))
    else:
        monkeypatch.setattr(service.closeout_log, "append", Mock(side_effect=SystemExit("crash")))
    with pytest.raises(SystemExit):
        executor.execute("close")
    assert service.execution_journal.entries()["close"].receipt["status"] == "filled"
    assert not service.execution_journal.entries()["close"].reconciled
    assert service.get("entry").closeout_attribution is None
    restarted = TradeIdeaService(tmp_path, now_factory=lambda: NOW)
    recovery = PaperIdeaExecutor(restarted, broker, now_factory=lambda: NOW)
    calls = broker.place_order.call_count
    recovery.recover_receipts()
    recovery.recover_receipts()
    assert broker.place_order.call_count == calls
    assert position(restarted, "entry").remaining == 0
    assert restarted.paper_accounting().realized_profit_loss_total == -10
    assert restarted.paper_accounting().closeout_count == 1
    assert len(restarted.query_closeout_records().items) == 1


def test_two_independent_service_writers_cannot_over_reduce(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    service, broker, _, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce-a", ".7")
    proposal(service, "reduce-b", ".7")

    def execute(decision):
        other = TradeIdeaService(tmp_path, now_factory=lambda: NOW)
        try:
            PaperIdeaExecutor(other, broker, now_factory=lambda: NOW).execute(decision)
            return "filled"
        except IdeaNotExecutableError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(execute, ["reduce-a", "reduce-b"]))
    assert sorted(outcomes) == ["filled", "refused"]
    assert position(service, "entry").remaining == Decimal(".3")
    assert broker.place_order.call_count == 2  # One entry plus one admitted reduction.


def test_receipt_conflict_keeps_target_reserved(tmp_path, monkeypatch):
    from gpt_trader.core.trading import OrderSide
    from gpt_trader.features.idea_execution import PaperExecutionError

    service, broker, executor, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    place = broker.place_order.side_effect
    broker.place_order.side_effect = lambda **kwargs: replace(place(**kwargs), side=OrderSide.BUY)
    with pytest.raises(PaperExecutionError):
        executor.execute("reduce")
    assert service.execution_journal.entries()["reduce"].receipt is None
    assert position(service, "entry").reserved == Decimal(".4")
    assert service.paper_accounting().realized_profit_loss_total == 0


def test_reduction_operations_do_not_inflate_track_record(tmp_path, monkeypatch):
    from gpt_trader.features.trade_ideas.lifecycle import (
        LifecycleClassification,
        classify_lifecycle,
    )
    from gpt_trader.features.trade_ideas.report import build_trade_idea_track_record_report

    service, _, executor, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    executor.execute("reduce")
    report = build_trade_idea_track_record_report(service, now=NOW)
    assert report["proposal_volume"]["idea_count"] == 1
    assert (
        classify_lifecycle(service.get("reduce"), now=NOW + timedelta(days=10))
        is LifecycleClassification.NOT_APPLICABLE
    )


def test_historical_and_new_operations_roundtrip_disposable_store(tmp_path, monkeypatch):
    from gpt_trader.features.trade_ideas.migration import export_current, import_legacy, validate

    root = tmp_path / "source"
    service, _, executor, _ = setup_position(root, monkeypatch)
    original = service.get("entry").idea.to_dict()
    assert "position_operation" not in original
    proposal(service, "reduce", ".4")
    executor.execute("reduce")
    exported = tmp_path / "export"
    imported = tmp_path / "import"
    export_current(root, exported)
    import_legacy(exported, imported)
    assert validate(root) == validate(imported)
    restarted = TradeIdeaService(imported, now_factory=lambda: NOW)
    assert restarted.get("entry").idea.to_dict() == original
    assert position(restarted, "entry").remaining == Decimal(".6")


def test_actual_exit_monitor_closes_only_confirmed_remainder(tmp_path, monkeypatch):
    from gpt_trader.core import Candle
    from gpt_trader.features.idea_execution import resolve_filled_ideas
    from gpt_trader.features.trade_ideas import MarketSnapshot, SymbolSeries

    service, broker, executor, price = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    price[0] = Decimal("105")
    executor.execute("reduce")
    candle = Candle(
        ts=NOW + timedelta(hours=1),
        open=Decimal("105"),
        high=Decimal("111"),
        low=Decimal("100"),
        close=Decimal("110"),
        volume=Decimal("10"),
    )
    snapshot = MarketSnapshot(
        as_of=NOW + timedelta(hours=2),
        source="offline-fixture",
        series=(SymbolSeries(symbol="BTC-USD", granularity="ONE_HOUR", candles=(candle,)),),
    )
    calls = broker.place_order.call_count
    result = resolve_filled_ideas(service, snapshot, now=NOW + timedelta(hours=2))
    assert len(result.recorded) == 1
    assert result.recorded[0].realized_profit_loss_amount == Decimal("8")  # .4*5 + .6*10
    assert result.recorded[0].timestamp == candle.ts
    assert broker.place_order.call_count == calls
    events = service.execution_journal.simulated_resolutions()
    assert len(events) == 1 and events[0]["quantity"] == "0.6"
    assert events[0]["source"] == "simulated_candle_resolution"
    assert len(service.execution_journal.entries()) == 2  # No fabricated broker exit receipt.
    assert not resolve_filled_ideas(service, snapshot, now=NOW + timedelta(hours=2)).recorded


def test_session_losses_equity_and_settlement_use_each_portion_once(tmp_path, monkeypatch):
    start = datetime(2026, 9, 8, 14, tzinfo=UTC)
    service, broker, executor, price = setup_position(
        tmp_path, monkeypatch, instrument="AAPL", now=start
    )
    clock = [start + timedelta(minutes=1)]
    monkeypatch.setattr(service, "_now", lambda: clock[0])
    monkeypatch.setattr(executor, "_now_factory", lambda: clock[0])
    original = broker.place_order.side_effect
    broker.place_order.side_effect = lambda **kwargs: replace(
        original(**kwargs), updated_at=clock[0]
    )
    proposal(service, "reduce", ".4")
    price[0] = Decimal("90")
    executor.execute("reduce")
    first = service.approval_budget_context(now=clock[0])
    assert first.same_day_realized_loss_pct == Decimal(".024")
    assert first.open_notional == Decimal("60")
    assert first.unsettled_equity_proceeds == Decimal("36")  # .4*100 - 4 loss
    assert service.paper_accounting().current_equity == Decimal("49996")
    clock[0] = start + timedelta(days=1)
    proposal(service, "close", None, "close")
    price[0] = Decimal("110")
    executor.execute("close")
    final = service.approval_budget_context(now=clock[0])
    assert final.same_day_realized_loss_pct == 0  # Yesterday's loss is not the final aggregate.
    assert final.unsettled_equity_proceeds == Decimal("66")
    assert final.open_notional == 0
    accounting = service.paper_accounting()
    assert accounting.current_equity == Decimal("50002")
    assert accounting.realized_profit_loss_total == Decimal("2")
    assert accounting.high_water_mark == Decimal("50002")
    assert accounting.closeout_count == 1


@pytest.mark.parametrize("conflict", ["hash", "instrument", "direction", "quantity"])
def test_approval_refuses_conflicting_target_before_any_broker_call(
    tmp_path, monkeypatch, conflict
):
    from gpt_trader.features.trade_ideas.policy import PolicyViolationError

    service, broker, _, _ = setup_position(tmp_path, monkeypatch)
    target = service.get("entry").idea
    idea = replace(
        target,
        decision_id="bad-reduction",
        broker_ticket=type(target.broker_ticket)(),
        position_operation=PositionOperation("reduce", "entry", target.record_hash()),
        sizing_recommendation=replace(target.sizing_recommendation, quantity=Decimal(".4")),
    )
    if conflict == "hash":
        idea = replace(idea, position_operation=PositionOperation("reduce", "entry", "a" * 64))
    elif conflict == "instrument":
        idea = replace(idea, instrument="ETH-USD")
    elif conflict == "direction":
        idea = replace(idea, direction=TradeDirection.SHORT)
    else:
        idea = replace(
            idea, sizing_recommendation=replace(idea.sizing_recommendation, quantity=Decimal("2"))
        )
    service.propose(idea, actor_id="proposer")
    with pytest.raises(PolicyViolationError):
        service.approve(idea.decision_id, actor_id="operator", reason="invalid target")
    assert broker.place_order.call_count == 1
    assert not position(service, "entry").reserved


def test_manual_fill_cannot_bypass_target_receipt_and_reservation(tmp_path, monkeypatch):
    from gpt_trader.features.trade_ideas.workflow import InvalidTransitionError

    service, _, _, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    with pytest.raises(InvalidTransitionError, match="admitted paper intent"):
        service.record_submission("reduce", actor_id="manual", venue="paper")
    with pytest.raises(InvalidTransitionError, match="durable paper receipt"):
        service.record_fill("reduce", actor_id="manual", venue="paper")
    assert position(service, "entry").remaining == 1
