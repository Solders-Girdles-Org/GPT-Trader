from __future__ import annotations

import multiprocessing
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    attest_account_equity,
    build_trade_idea,
    reconciliation_service,
)

from gpt_trader.features.trade_ideas import ActorType, PolicyViolationError, TradeIdeaService
from gpt_trader.features.trade_ideas.migration import export_current, import_legacy, legacy_files
from gpt_trader.features.trade_ideas.persistence import StateMigrationRequired, StateRepository


def test_two_public_approvals_share_the_ticket_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = reconciliation_service(tmp_path)
    second = reconciliation_service(tmp_path)
    attest_account_equity(first)
    first.update_budget(
        replace(first.current_budget(), version=3, max_concurrent_approved_tickets=1),
        ActorType.HUMAN,
        "owner",
    )
    ideas = [build_trade_idea(decision_id=f"trade-20260612-{name}") for name in ("first", "second")]
    first.propose_batch(tuple(ideas), actor_id="test")
    checked, release, entered = Event(), Event(), Event()
    original = first.kernel.check_approval

    def paused(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        checked.set()
        assert release.wait(10)
        return result

    def approve_other():
        entered.set()
        return second.approve(ideas[1].decision_id, "owner", "reviewed")

    monkeypatch.setattr(first.kernel, "check_approval", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted = pool.submit(first.approve, ideas[0].decision_id, "owner", "reviewed")
        assert checked.wait(10)
        denied = pool.submit(approve_other)
        assert entered.wait(10)
        assert not denied.done()
        release.set()
        admitted.result(timeout=10)
        with pytest.raises(PolicyViolationError, match="concurrent"):
            denied.result(timeout=10)
    assert first.open_approved_count() == 1
    assert len(first.audit_log.verify()) == 3


def test_kernel_preview_cannot_commit_after_budget_changes(tmp_path: Path) -> None:
    service = reconciliation_service(tmp_path)
    attest_account_equity(service)
    idea = build_trade_idea()
    service.propose(idea, actor_id="test")
    check = service.kernel.check_approval(idea, actor_type=ActorType.HUMAN)
    assert check.admitted
    service.update_budget(
        replace(service.current_budget(), version=3, max_loss_per_idea_pct=Decimal("0")),
        ActorType.HUMAN,
        "owner",
    )
    with pytest.raises(PolicyViolationError):
        service.kernel.record_approval(idea, check, actor_id="owner", reason="stale preview")
    assert service.open_approved_count() == 0


def _crash(root: str) -> None:
    service = reconciliation_service(Path(root))
    with service._repository.transaction(write=True):
        service.propose(build_trade_idea(), actor_id="crashing-process")
        os._exit(23)


def test_process_exit_rolls_back_record_and_event(tmp_path: Path) -> None:
    StateRepository(tmp_path).initialize()
    process = multiprocessing.get_context("spawn").Process(target=_crash, args=(str(tmp_path),))
    process.start()
    process.join(timeout=15)
    assert process.exitcode == 23
    service = reconciliation_service(tmp_path)
    assert service.list_views() == []
    assert service.audit_log.verify() == []


def test_current_export_roundtrip_after_new_writes_and_legacy_write_refusal(tmp_path: Path) -> None:
    source = tmp_path / "source"
    service = reconciliation_service(source)
    attest_account_equity(service)
    idea = build_trade_idea()
    service.propose(idea, actor_id="test")
    baseline = tmp_path / "legacy"
    export_current(source, baseline)
    with pytest.raises(StateMigrationRequired):
        reconciliation_service(baseline).approve(idea.decision_id, "owner", "must migrate")
    migrated = tmp_path / "migrated"
    import_legacy(baseline, migrated)
    unchanged = tmp_path / "unchanged"
    export_current(migrated, unchanged)
    assert legacy_files(baseline) == legacy_files(unchanged)
    client = reconciliation_service(migrated)
    client.approve(idea.decision_id, "owner", "reviewed")
    current = tmp_path / "current"
    export_current(migrated, current)
    roundtrip = tmp_path / "roundtrip"
    import_legacy(current, roundtrip)
    assert reconciliation_service(roundtrip).get(idea.decision_id) == client.get(idea.decision_id)
    assert len(reconciliation_service(roundtrip).audit_log.verify()) == 2


def test_database_rejects_event_and_record_rewrites(tmp_path: Path) -> None:
    service = reconciliation_service(tmp_path)
    service.propose(build_trade_idea(), actor_id="test")
    with sqlite3.connect(service._repository.path) as connection:
        for statement in (
            "DELETE FROM events",
            "UPDATE events SET payload='{}'",
            "DELETE FROM versions",
            "UPDATE versions SET payload='{}'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)


def test_reading_empty_state_does_not_initialize_storage(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    assert TradeIdeaService(root).list_views() == []
    assert not root.exists()


def test_policy_exception_after_admission_does_not_commit_partial_trade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = reconciliation_service(tmp_path)
    attest_account_equity(service)
    idea = build_trade_idea()
    service.propose(idea, actor_id="test")
    commit = service.kernel.record_approval

    def fail_after_admission(*args, **kwargs):
        commit(*args, **kwargs)
        raise PolicyViolationError("injected late denial", ["injected"])

    monkeypatch.setattr(service.kernel, "record_approval", fail_after_admission)
    with pytest.raises(PolicyViolationError, match="late denial"):
        service.approve(idea.decision_id, "owner", "reviewed")
    assert service.open_approved_count() == 0
    assert len(service.audit_log.verify()) == 1


def test_policy_denial_does_not_commit_autonomy_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gpt_trader.features.trade_ideas import AutonomyMode

    service = reconciliation_service(tmp_path)
    service.current_autonomy()
    append = service._autonomy_log.append

    def fail_after_upgrade(entry):
        append(entry)
        raise PolicyViolationError("injected late denial", ["injected"])

    monkeypatch.setattr(service._autonomy_log, "append", fail_after_upgrade)
    with pytest.raises(PolicyViolationError):
        service.set_autonomy_mode(
            AutonomyMode.BOUNDED_AUTONOMY,
            actor_type=ActorType.HUMAN,
            actor_id="owner",
            reason="test grant",
        )
    assert service.peek_autonomy().mode is AutonomyMode.HUMAN_APPROVED_EXECUTION
    assert len(service.autonomy_history()) == 1
