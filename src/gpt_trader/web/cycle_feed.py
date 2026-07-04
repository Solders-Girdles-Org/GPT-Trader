"""Read-only view over the paper-cycle manifest for the activity page.

``<ideas_root>/cycle/manifest.jsonl`` is the cycle runner's evidence contract
(``gpt_trader.features.idea_execution.cycle``): exactly one JSON line per
turn, including failed turns. The console renders that file as the durable
artifact it is rather than importing the runner — the runner's module pulls
in the paper execution lane, which the web console's frozen dependency set
forbids by decision (docs/decisions/adopt-operator-web-console.md).

Parsing is deliberately tolerant: a truncated or corrupt line is counted and
skipped, never fatal, because the feed must stay readable while a turn is
mid-append or after a crashed writer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ProposerActivity:
    """One proposer's leg of a turn, as recorded in the manifest row."""

    proposer_id: str
    proposal_count: int
    skipped_count: int


@dataclass(frozen=True, slots=True)
class CycleTurn:
    """One manifest row; failed turns carry an error and partial fields."""

    run_id: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    outcome: str
    error: str | None
    snapshot_source: str | None
    snapshot_symbols: tuple[str, ...]
    proposers: tuple[ProposerActivity, ...]
    execution_enabled: bool
    executed_count: int
    execution_skipped_count: int
    queue_pending_total: int | None

    @property
    def proposal_count(self) -> int:
        return sum(activity.proposal_count for activity in self.proposers)


@dataclass(frozen=True, slots=True)
class CycleFeed:
    """Newest-first window of turns plus whole-manifest counts."""

    turns: tuple[CycleTurn, ...]
    turn_count: int
    completed_count: int
    failed_count: int
    unreadable_line_count: int


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_proposers(value: Any) -> tuple[ProposerActivity, ...]:
    if not isinstance(value, list):
        return ()
    activities: list[ProposerActivity] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        skipped = item.get("skipped_open_instruments")
        activities.append(
            ProposerActivity(
                proposer_id=str(item.get("proposer_id", "—")),
                proposal_count=int(item.get("proposal_count", 0) or 0),
                skipped_count=len(skipped) if isinstance(skipped, list) else 0,
            )
        )
    return tuple(activities)


def _parse_turn(row: dict[str, Any]) -> CycleTurn:
    started_at = _parse_timestamp(row.get("started_at"))
    finished_at = _parse_timestamp(row.get("finished_at"))
    duration_seconds: float | None = None
    if started_at is not None and finished_at is not None:
        duration_seconds = (finished_at - started_at).total_seconds()

    snapshot = row.get("snapshot")
    snapshot_source: str | None = None
    snapshot_symbols: tuple[str, ...] = ()
    if isinstance(snapshot, dict):
        source = snapshot.get("source")
        snapshot_source = str(source) if source is not None else None
        symbols = snapshot.get("symbols")
        if isinstance(symbols, list):
            snapshot_symbols = tuple(str(symbol) for symbol in symbols)

    execution = row.get("execution")
    execution_enabled = False
    executed_count = 0
    execution_skipped_count = 0
    if isinstance(execution, dict):
        execution_enabled = bool(execution.get("enabled"))
        executed = execution.get("executed")
        executed_count = len(executed) if isinstance(executed, list) else 0
        skipped = execution.get("skipped")
        execution_skipped_count = len(skipped) if isinstance(skipped, list) else 0

    queue = row.get("queue")
    queue_pending_total: int | None = None
    if isinstance(queue, dict) and isinstance(queue.get("pending_total"), int):
        queue_pending_total = queue["pending_total"]

    error = row.get("error")
    return CycleTurn(
        run_id=str(row.get("run_id", "—")),
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        outcome=str(row.get("outcome", "unknown")),
        error=str(error) if error is not None else None,
        snapshot_source=snapshot_source,
        snapshot_symbols=snapshot_symbols,
        proposers=_parse_proposers(row.get("proposers")),
        execution_enabled=execution_enabled,
        executed_count=executed_count,
        execution_skipped_count=execution_skipped_count,
        queue_pending_total=queue_pending_total,
    )


def load_cycle_feed(manifest_path: Path, *, limit: int = 50) -> CycleFeed:
    """Load the manifest newest-first; a missing file is an empty feed."""
    turns: list[CycleTurn] = []
    unreadable_line_count = 0
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            unreadable_line_count += 1
            continue
        if not isinstance(row, dict):
            unreadable_line_count += 1
            continue
        try:
            turns.append(_parse_turn(row))
        except (TypeError, ValueError):
            # Valid JSON with a corrupt field (schema drift, manual repair)
            # is still an unreadable row, never a 500 on the activity page.
            unreadable_line_count += 1
    completed_count = sum(1 for turn in turns if turn.outcome == "completed")
    return CycleFeed(
        turns=tuple(reversed(turns[-limit:])),
        turn_count=len(turns),
        completed_count=completed_count,
        failed_count=len(turns) - completed_count,
        unreadable_line_count=unreadable_line_count,
    )
