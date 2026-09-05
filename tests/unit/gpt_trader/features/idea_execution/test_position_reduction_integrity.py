"""Admission and foreign-writer counterexamples for the existing paper journal."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from tests.unit.gpt_trader.features.idea_execution.reduction_test_helpers import (
    NOW,
    proposal,
    setup_position,
)

from gpt_trader.features.idea_execution import IdeaNotExecutableError, PaperIdeaExecutor
from gpt_trader.features.trade_ideas import (
    ActorType,
    AutonomyMode,
    CloseoutResolution,
    TradeIdeaService,
)
from gpt_trader.features.trade_ideas.execution_journal import (
    EXECUTION_STREAM,
    ExecutionJournalIntegrityError,
    payload_hash,
)
from gpt_trader.features.trade_ideas.position_operations import position, record_simulated_close


@pytest.mark.parametrize("change", ["expiry", "autonomy", "budget"])
def test_reduction_rechecks_current_authority_and_budget_at_actual_submission(
    tmp_path, monkeypatch, change
):
    service, broker, executor, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    if change == "expiry":
        monkeypatch.setattr(executor, "_now_factory", lambda: NOW + timedelta(days=8))
    elif change == "autonomy":
        service.set_autonomy_mode(
            AutonomyMode.RESEARCH_ONLY,
            actor_type=ActorType.HUMAN,
            actor_id="operator",
            reason="revoke after approval",
        )
    else:
        budget = service.current_budget()
        service.update_budget(
            replace(budget, version=budget.version + 1, max_daily_loss_pct=Decimal(".5")),
            ActorType.HUMAN,
            "operator",
        )
    with pytest.raises(IdeaNotExecutableError):
        executor.execute("reduce")
    assert broker.place_order.call_count == 1
    assert "reduce" not in service.execution_journal.entries()
    assert position(service, "entry").remaining == 1


def test_recovery_does_not_reauthorize_a_confirmed_reduction(tmp_path, monkeypatch):
    service, broker, executor, price = setup_position(tmp_path, monkeypatch)
    proposal(service, "close", None, "close")
    price[0] = Decimal("90")
    monkeypatch.setattr(
        executor, "_reconcile_receipt", Mock(side_effect=SystemExit("after receipt"))
    )
    with pytest.raises(SystemExit):
        executor.execute("close")
    service.set_autonomy_mode(
        AutonomyMode.RESEARCH_ONLY,
        actor_type=ActorType.HUMAN,
        actor_id="operator",
        reason="revoked after actual fill",
    )
    restarted = TradeIdeaService(tmp_path, now_factory=lambda: NOW)
    PaperIdeaExecutor(restarted, broker, now_factory=lambda: NOW).recover_receipts()
    assert broker.place_order.call_count == 2
    assert position(restarted, "entry").remaining == 0
    assert restarted.get("entry").closeout_attribution.realized_profit_loss_amount == -10


def test_simulated_resolution_and_final_closeout_rollback_together(tmp_path, monkeypatch):
    service, _, _, _ = setup_position(tmp_path, monkeypatch)
    append = service.closeout_log.append
    monkeypatch.setattr(service.closeout_log, "append", Mock(side_effect=SystemExit("crash")))
    kwargs = dict(
        quantity=Decimal("1"),
        price=Decimal("110"),
        resolved_at=NOW,
        observed_at=NOW,
        resolution=CloseoutResolution.THESIS_TARGET,
        evidence=("offline candle",),
        actor_id="monitor",
    )
    with pytest.raises(SystemExit):
        record_simulated_close(service, "entry", **kwargs)
    assert not service.execution_journal.simulated_resolutions()
    assert position(service, "entry").remaining == 1
    monkeypatch.setattr(service.closeout_log, "append", append)
    record_simulated_close(service, "entry", **kwargs)
    assert len(service.execution_journal.simulated_resolutions()) == 1
    assert len(service.query_closeout_records().items) == 1


@pytest.mark.parametrize("corruption", ["target", "quantity", "checksum"])
def test_actual_migration_refuses_corrupted_reduction_receipt_or_target(
    tmp_path, monkeypatch, corruption
):
    from gpt_trader.features.trade_ideas.migration import validate

    service, _, executor, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    executor.execute("reduce")
    repository = service.execution_journal.repository
    with repository.transaction(write=True):
        # Simulate an out-of-contract foreign writer in this disposable test DB.
        repository.connection.execute("DROP TRIGGER immutable_events_update")
        rows = repository.connection.execute(
            "SELECT sequence,payload FROM events WHERE stream=?", (EXECUTION_STREAM,)
        ).fetchall()
        for sequence, raw in rows:
            event = json.loads(raw)
            if event.get("decision_id") != "reduce":
                continue
            if corruption == "target" and event["kind"] == "intent":
                event["intent"]["position_operation"]["target_record_hash"] = "b" * 64
                event["intent_hash"] = payload_hash(event["intent"])
            elif corruption in {"quantity", "checksum"} and event["kind"] == "receipt":
                event["receipt"]["quantity"] = "2"
                if corruption == "quantity":
                    event["receipt_hash"] = payload_hash(event["receipt"])
            else:
                continue
            repository.connection.execute(
                "UPDATE events SET payload=? WHERE sequence=?", (json.dumps(event), sequence)
            )
            break
    with pytest.raises(ExecutionJournalIntegrityError):
        position(service, "entry")
    with pytest.raises(ExecutionJournalIntegrityError):
        validate(tmp_path)


def test_missing_legacy_fill_basis_is_not_invented_for_executable_reduction(tmp_path, monkeypatch):
    from gpt_trader.features.trade_ideas.policy import PolicyViolationError

    service, _, _, _ = setup_position(tmp_path / "good", monkeypatch)
    target = service.get("entry").idea
    legacy = TradeIdeaService(tmp_path / "legacy", now_factory=lambda: NOW)
    legacy.update_budget(replace(service.current_budget(), version=2), ActorType.HUMAN, "operator")
    legacy.propose(replace(target, broker_ticket=type(target.broker_ticket)()), actor_id="proposer")
    legacy.approve("entry", actor_id="operator", reason="legacy approved")
    legacy.record_submission("entry", actor_id="manual", venue="paper")
    legacy.record_fill("entry", actor_id="manual", venue="paper")  # No old quantity/basis evidence.
    with pytest.raises(PolicyViolationError, match="confirmed entry"):
        proposal(legacy, "reduce", ".4")
    assert not legacy.execution_journal.entries()


@pytest.mark.parametrize("available", ["1", ".2"])
def test_actual_hybrid_reduction_preserves_exact_intent_or_uncertainty(
    tmp_path, monkeypatch, available
):
    import gpt_trader.features.brokerages.paper.hybrid as hybrid_module
    from gpt_trader.core.trading import OrderSide
    from gpt_trader.features.brokerages.paper.hybrid import HybridPaperBroker
    from gpt_trader.features.idea_execution import PaperExecutionError

    service, _, _, _ = setup_position(tmp_path, monkeypatch)
    client = Mock()
    broker = HybridPaperBroker(client=client, slippage_bps=0, commission_bps=Decimal(0))
    broker._last_prices["BTC-USD"] = Decimal("100")
    monkeypatch.setattr(hybrid_module, "utc_now", lambda: NOW)
    broker.place_order(symbol="BTC-USD", side=OrderSide.BUY, quantity=Decimal(available))
    proposal(service, "reduce", ".4")
    executor = PaperIdeaExecutor(service, broker, now_factory=lambda: NOW)
    if available == "1":
        executor.execute("reduce")
        assert position(service, "entry").remaining == Decimal(".6")
        assert broker.list_positions()[0].quantity == Decimal(".6")
    else:
        with pytest.raises(PaperExecutionError, match="receipt conflicts"):
            executor.execute("reduce")
        assert position(service, "entry").reserved == Decimal(".4")
        assert service.execution_journal.entries()["reduce"].receipt is None
    assert client.mock_calls == []


def test_explicit_resolution_replay_rejects_quantity_change(tmp_path, monkeypatch):
    service, _, _, _ = setup_position(tmp_path, monkeypatch)
    with pytest.raises(ExecutionJournalIntegrityError, match="remaining"):
        record_simulated_close(
            service,
            "entry",
            quantity=Decimal(".9"),
            price=Decimal("110"),
            resolved_at=NOW,
            observed_at=NOW,
            resolution=CloseoutResolution.THESIS_TARGET,
            evidence=("offline candle",),
            actor_id="monitor",
        )
    assert not service.execution_journal.simulated_resolutions()
    assert position(service, "entry").remaining == 1


def test_pending_receipt_blocks_manual_final_attribution_without_poisoning_state(
    tmp_path, monkeypatch
):
    from gpt_trader.features.trade_ideas.workflow import InvalidTransitionError

    service, broker, executor, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "pending", ".4")
    broker.place_order.side_effect = SystemExit("lost acknowledgement")
    with pytest.raises(SystemExit):
        executor.execute("pending")
    with pytest.raises(InvalidTransitionError, match="Pending reduction"):
        service.record_closeout_attribution(
            "entry",
            actor_id="operator",
            resolution=CloseoutResolution.THESIS_TARGET,
            realized_profit_loss_amount=Decimal("10"),
        )
    assert service.get("entry").closeout_attribution is None
    assert position(service, "entry").reserved == Decimal(".4")
    assert service.paper_accounting().realized_profit_loss_total == 0


def test_denied_invalid_proposal_remains_exportable_history(tmp_path, monkeypatch):
    from gpt_trader.core.order_intent import PositionOperation
    from gpt_trader.features.trade_ideas.migration import export_current, import_legacy, validate
    from gpt_trader.features.trade_ideas.policy import PolicyViolationError

    root = tmp_path / "source"
    service, _, _, _ = setup_position(root, monkeypatch)
    target = service.get("entry").idea
    idea = replace(
        target,
        decision_id="invalid",
        broker_ticket=type(target.broker_ticket)(),
        position_operation=PositionOperation("reduce", "entry", "b" * 64),
    )
    service.propose(idea, actor_id="proposer")
    with pytest.raises(PolicyViolationError):
        service.approve("invalid", actor_id="operator", reason="refused")
    counts = validate(root)
    export_current(root, tmp_path / "export")
    import_legacy(tmp_path / "export", tmp_path / "import")
    assert validate(tmp_path / "import") == counts
    assert TradeIdeaService(tmp_path / "import").get("invalid").idea == idea


def test_future_receipt_never_becomes_an_effective_reduction(tmp_path, monkeypatch):
    from gpt_trader.features.idea_execution import PaperExecutionError

    service, broker, executor, price = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    price[0] = Decimal("90")
    place = broker.place_order.side_effect
    broker.place_order.side_effect = lambda **kwargs: replace(
        place(**kwargs), updated_at=NOW + timedelta(days=1)
    )
    with pytest.raises(PaperExecutionError, match="impossible execution time"):
        executor.execute("reduce")
    assert service.execution_journal.entries()["reduce"].receipt is None
    projected = position(service, "entry")
    assert projected.remaining == 1 and projected.reserved == Decimal(".4")
    assert service.approval_budget_context().open_notional == 100
    assert executor.recover_receipts()["unresolved"]
    assert broker.place_order.call_count == 2


def test_manual_final_attribution_uses_durable_resolution_time(tmp_path, monkeypatch):
    service, broker, executor, price = setup_position(tmp_path, monkeypatch)
    proposal(service, "close", None, "close")
    price[0] = Decimal("110")
    monkeypatch.setattr(
        executor, "_reconcile_receipt", Mock(side_effect=SystemExit("after receipt"))
    )
    with pytest.raises(SystemExit):
        executor.execute("close")
    monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(days=1))
    record = service.record_closeout_attribution(
        "entry",
        actor_id="operator",
        resolution=CloseoutResolution.THESIS_TARGET,
        realized_profit_loss_amount=Decimal("10"),
    )
    assert record.timestamp == NOW
    assert position(service, "entry").remaining == 0
    PaperIdeaExecutor(service, broker).recover_receipts()
    assert len(service.query_closeout_records().items) == 1


def test_actual_hybrid_refuses_short_target_before_buying(tmp_path, monkeypatch):
    from gpt_trader.features.brokerages.paper.hybrid import HybridPaperBroker
    from gpt_trader.features.trade_ideas import TradeDirection

    service, _, _, _ = setup_position(tmp_path, monkeypatch, TradeDirection.SHORT)
    proposal(service, "reduce", ".4")
    client = Mock()
    broker = HybridPaperBroker(client=client)
    place = Mock(wraps=broker.place_order)
    monkeypatch.setattr(broker, "place_order", place)
    with pytest.raises(IdeaNotExecutableError, match="spot-long"):
        PaperIdeaExecutor(service, broker, now_factory=lambda: NOW).execute("reduce")
    place.assert_not_called()
    assert client.mock_calls == []
    assert "reduce" not in service.execution_journal.entries()
    assert position(service, "entry").remaining == 1


@pytest.mark.parametrize("state", ["submitted", "filled"])
def test_orphaned_admitted_execution_never_becomes_zero_realization(tmp_path, monkeypatch, state):
    from gpt_trader.features.trade_ideas.migration import validate

    service, broker, executor, price = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    price[0] = Decimal("90")
    if state == "submitted":
        broker.place_order.side_effect = SystemExit("lost ack")
        with pytest.raises(SystemExit):
            executor.execute("reduce")
    else:
        executor.execute("reduce")
    repository = service.execution_journal.repository
    with repository.transaction(write=True):
        repository.connection.execute("DROP TRIGGER immutable_events_delete")
        rows = repository.connection.execute(
            "SELECT sequence,payload FROM events WHERE stream=?", (EXECUTION_STREAM,)
        ).fetchall()
        for sequence, raw in rows:
            if json.loads(raw).get("decision_id") == "reduce":
                repository.connection.execute("DELETE FROM events WHERE sequence=?", (sequence,))
    for read in [
        lambda: position(service, "entry"),
        service.paper_accounting,
        lambda: validate(tmp_path),
    ]:
        with pytest.raises(ExecutionJournalIntegrityError, match="missing its audit or journal"):
            read()


def test_recomputed_receipt_checksum_cannot_override_audited_fill(tmp_path, monkeypatch):
    service, _, executor, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    executor.execute("reduce")
    repository = service.execution_journal.repository
    with repository.transaction(write=True):
        repository.connection.execute("DROP TRIGGER immutable_events_update")
        rows = repository.connection.execute(
            "SELECT sequence,payload FROM events WHERE stream=?", (EXECUTION_STREAM,)
        ).fetchall()
        receipt_hash = None
        for sequence, raw in rows:
            event = json.loads(raw)
            if event.get("decision_id") != "reduce":
                continue
            if event["kind"] == "receipt":
                event["receipt"]["price"] = "90"
                receipt_hash = payload_hash(event["receipt"])
                event["receipt_hash"] = receipt_hash
            elif event["kind"] == "reconciled":
                event["receipt_hash"] = receipt_hash
            else:
                continue
            repository.connection.execute(
                "UPDATE events SET payload=? WHERE sequence=?", (json.dumps(event), sequence)
            )
    assert service.execution_journal.entries()["reduce"].receipt["price"] == "90"
    with pytest.raises(ExecutionJournalIntegrityError, match="Audited reduction fill conflicts"):
        service.paper_accounting()


def test_execution_before_submission_is_quarantined_before_receipt_write(tmp_path, monkeypatch):
    from gpt_trader.features.idea_execution import PaperExecutionError

    service, broker, executor, _ = setup_position(tmp_path, monkeypatch)
    proposal(service, "reduce", ".4")
    place = broker.place_order.side_effect
    broker.place_order.side_effect = lambda **kwargs: replace(
        place(**kwargs), updated_at=NOW - timedelta(seconds=1)
    )
    with pytest.raises(PaperExecutionError, match="impossible execution time"):
        executor.execute("reduce")
    assert service.execution_journal.entries()["reduce"].receipt is None
    assert position(service, "entry").reserved == Decimal(".4")
