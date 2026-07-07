"""One turn of the Stage-1 paper cycle (issue #1150).

A turn strings the existing rails together with no live authority: sweep
expired ideas, run the configured proposers over one market snapshot, paper-
execute approved ideas priced by that same snapshot, and leave evidence.
Recurrence is supplied by an external scheduler (launchd/cron); nothing in this
module knows or decides a cadence.

Evidence contract: every turn — including failed ones — appends exactly one
JSON line to ``<cycle_root>/manifest.jsonl`` and writes its artifacts under
``<cycle_root>/runs/<run_id>/``. "N consecutive unattended days" is computed
from the manifest alone.

The turn acquires a lock for its whole duration so overlapping scheduler
ticks cannot interleave; a second invocation is refused with
``PaperCycleLockError`` and leaves no manifest row (a refused start is not a
turn). Approval remains a separate event: the proposer leg only queues ideas,
and the execution leg touches only human-approved ideas or system approvals
that pass the Stage 2 paper-execution gate, through the paper-only lane in this
slice.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from gpt_trader.errors import ValidationError
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution.executor import (
    IdeaNotExecutableError,
    PaperExecutionError,
    PaperIdeaExecutor,
    paper_auto_execution_gate_evidence,
)
from gpt_trader.features.idea_execution.exit_monitor import resolve_filled_ideas
from gpt_trader.features.trade_ideas import (
    ActorType,
    AuditAction,
    AuditEvent,
    MarketSnapshot,
    Proposer,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaView,
    market_snapshot_to_payload,
)
from gpt_trader.features.trade_ideas.report import build_trade_idea_track_record_report

DEFAULT_CYCLE_ACTOR_ID = "paper-cycle"

# States in which an instrument already has a live idea in the pipeline. The
# baseline proposers re-emit an ongoing signal on consecutive turns (their
# crossover window spans several bars), so unattended operation must not queue
# near-duplicate ideas for the same instrument.
_OPEN_STATES = frozenset(
    {
        TradeIdeaState.PROPOSED,
        TradeIdeaState.NEEDS_CHANGES,
        TradeIdeaState.APPROVED,
        TradeIdeaState.SUBMITTED,
    }
)

SnapshotProvider = Callable[[], tuple[MarketSnapshot, str]]
"""Returns the turn's snapshot plus a human-readable source reference.

The provider owns acquisition (network fetch or file load); the runner never
imports a market-data client, so the paper lane's import topology stays
paper-only.
"""


def _instrument_key(instrument: str) -> str:
    return instrument.casefold()


def _latest_approval_event(view: TradeIdeaView) -> AuditEvent | None:
    for event in reversed(view.events):
        if event.action is AuditAction.APPROVED:
            return event
    return None


class PaperCycleLockError(ValidationError):
    """Raised when another cycle turn already holds the ideas-root lock."""


@dataclass(frozen=True, slots=True)
class ProposerTurn:
    """Outcome of one proposer over the turn's snapshot."""

    proposer_id: str
    proposal_count: int
    proposed_decision_ids: tuple[str, ...]
    skipped_open_instruments: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposer_id": self.proposer_id,
            "proposal_count": self.proposal_count,
            "proposed_decision_ids": list(self.proposed_decision_ids),
            "skipped_open_instruments": list(self.skipped_open_instruments),
        }


@dataclass(frozen=True, slots=True)
class ExecutionTurn:
    """Outcome of the execute-approved leg."""

    enabled: bool
    executed: tuple[dict[str, Any], ...] = ()
    skipped: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "executed": list(self.executed),
            "skipped": list(self.skipped),
        }


@dataclass(frozen=True, slots=True)
class PaperCycleResult:
    """One completed turn; failed turns raise and leave only a manifest row."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    snapshot: dict[str, Any]
    expired_decision_ids: tuple[str, ...]
    attributed_decision_ids: tuple[str, ...]
    resolved_decision_ids: tuple[str, ...]
    proposer_turns: tuple[ProposerTurn, ...]
    execution: ExecutionTurn
    queue: dict[str, Any] = field(default_factory=dict)
    report_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "outcome": "completed",
            "snapshot": self.snapshot,
            "expired_decision_ids": list(self.expired_decision_ids),
            "attributed_decision_ids": list(self.attributed_decision_ids),
            "resolved_decision_ids": list(self.resolved_decision_ids),
            "proposers": [turn.to_dict() for turn in self.proposer_turns],
            "execution": self.execution.to_dict(),
            "queue": self.queue,
            "report": self.report_summary,
        }


class PaperCycleRunner:
    """Runs one turn of the Stage-1 paper cycle.

    The runner is composition only: proposers, the paper broker, and the
    snapshot provider are injected, so cadence, granularity, and strategy set
    are the caller's configuration — never constants here.

    The broker is the deterministic one specifically: the execution leg prices
    every fill from the turn's own snapshot via ``set_mark``, which is the
    honesty contract of a scheduled offline turn. ``HybridPaperBroker`` prices
    from its own live feed, so wiring it here needs a pricing decision first
    (deferred with its CLI wiring).
    """

    def __init__(
        self,
        service: TradeIdeaService,
        *,
        cycle_root: Path,
        proposers: Sequence[Proposer],
        broker: DeterministicBroker,
        execute_approved: bool = True,
        actor_id: str = DEFAULT_CYCLE_ACTOR_ID,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._service = service
        self._cycle_root = cycle_root
        self._proposers = tuple(proposers)
        self._broker = broker
        self._execute_approved = execute_approved
        self._actor_id = actor_id
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        # The executor must share the turn's clock: with an injected clock
        # (deterministic or historical turns) a wall-clock expiry check would
        # refuse ideas the rest of the turn still considers live.
        self._executor = PaperIdeaExecutor(service, broker, now_factory=self._now_factory)

    def run(self, snapshot_provider: SnapshotProvider) -> PaperCycleResult:
        """Run one turn; append exactly one manifest row whatever happens."""
        lock = FileLock(str(self._cycle_root / "cycle.lock"))
        self._cycle_root.mkdir(parents=True, exist_ok=True)
        try:
            lock.acquire(timeout=0)
        except Timeout as error:
            raise PaperCycleLockError(
                "Another paper-cycle turn is already running for this ideas root",
                field="cycle_root",
                value=str(self._cycle_root),
            ) from error

        started_at = self._now_factory()
        run_id = f"cycle-{started_at:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
        row: dict[str, Any] = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "outcome": "failed",
            "error": None,
        }
        try:
            result = self._run_steps(run_id, started_at, snapshot_provider, row)
            row.update(result.to_dict())
            return result
        except Exception as error:
            row["error"] = f"{type(error).__name__}: {error}"
            row["finished_at"] = self._now_factory().isoformat()
            raise
        finally:
            try:
                self._append_manifest_row(row)
            finally:
                lock.release()

    def _run_steps(
        self,
        run_id: str,
        started_at: datetime,
        snapshot_provider: SnapshotProvider,
        row: dict[str, Any],
    ) -> PaperCycleResult:
        run_dir = self._cycle_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        expired_views = self._service.expire_due_ideas(
            actor_id=self._actor_id,
            reason="paper-cycle expiry sweep",
            actor_type=ActorType.SYSTEM,
        )
        expired_decision_ids = tuple(view.idea.decision_id for view in expired_views)
        row["expired_decision_ids"] = list(expired_decision_ids)

        # Attribute every expired-unexecuted idea so the closeout trail self-heals
        # each turn instead of waiting for a manual `ideas closeout record`. An
        # EXPIRED idea never opened a position (SUBMITTED cannot expire), so this
        # records an EXPIRY closeout with realized P&L unavailable — keeping
        # attribution coverage honest at 100% without inventing an outcome. Also
        # backfills any pre-existing unattributed expiries (issue #1214). Filled
        # ideas need an exit model and are left for issue #1218.
        attributed = self._service.auto_attribute_expired_ideas()
        attributed_decision_ids = tuple(record.decision_id for record in attributed)
        row["attributed_decision_ids"] = list(attributed_decision_ids)

        # Capture the execution candidates before any slow turn work — the
        # snapshot fetch is a network call and the approval CLI does not take
        # the cycle lock, so an approval landing anywhere inside the turn must
        # wait for the next turn: approval lands between turns, never inside
        # one. Only the local expiry sweep runs first, so already-stale ideas
        # are not captured.
        approved_before_turn = tuple(
            view.idea.decision_id for view in self._service.list_views(TradeIdeaState.APPROVED)
        )

        snapshot, snapshot_reference = snapshot_provider()
        snapshot_info = self._persist_snapshot(snapshot, snapshot_reference, run_dir)
        row["snapshot"] = snapshot_info

        proposer_turns: list[ProposerTurn] = []
        for proposer in self._proposers:
            turn = self._run_proposer(proposer, snapshot, snapshot_reference)
            proposer_turns.append(turn)
            row["proposers"] = [item.to_dict() for item in proposer_turns]

        execution = (
            self._execute_approved_ideas(snapshot, approved_before_turn)
            if self._execute_approved
            else ExecutionTurn(enabled=False)
        )
        row["execution"] = execution.to_dict()

        # Resolve open filled positions against this turn's candles (first touch
        # of the plan's target/stop, or mark-to-market once expired) so realized
        # P&L lands on the trail — the evidence the Stage 1->2 calibration /
        # expectancy / benchmark gates read (issue #1218). Runs before the report
        # so the fresh closeouts are reflected in this turn's artifact.
        resolved = resolve_filled_ideas(
            self._service, snapshot, now=self._now_factory(), actor_id=self._actor_id
        )
        resolved_decision_ids = tuple(record.decision_id for record in resolved)
        row["resolved_decision_ids"] = list(resolved_decision_ids)

        report = build_trade_idea_track_record_report(self._service, now=self._now_factory())
        (run_dir / "report.json").write_text(
            f"{json.dumps(report, indent=2, sort_keys=True, default=str)}\n",
            encoding="utf-8",
        )
        report_summary = {
            "row_count": report.get("row_count"),
            "closeouts": report.get("closeouts"),
        }

        queue_status = self._service.queue_status()
        queue_summary = {
            "as_of": queue_status.as_of.isoformat(),
            "proposed_count": queue_status.proposed_count,
            "needs_changes_count": queue_status.needs_changes_count,
            "pending_total": queue_status.pending_total,
        }

        return PaperCycleResult(
            run_id=run_id,
            started_at=started_at,
            finished_at=self._now_factory(),
            snapshot=snapshot_info,
            expired_decision_ids=expired_decision_ids,
            attributed_decision_ids=attributed_decision_ids,
            resolved_decision_ids=resolved_decision_ids,
            proposer_turns=tuple(proposer_turns),
            execution=execution,
            queue=queue_summary,
            report_summary=report_summary,
        )

    def _run_proposer(
        self,
        proposer: Proposer,
        snapshot: MarketSnapshot,
        snapshot_reference: str,
    ) -> ProposerTurn:
        candidates = proposer.propose(snapshot)

        # An instrument is busy while an idea for it is open, and also while a
        # filled trade awaits closeout attribution: until the outcome is
        # recorded, the trade is unresolved and the same ongoing signal must
        # not pyramid a second position onto it.
        busy_instruments: dict[str, tuple[str, str]] = {}
        known_decision_ids: set[str] = set()
        for view in self._service.list_views():
            known_decision_ids.add(view.idea.decision_id)
            instrument_key = _instrument_key(view.idea.instrument)
            if view.state in _OPEN_STATES:
                busy_instruments[instrument_key] = (
                    view.idea.decision_id,
                    "instrument already has an open idea",
                )
            elif view.state is TradeIdeaState.FILLED and view.closeout_attribution is None:
                busy_instruments.setdefault(
                    instrument_key,
                    (view.idea.decision_id, "instrument has a filled idea awaiting closeout"),
                )
        admitted = []
        skipped: list[dict[str, str]] = []
        for idea in candidates:
            # Deterministic proposers emit the same decision_id for the same
            # snapshot, so a rerun over a saved snapshot must skip
            # idempotently rather than fail the turn on a duplicate id.
            if idea.decision_id in known_decision_ids:
                skipped.append(
                    {
                        "instrument": idea.instrument,
                        "reason": "decision id already recorded (idempotent rerun)",
                        "existing_decision_id": idea.decision_id,
                    }
                )
                continue
            instrument_key = _instrument_key(idea.instrument)
            busy = busy_instruments.get(instrument_key)
            if busy is not None:
                existing_decision_id, reason = busy
                skipped.append(
                    {
                        "instrument": idea.instrument,
                        "reason": reason,
                        "existing_decision_id": existing_decision_id,
                    }
                )
                continue
            admitted.append(idea)
            known_decision_ids.add(idea.decision_id)
            busy_instruments[instrument_key] = (
                idea.decision_id,
                "instrument already has an open idea",
            )

        proposed_decision_ids: tuple[str, ...] = ()
        if admitted:
            batch = tuple(admitted)
            self._service.validate_new_proposals(batch)
            views = self._service.propose_batch(
                batch,
                actor_id=proposer.proposer_id,
                actor_type=ActorType.AI,
                reason="paper-cycle scheduled proposal",
                evidence=(
                    f"proposer_id={proposer.proposer_id}",
                    f"snapshot_reference={snapshot_reference}",
                    f"snapshot_source={snapshot.source}",
                    f"snapshot_as_of={snapshot.as_of.isoformat()}",
                ),
            )
            proposed_decision_ids = tuple(view.idea.decision_id for view in views)

        return ProposerTurn(
            proposer_id=proposer.proposer_id,
            proposal_count=len(proposed_decision_ids),
            proposed_decision_ids=proposed_decision_ids,
            skipped_open_instruments=tuple(skipped),
        )

    def _execute_approved_ideas(
        self,
        snapshot: MarketSnapshot,
        approved_before_turn: tuple[str, ...],
    ) -> ExecutionTurn:
        marks = {
            _instrument_key(series.symbol): series.candles[-1].close
            for series in snapshot.series
            if series.candles
        }
        executed: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for decision_id in approved_before_turn:
            view = self._service.get(decision_id)
            if view.state is not TradeIdeaState.APPROVED:
                # The state moved while the turn was running (for example a
                # human cancelled it); the lane would refuse it anyway, so
                # record the observation instead of attempting execution.
                skipped.append(
                    {
                        "decision_id": decision_id,
                        "reason": f"state changed to {view.state.value} during the turn",
                    }
                )
                continue
            approval_event = _latest_approval_event(view)
            approval_actor_type = approval_event.actor_type if approval_event else None
            if approval_actor_type is not ActorType.HUMAN:
                gate_evidence = paper_auto_execution_gate_evidence(
                    self._service,
                    approval_event,
                    now=self._now_factory(),
                )
                if gate_evidence is None:
                    actor = approval_actor_type.value if approval_actor_type else "none"
                    skipped.append(
                        {
                            "decision_id": decision_id,
                            "reason": (
                                "approval actor_type "
                                f"'{actor}' is not executable by the Stage-1 paper cycle"
                            ),
                        }
                    )
                    continue
            mark = marks.get(_instrument_key(view.idea.instrument))
            if mark is None:
                skipped.append(
                    {
                        "decision_id": decision_id,
                        "reason": (
                            "no fresh mark for instrument "
                            f"{view.idea.instrument} in this turn's snapshot"
                        ),
                    }
                )
                continue
            self._broker.set_mark(view.idea.instrument, mark)
            try:
                result = self._executor.execute(decision_id, actor_id=self._actor_id)
            except (IdeaNotExecutableError, PaperExecutionError) as error:
                # Typed refusals are the lane's admission rules working (for
                # example the idea expired between sweep and execution); they
                # are evidence, not turn failures.
                skipped.append({"decision_id": decision_id, "reason": str(error)})
                continue
            executed.append(
                {
                    "decision_id": result.decision_id,
                    "order_id": result.order_id,
                    "client_order_id": result.client_order_id,
                    "symbol": result.symbol,
                    "side": result.side,
                    "quantity": str(result.quantity),
                    "fill_price": str(result.fill_price) if result.fill_price is not None else None,
                    "final_state": result.final_state,
                }
            )
        return ExecutionTurn(enabled=True, executed=tuple(executed), skipped=tuple(skipped))

    def _persist_snapshot(
        self,
        snapshot: MarketSnapshot,
        snapshot_reference: str,
        run_dir: Path,
    ) -> dict[str, Any]:
        payload = market_snapshot_to_payload(snapshot)
        canonical = json.dumps(payload, indent=2, sort_keys=True)
        snapshot_path = run_dir / "snapshot.json"
        snapshot_path.write_text(f"{canonical}\n", encoding="utf-8")
        return {
            "reference": snapshot_reference,
            "path": str(snapshot_path),
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "source": snapshot.source,
            "as_of": snapshot.as_of.isoformat(),
            "symbols": [series.symbol for series in snapshot.series],
            "granularities": sorted({series.granularity for series in snapshot.series}),
        }

    def _append_manifest_row(self, row: dict[str, Any]) -> None:
        manifest_path = self._cycle_root / "manifest.jsonl"
        with manifest_path.open("a", encoding="utf-8") as manifest_file:
            manifest_file.write(f"{json.dumps(row, sort_keys=True, default=str)}\n")
