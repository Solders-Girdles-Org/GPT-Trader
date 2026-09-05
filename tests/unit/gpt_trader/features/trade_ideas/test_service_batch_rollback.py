from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    build_trade_idea,
    reconciliation_service,
)

from gpt_trader.features.trade_ideas import TradeIdea


def test_failed_batch_rollback_preserves_other_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = reconciliation_service(tmp_path)
    service.current_budget()
    other = reconciliation_service(tmp_path)
    first, second, foreign = (
        build_trade_idea(decision_id=f"trade-20260612-{name}")
        for name in ("first", "second", "foreign")
    )
    paused, release, started = Event(), Event(), Event()
    save = service._store.save

    def fail_second(idea: TradeIdea) -> str:
        result = save(idea)
        if idea == second:
            paused.set()
            assert release.wait(10)
            raise RuntimeError("failed after durable-row insertion")
        return result

    def foreign_writer() -> None:
        started.set()
        other.propose(foreign, actor_id="other")

    monkeypatch.setattr(service._store, "save", fail_second)
    with ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(service.propose_batch, (first, second), actor_id="batch")
        assert paused.wait(10)
        committed = pool.submit(foreign_writer)
        assert started.wait(10)
        assert not committed.done()
        release.set()
        with pytest.raises(RuntimeError, match="durable-row"):
            failed.result(timeout=10)
        committed.result(timeout=10)
    assert [view.idea.decision_id for view in service.list_views()] == [foreign.decision_id]
    assert [event.decision_id for event in service.audit_log.verify()] == [foreign.decision_id]


def test_single_proposal_failure_has_no_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = reconciliation_service(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(service, "append_audit", fail)
    with pytest.raises(RuntimeError, match="audit write failed"):
        service.propose(build_trade_idea(), actor_id="test")
    assert service._store.list_decision_ids() == []
    assert service.audit_log.read_events() == []
