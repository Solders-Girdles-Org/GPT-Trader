"""Persistent, audited autonomy-level state for trade-idea workflows.

Implements the accepted decision docs/decisions/persistent-autonomy-state.md
(Option A): the autonomy level gets its own append-only, versioned audit log
beside the risk budget log, mirroring the ``RiskBudgetLog`` pattern. Each
entry records the mode, the actor type and id, the rationale, and (for
automatic ratchets) the breach evidence that triggered it.

Resolution fails closed: an absent log means the seeded default
``human_approved_execution`` (no AI submission); an unreadable or
integrity-broken log resolves to ``research_only`` and surfaces the error.

Transition rules: raising (or re-affirming) the level requires a human actor;
lowering is open to any actor so the breach ratchet can act without a human
in the loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from gpt_trader.core.risk_units import trading_day
from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import ActorType
from gpt_trader.features.trade_ideas.models import AutonomyMode

DEFAULT_AUTONOMY_MODE = AutonomyMode.HUMAN_APPROVED_EXECUTION
FAIL_CLOSED_AUTONOMY_MODE = AutonomyMode.RESEARCH_ONLY
RATCHET_ACTOR_ID = "autonomy-ratchet"

AUTONOMY_RANK: dict[AutonomyMode, int] = {
    AutonomyMode.RESEARCH_ONLY: 0,
    AutonomyMode.HUMAN_APPROVED_EXECUTION: 1,
    AutonomyMode.BOUNDED_AUTONOMY: 2,
}

AUTONOMY_SOURCE_LOG = "autonomy_state_log"
AUTONOMY_SOURCE_SEEDED_DEFAULT = "seeded_default"
AUTONOMY_SOURCE_FAIL_CLOSED = "fail_closed"


class AutonomyIntegrityError(ValidationError):
    """Raised when the autonomy log is malformed or an append breaks sequencing."""


@dataclass(frozen=True, slots=True)
class AutonomyStateEntry:
    """One appended autonomy-level version plus the actor and rationale behind it."""

    version: int
    timestamp: datetime
    mode: AutonomyMode
    actor_type: ActorType
    actor_id: str
    reason: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("version must be positive")
        if not self.reason.strip():
            raise ValueError("reason must be a non-empty rationale")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "timestamp": self.timestamp.isoformat(),
            "mode": self.mode.value,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AutonomyStateEntry:
        return cls(
            version=int(payload["version"]),
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            mode=AutonomyMode(payload["mode"]),
            actor_type=ActorType(payload["actor_type"]),
            actor_id=payload["actor_id"],
            reason=payload["reason"],
            evidence=tuple(payload.get("evidence", ())),
        )


@dataclass(frozen=True, slots=True)
class AutonomyResolution:
    """Outcome of resolving the active autonomy mode through the audited log."""

    mode: AutonomyMode
    version: int | None
    source: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "version": self.version,
            "source": self.source,
            "error": self.error,
        }


class AutonomyStateLog:
    """Append-only JSONL log of autonomy-level versions; the last entry is current.

    Unlike the budget log, reads validate version sequencing so a tampered or
    truncated log is detected at resolution time and can fail closed.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def append(self, entry: AutonomyStateEntry) -> None:
        current = self.current()
        expected_version = 1 if current is None else current.version + 1
        if entry.version != expected_version:
            raise AutonomyIntegrityError(
                f"Autonomy state version must be {expected_version}, got {entry.version}",
                field="version",
                value=entry.version,
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.to_dict(), sort_keys=True, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def history(self) -> list[AutonomyStateEntry]:
        if not self._path.exists():
            return []
        entries: list[AutonomyStateEntry] = []
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = AutonomyStateEntry.from_dict(json.loads(line))
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                        raise AutonomyIntegrityError(
                            f"Autonomy state log line {line_number} is malformed: {error}",
                            field="line",
                            value=line_number,
                        ) from error
                    expected_version = len(entries) + 1
                    if entry.version != expected_version:
                        raise AutonomyIntegrityError(
                            f"Autonomy state log line {line_number} has version "
                            f"{entry.version}; expected {expected_version}",
                            field="version",
                            value=entry.version,
                        )
                    entries.append(entry)
        except OSError as error:
            raise AutonomyIntegrityError(
                f"Autonomy state log is unreadable: {error}",
                field="path",
                value=str(self._path),
            ) from error
        return entries

    def current(self) -> AutonomyStateEntry | None:
        entries = self.history()
        if not entries:
            return None
        return entries[-1]


def resolve_autonomy(log: AutonomyStateLog) -> AutonomyResolution:
    """Resolve the active mode from the log, failing closed on integrity errors."""
    try:
        entry = log.current()
    except AutonomyIntegrityError as error:
        return AutonomyResolution(
            mode=FAIL_CLOSED_AUTONOMY_MODE,
            version=None,
            source=AUTONOMY_SOURCE_FAIL_CLOSED,
            error=str(error),
        )
    if entry is None:
        return AutonomyResolution(
            mode=DEFAULT_AUTONOMY_MODE,
            version=None,
            source=AUTONOMY_SOURCE_SEEDED_DEFAULT,
        )
    return AutonomyResolution(
        mode=entry.mode,
        version=entry.version,
        source=AUTONOMY_SOURCE_LOG,
    )


def autonomy_transition_violations(
    *,
    current_mode: AutonomyMode,
    requested_mode: AutonomyMode,
    actor_type: ActorType,
) -> list[str]:
    """Return every reason this mode change must be refused; empty means allowed."""
    if AUTONOMY_RANK[requested_mode] < AUTONOMY_RANK[current_mode]:
        return []
    if actor_type is ActorType.HUMAN:
        return []
    return [
        f"Autonomy mode change '{current_mode.value}' -> '{requested_mode.value}' "
        f"requires a human actor; got actor_type '{actor_type.value}'"
    ]


def daily_loss_breach_evidence(
    *,
    same_day_realized_loss_pct: Decimal,
    max_daily_loss_pct: Decimal,
    budget_version: int,
    moment: datetime,
) -> tuple[str, ...] | None:
    """Return ratchet evidence when same-day realized loss breaches the budget cap.

    This is the trigger set shipped with the persistent-autonomy-state
    implementation (#1170), defined against the unified risk vocabulary from
    #1120: one appetite source (``max_daily_loss_pct`` on the active
    ``RiskBudget``) and one trading-day boundary
    (``gpt_trader.core.risk_units.trading_day``). Returns ``None`` when there
    is no breach.
    """
    if same_day_realized_loss_pct <= max_daily_loss_pct:
        return None
    return (
        f"same_day_realized_loss_pct={same_day_realized_loss_pct} exceeds "
        f"max_daily_loss_pct={max_daily_loss_pct} from risk budget version "
        f"{budget_version} on trading_day={trading_day(moment).isoformat()}",
    )


__all__ = [
    "AUTONOMY_RANK",
    "AUTONOMY_SOURCE_FAIL_CLOSED",
    "AUTONOMY_SOURCE_LOG",
    "AUTONOMY_SOURCE_SEEDED_DEFAULT",
    "AutonomyIntegrityError",
    "AutonomyResolution",
    "AutonomyStateEntry",
    "AutonomyStateLog",
    "DEFAULT_AUTONOMY_MODE",
    "FAIL_CLOSED_AUTONOMY_MODE",
    "RATCHET_ACTOR_ID",
    "autonomy_transition_violations",
    "daily_loss_breach_evidence",
    "resolve_autonomy",
]
