"""Append-only JSONL audit log for trade-idea workflow events.

Implements the audit contract from docs/DIRECTION.md:
every state change is a new event pinned to a hash of the record version it
acted on. Events are never rewritten or deleted; the current workflow state of
an idea is always derived from its latest event.

Never write secrets, API keys, session tokens, or account credentials into
event fields.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.workflow import (
    InvalidTransitionError,
    TradeIdeaState,
    validate_transition,
)


class ActorType(str, Enum):
    AI = "ai"
    HUMAN = "human"
    SYSTEM = "system"
    VENUE = "venue"


class AuditAction(str, Enum):
    PROPOSED = "proposed"
    CHANGED = "changed"
    AUTO_APPROVAL_SKIPPED = "auto_approval_skipped"
    AUTO_EXECUTION_SKIPPED = "auto_execution_skipped"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AuditIntegrityError(ValidationError):
    """Raised when an append would break audit-log sequencing or integrity."""


_ACTION_AFTER_STATES: dict[AuditAction, TradeIdeaState] = {
    AuditAction.PROPOSED: TradeIdeaState.PROPOSED,
    AuditAction.CHANGED: TradeIdeaState.NEEDS_CHANGES,
    AuditAction.AUTO_APPROVAL_SKIPPED: TradeIdeaState.PROPOSED,
    AuditAction.AUTO_EXECUTION_SKIPPED: TradeIdeaState.APPROVED,
    AuditAction.APPROVED: TradeIdeaState.APPROVED,
    AuditAction.REJECTED: TradeIdeaState.REJECTED,
    AuditAction.SUBMITTED: TradeIdeaState.SUBMITTED,
    AuditAction.FILLED: TradeIdeaState.FILLED,
    AuditAction.CANCELLED: TradeIdeaState.CANCELLED,
    AuditAction.EXPIRED: TradeIdeaState.EXPIRED,
}


def new_event_id() -> str:
    return f"evt-{uuid.uuid4().hex}"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    timestamp: datetime
    decision_id: str
    actor_type: ActorType
    actor_id: str
    action: AuditAction
    before_state: TradeIdeaState | None
    after_state: TradeIdeaState
    reason: str
    record_hash: str
    evidence: tuple[str, ...] = ()
    venue: str = ""
    external_order_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "decision_id": self.decision_id,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "action": self.action.value,
            "before_state": self.before_state.value if self.before_state else None,
            "after_state": self.after_state.value,
            "reason": self.reason,
            "record_hash": self.record_hash,
            "evidence": list(self.evidence),
            "venue": self.venue,
            "external_order_id": self.external_order_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AuditEvent:
        raw_before = payload.get("before_state")
        return cls(
            event_id=payload["event_id"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            decision_id=payload["decision_id"],
            actor_type=ActorType(payload["actor_type"]),
            actor_id=payload["actor_id"],
            action=AuditAction(payload["action"]),
            before_state=TradeIdeaState(raw_before) if raw_before else None,
            after_state=TradeIdeaState(payload["after_state"]),
            reason=payload.get("reason", ""),
            record_hash=payload["record_hash"],
            evidence=tuple(payload.get("evidence", ())),
            venue=payload.get("venue", ""),
            external_order_id=payload.get("external_order_id", ""),
        )


class TradeIdeaAuditLog:
    """Append-only JSONL audit log, one event object per line.

    Appends are validated against the state machine and against the last
    recorded state for the same decision, so an out-of-order or conflicting
    event cannot silently corrupt workflow history.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: AuditEvent) -> None:
        last_state = self.current_state(event.decision_id)
        if event.before_state != last_state:
            recorded = last_state.value if last_state else "none"
            claimed = event.before_state.value if event.before_state else "none"
            raise AuditIntegrityError(
                f"Audit event for '{event.decision_id}' claims before_state "
                f"'{claimed}' but the log records '{recorded}'",
                field="before_state",
                value=claimed,
            )
        _validate_action_after_state(event)
        validate_transition(event.before_state, event.after_state)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read_events(self, decision_id: str | None = None) -> list[AuditEvent]:
        if not self._path.exists():
            return []
        events: list[AuditEvent] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                event = AuditEvent.from_dict(json.loads(line))
                if decision_id is None or event.decision_id == decision_id:
                    events.append(event)
        return events

    def verify(self) -> list[AuditEvent]:
        """Read the full log and verify per-decision state sequencing."""
        if not self._path.exists():
            return []

        events: list[AuditEvent] = []
        states: dict[str, TradeIdeaState | None] = {}
        with self._path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = AuditEvent.from_dict(json.loads(line))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise AuditIntegrityError(
                        f"Audit log line {line_number} is malformed: {error}",
                        field="line",
                        value=line_number,
                    ) from error

                expected_before = states.get(event.decision_id)
                if event.before_state != expected_before:
                    recorded = expected_before.value if expected_before else "none"
                    claimed = event.before_state.value if event.before_state else "none"
                    raise AuditIntegrityError(
                        f"Audit log line {line_number} for '{event.decision_id}' claims "
                        f"before_state '{claimed}' but previous events record '{recorded}'",
                        field="before_state",
                        value=claimed,
                    )
                _validate_action_after_state(event, line_number=line_number)
                try:
                    validate_transition(event.before_state, event.after_state)
                except InvalidTransitionError as error:
                    raise AuditIntegrityError(
                        f"Audit log line {line_number} has an illegal transition: {error}",
                        field="line",
                        value=line_number,
                    ) from error

                states[event.decision_id] = event.after_state
                events.append(event)

        return events

    def current_state(self, decision_id: str) -> TradeIdeaState | None:
        events = self.read_events(decision_id)
        if not events:
            return None
        return events[-1].after_state


def _validate_action_after_state(event: AuditEvent, *, line_number: int | None = None) -> None:
    expected_after_state = _ACTION_AFTER_STATES[event.action]
    if event.after_state is expected_after_state:
        return

    location = f"Audit log line {line_number}" if line_number is not None else "Audit event"
    raise AuditIntegrityError(
        f"{location} for '{event.decision_id}' has action '{event.action.value}' "
        f"but after_state '{event.after_state.value}'; expected "
        f"after_state '{expected_after_state.value}'",
        field="after_state",
        value=event.after_state.value,
    )
