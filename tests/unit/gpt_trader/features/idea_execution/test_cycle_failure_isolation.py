"""A broken proposer/entry must not starve existing paper-position closeouts."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    CYCLE_NOW,
    build_cycle_idea,
    crossover_series,
    make_cycle_runner,
    manifest_rows,
    snapshot,
    snapshot_provider,
)

from gpt_trader.core import Candle
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.trade_ideas import BaselineProposer, SymbolSeries, TradeIdeaState


class FailingProposer:
    proposer_id = "broken"

    def propose(self, snapshot):
        raise ValueError("price_precision is too coarse")


def _existing_positions(service):
    for decision_id, symbol in (
        ("trade-20260703-entry", "BTC-USD"),
        ("trade-20260703-exit", "ETH-USD"),
    ):
        service.propose(build_cycle_idea(decision_id, instrument=symbol), actor_id="test")
        service.approve(decision_id, actor_id="rj", reason="verified")
    service.record_submission("trade-20260703-exit", actor_id="test", venue="paper")
    service.record_fill(
        "trade-20260703-exit",
        actor_id="test",
        venue="paper",
        evidence=("fill_price=60750", "fill_quantity=0.1"),
    )


def _prices():
    candle = Candle(
        ts=CYCLE_NOW + timedelta(hours=1),
        open=Decimal("60750"),
        high=Decimal("68000"),
        low=Decimal("60500"),
        close=Decimal("60750"),
        volume=Decimal("10"),
    )
    return snapshot(
        *(
            SymbolSeries(symbol=symbol, granularity="ONE_HOUR", candles=(candle,))
            for symbol in ("BTC-USD", "ETH-USD")
        ),
        as_of=CYCLE_NOW + timedelta(hours=3),
    )


@pytest.mark.parametrize("entry_failure", [False, True])
def test_proposer_failure_keeps_fills_and_closeouts_running(
    cycle_service, tmp_path, monkeypatch, entry_failure
):
    _existing_positions(cycle_service)
    if entry_failure:

        def broken_order(*args, **kwargs):
            raise ConnectionError("paper order failed after durable submission")

        monkeypatch.setattr(DeterministicBroker, "place_order", broken_order)
    runner = make_cycle_runner(
        cycle_service, tmp_path, proposers=[FailingProposer()], now=CYCLE_NOW + timedelta(hours=3)
    )
    result = runner.run(snapshot_provider(_prices()))
    assert result.outcome == "partial"
    assert "too coarse" in result.proposer_turns[0].error
    assert result.resolved_decision_ids == ("trade-20260703-exit",)
    assert cycle_service.get_closeout_attribution("trade-20260703-exit") is not None
    if entry_failure:
        assert cycle_service.get("trade-20260703-entry").state is TradeIdeaState.SUBMITTED
        assert result.execution.failed[0]["decision_id"] == "trade-20260703-entry"
        # Uncertain submitted intent is never resent by a later turn.
        second = runner.run(snapshot_provider(_prices()))
        assert not second.execution.failed
        assert not second.execution.executed
    else:
        assert cycle_service.get("trade-20260703-entry").state is TradeIdeaState.FILLED
        assert result.execution.executed[0]["final_state"] == "filled"
    assert manifest_rows(tmp_path)[0]["outcome"] == "partial"
    assert "proposer broken:" in manifest_rows(tmp_path)[0]["error"]
    assert result.report_summary["closeouts"] is not None


def test_other_proposer_continues_after_failure(cycle_service, tmp_path):
    result = make_cycle_runner(
        cycle_service, tmp_path, proposers=[FailingProposer(), BaselineProposer()]
    ).run(snapshot_provider(snapshot(crossover_series("BTC-USD"))))
    assert result.outcome == "partial"
    assert result.proposer_turns[1].proposal_count == 1


def test_unavailable_metadata_blocks_proposals_but_retains_exit_candles(cycle_service, tmp_path):
    _existing_positions(cycle_service)
    prices = _prices()
    prices = replace(
        prices,
        series=tuple(
            replace(series, price_increment_error="metadata unavailable")
            for series in prices.series
        ),
    )
    result = make_cycle_runner(
        cycle_service, tmp_path, proposers=[BaselineProposer()], now=prices.as_of
    ).run(snapshot_provider(prices))
    assert result.outcome == "partial"
    assert "metadata unavailable" in result.proposer_turns[0].error
    assert result.resolved_decision_ids == ("trade-20260703-exit",)


@pytest.mark.parametrize(
    "error_kind", ["audit", "budget", "autonomy", "closeout", "sqlite", "schema", "io"]
)
def test_global_storage_integrity_failure_stops_mutations(
    cycle_service, tmp_path, monkeypatch, error_kind
):
    import sqlite3

    from gpt_trader.features.trade_ideas import (
        AuditIntegrityError,
        AutonomyIntegrityError,
        BudgetIntegrityError,
    )
    from gpt_trader.features.trade_ideas.closeout import CloseoutAttributionIntegrityError

    errors = {
        "audit": AuditIntegrityError,
        "budget": BudgetIntegrityError,
        "autonomy": AutonomyIntegrityError,
        "closeout": CloseoutAttributionIntegrityError,
        "sqlite": sqlite3.DatabaseError,
        "schema": RuntimeError,
        "io": OSError,
    }
    error_type = errors[error_kind]
    _existing_positions(cycle_service)

    def reject_submission(*args, **kwargs):
        raise error_type("persistent state cannot be trusted")

    monkeypatch.setattr(cycle_service, "record_submission", reject_submission)
    runner = make_cycle_runner(
        cycle_service, tmp_path, proposers=[FailingProposer()], now=CYCLE_NOW + timedelta(hours=3)
    )
    with pytest.raises(error_type, match="persistent state"):
        runner.run(snapshot_provider(_prices()))
    assert cycle_service.get_closeout_attribution("trade-20260703-exit") is None
    assert cycle_service.get("trade-20260703-entry").state is TradeIdeaState.APPROVED
    assert manifest_rows(tmp_path)[0]["outcome"] == "failed"


def test_machine_proposal_requires_structured_exits_before_any_batch_write(cycle_service, tmp_path):
    candidates = [
        build_cycle_idea("trade-20260703-valid", instrument="ETH-USD"),
        replace(build_cycle_idea("trade-20260703-prose"), exit_plan=None),
    ]
    assert candidates[0].exit_plan is not None
    assert candidates[1].exit_plan is None

    class ProposerWithoutStructuredExit:
        proposer_id = "prose-only"

        def propose(self, snapshot):
            return candidates

    result = make_cycle_runner(
        cycle_service, tmp_path, proposers=[ProposerWithoutStructuredExit()]
    ).run(snapshot_provider(snapshot(crossover_series("BTC-USD"))))
    assert result.outcome == "partial"
    assert "structured exit_plan" in result.proposer_turns[0].error
    assert cycle_service.list_views() == []
