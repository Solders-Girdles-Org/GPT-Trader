"""Trade-idea read-model contracts.

Inert data types shared by the trade-idea service and its review adapters: the
list/query/result/view value objects and the not-found / duplicate / pre-approval
errors. The audited service logic that produces them lives in service.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Generic, TypeVar

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import AuditEvent
from gpt_trader.features.trade_ideas.closeout import CloseoutAttribution
from gpt_trader.features.trade_ideas.models import (
    ConfidenceLabel,
    TradeDirection,
    TradeIdea,
)
from gpt_trader.features.trade_ideas.workflow import TradeIdeaState

_QueryItem = TypeVar("_QueryItem")


class UnknownTradeIdeaError(ValidationError):
    """Raised when a decision_id has no stored record."""


class DuplicateTradeIdeaError(ValidationError):
    """Raised when a new proposal reuses an existing decision_id."""


class PreApprovalBrokerTicketError(ValidationError):
    """Raised when a proposed record carries broker-specific ticket state."""


@dataclass(frozen=True, slots=True)
class TradeIdeaView:
    """A record plus its derived workflow state and full history."""

    idea: TradeIdea
    state: TradeIdeaState
    events: tuple[AuditEvent, ...]
    closeout_attribution: CloseoutAttribution | None = None


@dataclass(frozen=True, slots=True)
class TradeIdeaQueryPage(Generic[_QueryItem]):
    """Stable read-only query page for audit and closeout reporting."""

    items: tuple[_QueryItem, ...]
    total_count: int
    limit: int | None
    offset: int


class TradeIdeaListSortKey(str, Enum):
    DECISION_ID = "decision_id"
    STATE = "state"
    INSTRUMENT = "instrument"
    DIRECTION = "direction"
    CONFIDENCE = "confidence"
    MAX_LOSS_PCT = "max_loss_pct"
    EXPIRES_AT = "expires_at"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


@dataclass(frozen=True, slots=True)
class TradeIdeaListQuery:
    """Shared read query for trade-idea review adapters."""

    state: TradeIdeaState | None = None
    instrument: str | None = None
    decision_id: str | None = None
    direction: TradeDirection | None = None
    min_confidence: ConfidenceLabel | None = None
    max_confidence: ConfidenceLabel | None = None
    updated_since: datetime | None = None
    updated_until: datetime | None = None
    sort_by: TradeIdeaListSortKey | None = None
    descending: bool = False
    limit: int | None = None
    offset: int = 0


@dataclass(frozen=True, slots=True)
class TradeIdeaListResult:
    """Filtered trade-idea views plus pagination metadata."""

    views: tuple[TradeIdeaView, ...]
    total_count: int
    offset: int
    limit: int | None
    has_more: bool

    @property
    def returned_count(self) -> int:
        return len(self.views)


@dataclass(frozen=True, slots=True)
class TradeIdeaQueueExpiration:
    """One pending idea expiring inside the queue warning window."""

    decision_id: str
    state: TradeIdeaState
    instrument: str
    expires_at: datetime
    deadline_type: str
    seconds_until_expiry: int

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "state": self.state.value,
            "instrument": self.instrument,
            "expires_at": self.expires_at.isoformat(),
            "deadline_type": self.deadline_type,
            "seconds_until_expiry": self.seconds_until_expiry,
        }


@dataclass(frozen=True, slots=True)
class TradeIdeaQueueStatus:
    """Read-only queue health for pending human review."""

    as_of: datetime
    warning_window_hours: int
    proposed_count: int
    needs_changes_count: int
    upcoming_expirations: tuple[TradeIdeaQueueExpiration, ...]

    @property
    def pending_total(self) -> int:
        return self.proposed_count + self.needs_changes_count

    @property
    def upcoming_expiration_count(self) -> int:
        return len(self.upcoming_expirations)

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "warning_window_hours": self.warning_window_hours,
            "counts": {
                "proposed": self.proposed_count,
                "needs_changes": self.needs_changes_count,
                "pending_total": self.pending_total,
                "upcoming_expirations": self.upcoming_expiration_count,
            },
            "upcoming_expirations": [
                expiration.to_dict() for expiration in self.upcoming_expirations
            ],
        }
