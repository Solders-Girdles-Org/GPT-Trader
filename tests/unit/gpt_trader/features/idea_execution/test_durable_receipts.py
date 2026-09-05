"""Restart and concurrent-writer counterexamples for the actual paper executor."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    CYCLE_NOW,
    build_cycle_idea,
    make_cycle_runner,
    manifest_rows,
)

from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import IdeaNotExecutableError, PaperIdeaExecutor
from gpt_trader.features.trade_ideas import AuditAction, TradeIdeaService, TradeIdeaState
from gpt_trader.features.trade_ideas.migration import export_current, import_legacy

ID = "trade-20260703-receipt"


def approve(service):
    service.propose(build_cycle_idea(ID), actor_id="test")
    service.approve(ID, actor_id="rj", reason="fixture approval")


def executor(service, broker=None):
    return PaperIdeaExecutor(
        service, broker or DeterministicBroker(), now_factory=lambda: CYCLE_NOW
    )


def pending_receipt(service, monkeypatch):
    approve(service)
    worker = executor(service)

    def crash(*args, **kwargs):
        raise SystemExit("after durable receipt")

    monkeypatch.setattr(worker, "_reconcile_receipt", crash)
    with pytest.raises(SystemExit):
        worker.execute(ID)
    assert service.get(ID).state is TradeIdeaState.SUBMITTED
    assert service.execution_journal.entries()[ID].receipt is not None


def test_restart_replays_receipt_once_without_broker_call(cycle_service, monkeypatch):
    pending_receipt(cycle_service, monkeypatch)
    restarted = TradeIdeaService(cycle_service.root, now_factory=lambda: CYCLE_NOW)
    broker = DeterministicBroker()
    broker.place_order = Mock(side_effect=AssertionError("recovery must never submit"))
    worker = executor(restarted, broker)
    assert worker.recover_receipts()["recovered_decision_ids"] == [ID]
    assert worker.recover_receipts()["recovered_decision_ids"] == []
    assert restarted.execution_journal.entries()[ID].reconciled
    assert len([e for e in restarted.get(ID).events if e.action is AuditAction.FILLED]) == 1
    broker.place_order.assert_not_called()
    with pytest.raises(IdeaNotExecutableError):
        worker.execute(ID)


def test_real_process_exit_during_fill_transaction_rolls_back_and_recovers(
    cycle_service, monkeypatch
):
    pending_receipt(cycle_service, monkeypatch)
    script = """
import os, sys
from gpt_trader.features.trade_ideas import TradeIdeaService, PaperFillReconciler
from gpt_trader.features.idea_execution import PaperIdeaExecutor
from gpt_trader.features.brokerages.mock import DeterministicBroker
from pathlib import Path
service = TradeIdeaService(Path(sys.argv[1]))
original = PaperFillReconciler.reconcile_fills
def die(self, *args, **kwargs):
    original(self, *args, **kwargs)
    os._exit(87)
PaperFillReconciler.reconcile_fills = die
PaperIdeaExecutor(service, DeterministicBroker()).recover_receipts()
"""
    result = subprocess.run([sys.executable, "-c", script, str(cycle_service.root)], check=False)
    assert result.returncode == 87
    restarted = TradeIdeaService(cycle_service.root, now_factory=lambda: CYCLE_NOW)
    assert restarted.get(ID).state is TradeIdeaState.SUBMITTED
    assert not restarted.execution_journal.entries()[ID].reconciled
    assert executor(restarted).recover_receipts()["recovered_decision_ids"] == [ID]
    assert len([e for e in restarted.get(ID).events if e.action is AuditAction.FILLED]) == 1


def test_concurrent_executors_admit_one_submission(cycle_service, monkeypatch):
    approve(cycle_service)
    broker = DeterministicBroker()
    place = Mock(wraps=broker.place_order)
    monkeypatch.setattr(broker, "place_order", place)
    barrier = Barrier(2)

    def run():
        worker = executor(
            TradeIdeaService(cycle_service.root, now_factory=lambda: CYCLE_NOW), broker
        )
        barrier.wait()
        try:
            return worker.execute(ID).final_state
        except IdeaNotExecutableError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run(), range(2)))
    assert sorted(outcomes) == ["filled", "refused"]
    assert place.call_count == 1
    assert len([e for e in cycle_service.get(ID).events if e.action is AuditAction.SUBMITTED]) == 1


def test_concurrent_recovery_records_one_fill(cycle_service, monkeypatch):
    pending_receipt(cycle_service, monkeypatch)

    def run(_):
        return executor(TradeIdeaService(cycle_service.root)).recover_receipts()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(len(result["recovered_decision_ids"]) for result in results) == 1
    assert len([e for e in cycle_service.get(ID).events if e.action is AuditAction.FILLED]) == 1


def test_missing_receipt_remains_uncertain_after_restart(cycle_service, monkeypatch):
    approve(cycle_service)
    broker = DeterministicBroker()
    broker.place_order = Mock(side_effect=ConnectionError("response lost"))
    with pytest.raises(ConnectionError):
        executor(cycle_service, broker).execute(ID)
    restarted = TradeIdeaService(cycle_service.root)
    result = executor(restarted).recover_receipts()
    assert result["recovered_decision_ids"] == []
    assert "no durable broker receipt" in result["unresolved"][0]["reason"]
    with pytest.raises(IdeaNotExecutableError):
        executor(restarted, broker).execute(ID)
    assert broker.place_order.call_count == 1


def test_current_export_import_preserves_pending_and_reconciled_receipts(
    cycle_service, tmp_path, monkeypatch
):
    pending_receipt(cycle_service, monkeypatch)
    exported = tmp_path / "export"
    imported = tmp_path / "import"
    report = export_current(cycle_service.root, exported)
    assert report["counts"]["execution_intents"] == 1
    import_legacy(exported, imported)
    restored = TradeIdeaService(imported)
    assert restored.execution_journal.entries() == cycle_service.execution_journal.entries()
    executor(restored).recover_receipts()
    export_current(imported, tmp_path / "after")
    import_legacy(tmp_path / "after", tmp_path / "reimport")
    assert TradeIdeaService(tmp_path / "reimport").execution_journal.entries()[ID].reconciled


def test_cycle_recovers_before_market_data_failure(cycle_service, tmp_path, monkeypatch):
    pending_receipt(cycle_service, monkeypatch)

    def unavailable():
        raise ConnectionError("market data unavailable")

    with pytest.raises(ConnectionError):
        make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(unavailable)
    assert cycle_service.get(ID).state is TradeIdeaState.FILLED
    row = manifest_rows(tmp_path)[0]
    assert row["outcome"] == "failed"
    assert row["recovery"]["recovered_decision_ids"] == [ID]


def test_intent_failure_rolls_back_submission_before_broker(cycle_service, monkeypatch):
    from gpt_trader.features.trade_ideas.execution_journal import ExecutionJournalIntegrityError

    approve(cycle_service)
    broker = DeterministicBroker()
    broker.place_order = Mock(wraps=broker.place_order)
    monkeypatch.setattr(
        cycle_service.execution_journal,
        "record_intent",
        Mock(side_effect=ExecutionJournalIntegrityError("cannot record intent")),
    )
    with pytest.raises(ExecutionJournalIntegrityError):
        executor(cycle_service, broker).execute(ID)
    assert cycle_service.get(ID).state is TradeIdeaState.APPROVED
    assert cycle_service.execution_journal.entries() == {}
    broker.place_order.assert_not_called()


def test_broker_filled_but_process_dies_before_receipt_never_resends(cycle_service, monkeypatch):
    approve(cycle_service)
    broker = DeterministicBroker()
    original = broker.place_order

    def lose_response(**kwargs):
        original(**kwargs)
        raise SystemExit("filled but receipt not committed")

    broker.place_order = Mock(side_effect=lose_response)
    with pytest.raises(SystemExit):
        executor(cycle_service, broker).execute(ID)
    restarted = TradeIdeaService(cycle_service.root)
    assert executor(restarted).recover_receipts()["unresolved"]
    with pytest.raises(IdeaNotExecutableError):
        executor(restarted, broker).execute(ID)
    assert broker.place_order.call_count == 1


def test_conflicting_receipt_cannot_overwrite_committed_fact(cycle_service, monkeypatch):
    from gpt_trader.features.trade_ideas.execution_journal import ExecutionJournalIntegrityError

    pending_receipt(cycle_service, monkeypatch)
    before = cycle_service.execution_journal.entries()[ID]
    with pytest.raises(ExecutionJournalIntegrityError):
        cycle_service.execution_journal.record_receipt(
            ID, {**before.receipt, "order_id": "foreign"}
        )
    assert cycle_service.execution_journal.entries()[ID] == before


def test_export_rejects_rehashed_intent_with_wrong_record_quantity(
    cycle_service, tmp_path, monkeypatch
):
    import json

    from gpt_trader.features.trade_ideas.execution_journal import payload_hash

    pending_receipt(cycle_service, monkeypatch)
    exported = tmp_path / "export"
    export_current(cycle_service.root, exported)
    path = exported / "paper_execution.jsonl"
    event = json.loads(path.read_text().splitlines()[0])
    event["intent"]["quantity"] = "100"
    event["intent_hash"] = payload_hash(event["intent"])
    path.write_text(json.dumps(event) + "\n")
    from gpt_trader.features.trade_ideas.execution_journal import ExecutionJournalIntegrityError

    with pytest.raises(ExecutionJournalIntegrityError, match="conflicts with idea"):
        import_legacy(exported, tmp_path / "bad-import")
    assert not (tmp_path / "bad-import").exists()
