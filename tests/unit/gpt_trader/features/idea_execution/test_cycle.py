"""Turn-contract tests for the Stage-1 paper cycle runner (issue #1150).

These pin the evidence and safety contract of one unattended turn: exactly one
manifest row per turn (including failed turns), lock-protected concurrency, the
open-instrument dedup filter, snapshot-priced execution of human-approved ideas
only, and the absence of any cadence knowledge in the runner.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from filelock import FileLock

from gpt_trader.core import Candle
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution import (
    PaperCycleLockError,
    PaperCycleRunner,
)
from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    AuditAction,
    AutonomyMode,
    BaselineProposer,
    Confidence,
    ConfidenceLabel,
    EntryZone,
    MarketSnapshot,
    MaxLoss,
    ProductType,
    SizingRecommendation,
    SymbolSeries,
    TimeHorizon,
    TradeDirection,
    TradeIdea,
    TradeIdeaService,
    TradeIdeaState,
)

_NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _crossover_series(symbol: str) -> SymbolSeries:
    """Sixty hourly candles whose 10/50 MA golden cross lands in the last 3 bars."""
    closes = [Decimal("100")] * 57 + [Decimal("120"), Decimal("125"), Decimal("130")]
    volumes = [Decimal("10")] * 59 + [Decimal("100")]
    start = _NOW - timedelta(hours=len(closes))
    candles = tuple(
        Candle(
            ts=start + timedelta(hours=index),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
        )
        for index, (close, volume) in enumerate(zip(closes, volumes, strict=True))
    )
    return SymbolSeries(symbol=symbol, granularity="ONE_HOUR", candles=candles)


def _flat_series(symbol: str) -> SymbolSeries:
    """Sixty flat candles: never triggers the baseline proposer."""
    start = _NOW - timedelta(hours=60)
    candles = tuple(
        Candle(
            ts=start + timedelta(hours=index),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("10"),
        )
        for index in range(60)
    )
    return SymbolSeries(symbol=symbol, granularity="ONE_HOUR", candles=candles)


def _snapshot(*series: SymbolSeries) -> MarketSnapshot:
    return MarketSnapshot(as_of=_NOW, source="test:fixture", series=tuple(series))


def _snapshot_provider(snapshot: MarketSnapshot):
    return lambda: (snapshot, "test:fixture:reference")


def _build_idea(decision_id: str, *, instrument: str = "BTC-USD") -> TradeIdea:
    return TradeIdea(
        decision_id=decision_id,
        autonomy_mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
        thesis="Cycle test: eligible fixture record",
        instrument=instrument,
        product_type=ProductType.SPOT,
        direction=TradeDirection.LONG,
        entry_zone=EntryZone(lower=Decimal("60000"), upper=Decimal("61500")),
        invalidation="Daily close below 58000",
        target_exit="Take profit at 67000",
        max_loss=MaxLoss(
            amount=Decimal("250"),
            percent_of_account=Decimal("1.5"),
            assumptions=("Fill at zone midpoint",),
        ),
        sizing_recommendation=SizingRecommendation(
            quantity=Decimal("0.1"),
            notional=Decimal("6075"),
            rationale="Fixture sizing",
        ),
        time_horizon=TimeHorizon(
            expected_hold="3-10 days",
            expires_at=_NOW + timedelta(days=7),
        ),
        data_used=("test:fixture:no-market-data",),
        confidence=Confidence(label=ConfidenceLabel.MEDIUM, rationale="fixture"),
        failure_mode="Not applicable in tests",
        do_not_trade_if=("fixture record",),
    )


@pytest.fixture
def service(tmp_path: Path) -> TradeIdeaService:
    trade_idea_service = TradeIdeaService(tmp_path / "ideas", now_factory=lambda: _NOW)
    trade_idea_service.update_budget(
        replace(
            DEFAULT_RISK_BUDGET,
            version=2,
            account_equity=Decimal("25000"),
            reason="test: attest scratch equity",
        ),
        actor_type=ActorType.HUMAN,
        actor_id="test-operator",
    )
    return trade_idea_service


def _runner(
    service: TradeIdeaService,
    tmp_path: Path,
    *,
    proposers: list | None = None,
    execute_approved: bool = True,
) -> PaperCycleRunner:
    return PaperCycleRunner(
        service,
        cycle_root=tmp_path / "cycle",
        proposers=proposers if proposers is not None else [BaselineProposer()],
        broker=DeterministicBroker(),
        execute_approved=execute_approved,
        now_factory=lambda: _NOW,
    )


def _manifest_rows(tmp_path: Path) -> list[dict]:
    manifest_path = tmp_path / "cycle" / "manifest.jsonl"
    if not manifest_path.exists():
        return []
    return [json.loads(line) for line in manifest_path.read_text().splitlines() if line]


class TestProposeLeg:
    def test_crossover_snapshot_proposes_idea(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        result = _runner(service, tmp_path).run(
            _snapshot_provider(_snapshot(_crossover_series("BTC-USD")))
        )

        (proposer_turn,) = result.proposer_turns
        assert proposer_turn.proposal_count == 1
        (decision_id,) = proposer_turn.proposed_decision_ids
        view = service.get(decision_id)
        assert view.state is TradeIdeaState.PROPOSED
        assert view.events[0].actor_type is ActorType.AI
        assert view.events[0].actor_id == proposer_turn.proposer_id

    def test_flat_snapshot_is_honest_noop(self, service: TradeIdeaService, tmp_path: Path) -> None:
        result = _runner(service, tmp_path).run(
            _snapshot_provider(_snapshot(_flat_series("BTC-USD")))
        )
        (proposer_turn,) = result.proposer_turns
        assert proposer_turn.proposal_count == 0
        rows = _manifest_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "completed"

    def test_open_instrument_is_not_reproposed(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        runner = _runner(service, tmp_path)
        snapshot_provider = _snapshot_provider(_snapshot(_crossover_series("BTC-USD")))
        first = runner.run(snapshot_provider)
        assert first.proposer_turns[0].proposal_count == 1

        second = runner.run(snapshot_provider)
        (proposer_turn,) = second.proposer_turns
        assert proposer_turn.proposal_count == 0
        (skip,) = proposer_turn.skipped_open_instruments
        assert skip["instrument"] == "BTC-USD"
        assert skip["existing_decision_id"] == first.proposer_turns[0].proposed_decision_ids[0]


class TestExecuteApprovedLeg:
    def test_executes_approved_idea_at_snapshot_mark(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-001"
        service.propose(_build_idea(decision_id), actor_id="test-proposer")
        service.approve(decision_id, actor_id="test-operator", reason="test approval")

        result = _runner(service, tmp_path, proposers=[]).run(
            _snapshot_provider(_snapshot(_crossover_series("BTC-USD")))
        )

        (executed,) = result.execution.executed
        assert executed["decision_id"] == decision_id
        assert executed["client_order_id"] == decision_id
        # Priced from the turn's snapshot: the fixture's last close is 130.
        assert executed["fill_price"] == "130"
        assert service.get(decision_id).state is TradeIdeaState.FILLED

    def test_skips_approved_idea_without_fresh_mark(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-002"
        service.propose(_build_idea(decision_id, instrument="ETH-USD"), actor_id="test-proposer")
        service.approve(decision_id, actor_id="test-operator", reason="test approval")

        result = _runner(service, tmp_path, proposers=[]).run(
            _snapshot_provider(_snapshot(_crossover_series("BTC-USD")))
        )

        assert result.execution.executed == ()
        (skip,) = result.execution.skipped
        assert skip["decision_id"] == decision_id
        assert "no fresh mark" in skip["reason"]
        assert service.get(decision_id).state is TradeIdeaState.APPROVED

    def test_execution_leg_can_be_disabled(self, service: TradeIdeaService, tmp_path: Path) -> None:
        decision_id = "trade-20260703-cycle-003"
        service.propose(_build_idea(decision_id), actor_id="test-proposer")
        service.approve(decision_id, actor_id="test-operator", reason="test approval")

        result = _runner(service, tmp_path, proposers=[], execute_approved=False).run(
            _snapshot_provider(_snapshot(_crossover_series("BTC-USD")))
        )

        assert result.execution.enabled is False
        assert service.get(decision_id).state is TradeIdeaState.APPROVED

    def test_proposed_ideas_are_never_executed_in_same_turn(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        # The turn proposes AND has the execution leg enabled; the freshly
        # proposed idea must stay PROPOSED because approval is a human event.
        result = _runner(service, tmp_path).run(
            _snapshot_provider(_snapshot(_crossover_series("BTC-USD")))
        )
        (decision_id,) = result.proposer_turns[0].proposed_decision_ids
        assert result.execution.executed == ()
        assert service.get(decision_id).state is TradeIdeaState.PROPOSED


class TestExpirySweep:
    def test_stale_idea_is_swept_before_proposing(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-004"
        stale = replace(
            _build_idea(decision_id),
            time_horizon=TimeHorizon(
                expected_hold="3-10 days",
                expires_at=_NOW - timedelta(hours=1),
            ),
        )
        service.propose(stale, actor_id="test-proposer")

        result = _runner(service, tmp_path, proposers=[]).run(
            _snapshot_provider(_snapshot(_flat_series("BTC-USD")))
        )

        assert result.expired_decision_ids == (decision_id,)
        assert service.get(decision_id).state is TradeIdeaState.EXPIRED


class TestEvidenceContract:
    def test_every_turn_appends_exactly_one_manifest_row(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        runner = _runner(service, tmp_path, proposers=[])
        snapshot_provider = _snapshot_provider(_snapshot(_flat_series("BTC-USD")))
        runner.run(snapshot_provider)
        runner.run(snapshot_provider)
        rows = _manifest_rows(tmp_path)
        assert len(rows) == 2
        assert len({row["run_id"] for row in rows}) == 2
        assert all(row["outcome"] == "completed" for row in rows)

    def test_failed_turn_appends_honest_failure_row(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        def broken_provider():
            raise ConnectionError("market data unreachable")

        with pytest.raises(ConnectionError, match="market data unreachable"):
            _runner(service, tmp_path, proposers=[]).run(broken_provider)

        (row,) = _manifest_rows(tmp_path)
        assert row["outcome"] == "failed"
        assert "market data unreachable" in row["error"]
        assert row["finished_at"]

    def test_snapshot_artifact_is_persisted_with_hash(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        result = _runner(service, tmp_path, proposers=[]).run(
            _snapshot_provider(_snapshot(_flat_series("BTC-USD")))
        )
        snapshot_path = Path(result.snapshot["path"])
        assert snapshot_path.exists()
        assert result.snapshot["sha256"]
        assert result.snapshot["symbols"] == ["BTC-USD"]
        report_path = snapshot_path.parent / "report.json"
        assert report_path.exists()

    def test_audit_chain_stays_intact_across_turns(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        decision_id = "trade-20260703-cycle-005"
        service.propose(_build_idea(decision_id), actor_id="test-proposer")
        service.approve(decision_id, actor_id="test-operator", reason="test approval")
        _runner(service, tmp_path).run(_snapshot_provider(_snapshot(_crossover_series("BTC-USD"))))
        events = service.audit_log.verify()
        filled = [event for event in events if event.action is AuditAction.FILLED]
        assert len(filled) == 1
        assert filled[0].actor_id == "paper-cycle"


class TestLocking:
    def test_concurrent_turn_is_refused_without_manifest_row(
        self, service: TradeIdeaService, tmp_path: Path
    ) -> None:
        cycle_root = tmp_path / "cycle"
        cycle_root.mkdir(parents=True)
        held = FileLock(str(cycle_root / "cycle.lock"))
        held.acquire()
        try:
            with pytest.raises(PaperCycleLockError, match="already running"):
                _runner(service, tmp_path, proposers=[]).run(
                    _snapshot_provider(_snapshot(_flat_series("BTC-USD")))
                )
        finally:
            held.release()
        assert _manifest_rows(tmp_path) == []
