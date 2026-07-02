"""Evidence and locking contract of the Stage-1 paper cycle (issue #1150).

Every turn — including failed ones — appends exactly one manifest row and
persists its snapshot/report artifacts; a lock-refused start is not a turn and
leaves no row. Turn behavior (propose/execute/expiry legs) is pinned in
test_cycle.py; shared builders live in conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from filelock import FileLock
from tests.unit.gpt_trader.features.idea_execution.conftest import (
    build_cycle_idea,
    crossover_series,
    flat_series,
    make_cycle_runner,
    manifest_rows,
    snapshot,
    snapshot_provider,
)

from gpt_trader.features.idea_execution import PaperCycleLockError
from gpt_trader.features.trade_ideas import AuditAction, TradeIdeaService


class TestEvidenceContract:
    def test_every_turn_appends_exactly_one_manifest_row(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        runner = make_cycle_runner(cycle_service, tmp_path, proposers=[])
        provider = snapshot_provider(snapshot(flat_series("BTC-USD")))
        runner.run(provider)
        runner.run(provider)
        rows = manifest_rows(tmp_path)
        assert len(rows) == 2
        assert len({row["run_id"] for row in rows}) == 2
        assert all(row["outcome"] == "completed" for row in rows)

    def test_failed_turn_appends_honest_failure_row(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        def broken_provider():
            raise ConnectionError("market data unreachable")

        with pytest.raises(ConnectionError, match="market data unreachable"):
            make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(broken_provider)

        (row,) = manifest_rows(tmp_path)
        assert row["outcome"] == "failed"
        assert "market data unreachable" in row["error"]
        assert row["finished_at"]

    def test_snapshot_artifact_is_persisted_with_hash(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        result = make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
            snapshot_provider(snapshot(flat_series("BTC-USD")))
        )
        snapshot_path = Path(result.snapshot["path"])
        assert snapshot_path.exists()
        assert result.snapshot["sha256"]
        assert result.snapshot["symbols"] == ["BTC-USD"]
        report_path = snapshot_path.parent / "report.json"
        assert report_path.exists()

    def test_audit_chain_stays_intact_across_turns(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-005"
        cycle_service.propose(build_cycle_idea(decision_id), actor_id="test-proposer")
        cycle_service.approve(decision_id, actor_id="test-operator", reason="test approval")
        make_cycle_runner(cycle_service, tmp_path).run(
            snapshot_provider(snapshot(crossover_series("BTC-USD")))
        )
        events = cycle_service.audit_log.verify()
        filled = [event for event in events if event.action is AuditAction.FILLED]
        assert len(filled) == 1
        assert filled[0].actor_id == "paper-cycle"


class TestLocking:
    def test_lock_releases_even_when_manifest_write_fails(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        cycle_root = tmp_path / "cycle"
        # A directory at the manifest path makes the append raise.
        (cycle_root / "manifest.jsonl").mkdir(parents=True)
        runner = make_cycle_runner(cycle_service, tmp_path, proposers=[])
        provider = snapshot_provider(snapshot(flat_series("BTC-USD")))
        with pytest.raises(IsADirectoryError):
            runner.run(provider)

        # The lock must not leak: a follow-up turn acquires it normally.
        (cycle_root / "manifest.jsonl").rmdir()
        result = runner.run(provider)
        assert result.run_id

    def test_concurrent_turn_is_refused_without_manifest_row(
        self, cycle_service: TradeIdeaService, tmp_path: Path
    ) -> None:
        cycle_root = tmp_path / "cycle"
        cycle_root.mkdir(parents=True)
        held = FileLock(str(cycle_root / "cycle.lock"))
        held.acquire()
        try:
            with pytest.raises(PaperCycleLockError, match="already running"):
                make_cycle_runner(cycle_service, tmp_path, proposers=[]).run(
                    snapshot_provider(snapshot(flat_series("BTC-USD")))
                )
        finally:
            held.release()
        assert manifest_rows(tmp_path) == []
