"""Broker-neutral trade-idea records.

Implements the record contract from the accepted Pre-Migration Decision
Framework (docs/DIRECTION.md): every AI-generated idea
carries its thesis, entry zone, invalidation, max loss, sizing, horizon, and
confidence before any human review. Broker payloads are derived artifacts
created after approval; nothing in this module is venue-specific beyond the
ticket envelope.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from gpt_trader.core.instruments import Instrument, ProductType

_SAFE_DECISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_safe_decision_id(value: str) -> bool:
    """Return whether a decision id is one filesystem-safe path segment."""
    return bool(_SAFE_DECISION_ID.fullmatch(value))


class AutonomyMode(str, Enum):
    RESEARCH_ONLY = "research_only"
    HUMAN_APPROVED_EXECUTION = "human_approved_execution"
    BOUNDED_AUTONOMY = "bounded_autonomy"


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    SPREAD = "spread"
    HEDGE = "hedge"
    NO_TRADE = "no_trade"


class ConfidenceLabel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TicketVenue(str, Enum):
    COINBASE = "coinbase"
    MANUAL = "manual"
    PAPER = "paper"
    NONE = "none"


class TicketStatus(str, Enum):
    NOT_CREATED = "not_created"
    DRAFTED = "drafted"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"


def _validate_finite_decimal(value: Decimal | None, field: str) -> None:
    if value is not None and not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _validate_non_negative_decimal(value: Decimal | None, field: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field} must be non-negative")


def _decimal_or_none(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    parsed = Decimal(str(value))
    _validate_finite_decimal(parsed, field)
    return parsed


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _object_payload(value: Any, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _string_sequence(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a JSON array of strings")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field}[{index}] must be a string")
    return tuple(value)


def _string_value(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_timezone_aware(value: datetime | None, field: str) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")


def _parse_expires_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("time_horizon.expires_at must be an ISO datetime string")
    expires_at = datetime.fromisoformat(value)
    _require_timezone_aware(expires_at, "time_horizon.expires_at")
    return expires_at


@dataclass(frozen=True, slots=True)
class EntryZone:
    """Price range or conditional trigger that defines a valid entry."""

    lower: Decimal | None = None
    upper: Decimal | None = None
    trigger: str = ""

    def __post_init__(self) -> None:
        _validate_finite_decimal(self.lower, "entry_zone.lower")
        _validate_finite_decimal(self.upper, "entry_zone.upper")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lower": _decimal_to_str(self.lower),
            "upper": _decimal_to_str(self.upper),
            "trigger": self.trigger,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EntryZone:
        payload = _object_payload(payload, "entry_zone")
        return cls(
            lower=_decimal_or_none(payload.get("lower"), "entry_zone.lower"),
            upper=_decimal_or_none(payload.get("upper"), "entry_zone.upper"),
            trigger=_string_value(payload.get("trigger", ""), "entry_zone.trigger"),
        )


@dataclass(frozen=True, slots=True)
class MaxLoss:
    """Dollar and percent loss estimate, including assumptions."""

    amount: Decimal | None = None
    percent_of_account: Decimal | None = None
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_finite_decimal(self.amount, "max_loss.amount")
        _validate_finite_decimal(self.percent_of_account, "max_loss.percent_of_account")
        _validate_non_negative_decimal(self.amount, "max_loss.amount")
        _validate_non_negative_decimal(self.percent_of_account, "max_loss.percent_of_account")

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": _decimal_to_str(self.amount),
            "percent_of_account": _decimal_to_str(self.percent_of_account),
            "assumptions": list(self.assumptions),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MaxLoss:
        payload = _object_payload(payload, "max_loss")
        return cls(
            amount=_decimal_or_none(payload.get("amount"), "max_loss.amount"),
            percent_of_account=_decimal_or_none(
                payload.get("percent_of_account"), "max_loss.percent_of_account"
            ),
            assumptions=_string_sequence(payload.get("assumptions", ()), "max_loss.assumptions"),
        )


@dataclass(frozen=True, slots=True)
class ExitPlan:
    """Machine-readable stop and target levels for resolving a filled position.

    The free-text ``invalidation`` / ``target_exit`` fields carry the human
    rationale; these numeric levels let the paper-exit monitor resolve a filled
    idea against later marks (issue #1218). Optional so human-authored ideas and
    pre-existing records omit it — and serialized only when set, so a record
    without a plan hashes exactly as it did before this field existed.
    """

    stop: Decimal | None = None
    target: Decimal | None = None

    def __post_init__(self) -> None:
        _validate_finite_decimal(self.stop, "exit_plan.stop")
        _validate_finite_decimal(self.target, "exit_plan.target")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop": _decimal_to_str(self.stop),
            "target": _decimal_to_str(self.target),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExitPlan:
        payload = _object_payload(payload, "exit_plan")
        return cls(
            stop=_decimal_or_none(payload.get("stop"), "exit_plan.stop"),
            target=_decimal_or_none(payload.get("target"), "exit_plan.target"),
        )


@dataclass(frozen=True, slots=True)
class SizingRecommendation:
    """Proposed size and how it was derived."""

    quantity: Decimal | None = None
    notional: Decimal | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        _validate_finite_decimal(self.quantity, "sizing_recommendation.quantity")
        _validate_finite_decimal(self.notional, "sizing_recommendation.notional")

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": _decimal_to_str(self.quantity),
            "notional": _decimal_to_str(self.notional),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SizingRecommendation:
        payload = _object_payload(payload, "sizing_recommendation")
        return cls(
            quantity=_decimal_or_none(payload.get("quantity"), "sizing_recommendation.quantity"),
            notional=_decimal_or_none(payload.get("notional"), "sizing_recommendation.notional"),
            rationale=_string_value(
                payload.get("rationale", ""), "sizing_recommendation.rationale"
            ),
        )


@dataclass(frozen=True, slots=True)
class TimeHorizon:
    """Expected holding period plus a hard review/expiry deadline."""

    expected_hold: str = ""
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_timezone_aware(self.expires_at, "time_horizon.expires_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_hold": self.expected_hold,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TimeHorizon:
        payload = _object_payload(payload, "time_horizon")
        return cls(
            expected_hold=_string_value(
                payload.get("expected_hold", ""), "time_horizon.expected_hold"
            ),
            expires_at=_parse_expires_at(payload.get("expires_at")),
        )


@dataclass(frozen=True, slots=True)
class Confidence:
    """Bounded confidence label plus why it may be wrong."""

    label: ConfidenceLabel
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label.value, "rationale": self.rationale}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Confidence:
        payload = _object_payload(payload, "confidence")
        return cls(
            label=ConfidenceLabel(payload["label"]),
            rationale=_string_value(payload.get("rationale", ""), "confidence.rationale"),
        )


@dataclass(frozen=True, slots=True)
class BrokerTicket:
    """Venue envelope for the derived broker payload; broker-neutral by default."""

    venue: TicketVenue = TicketVenue.NONE
    status: TicketStatus = TicketStatus.NOT_CREATED

    def to_dict(self) -> dict[str, Any]:
        return {"venue": self.venue.value, "status": self.status.value}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BrokerTicket:
        payload = _object_payload(payload, "broker_ticket")
        return cls(
            venue=TicketVenue(payload.get("venue", TicketVenue.NONE.value)),
            status=TicketStatus(payload.get("status", TicketStatus.NOT_CREATED.value)),
        )


@dataclass(frozen=True, slots=True)
class TradeIdea:
    """Immutable trade-idea record.

    Workflow state is intentionally not a field here: state lives in the
    append-only audit log (see audit.py) so the original thesis is never
    overwritten when reviewers change or approve a ticket.
    """

    decision_id: str
    autonomy_mode: AutonomyMode
    thesis: str
    instrument: str
    product_type: ProductType
    direction: TradeDirection
    entry_zone: EntryZone
    invalidation: str
    target_exit: str
    max_loss: MaxLoss
    sizing_recommendation: SizingRecommendation
    time_horizon: TimeHorizon
    data_used: tuple[str, ...]
    confidence: Confidence
    failure_mode: str
    do_not_trade_if: tuple[str, ...] = ()
    broker_ticket: BrokerTicket = field(default_factory=BrokerTicket)
    exit_plan: ExitPlan | None = None

    def __post_init__(self) -> None:
        if not is_safe_decision_id(self.decision_id):
            raise ValueError("decision_id must be a safe path segment")

    @property
    def instrument_info(self) -> Instrument:
        """Structured view of the instrument string (derived, never persisted).

        The persisted schema keeps ``instrument`` as a plain string: idea
        records are content-hashed and audit events chain on those hashes, so
        the serialized form must not change. Raises
        :class:`gpt_trader.core.instruments.InstrumentParseError` when the
        string does not match a known shape.
        """
        return Instrument.parse(self.instrument)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision_id": self.decision_id,
            "autonomy_mode": self.autonomy_mode.value,
            "thesis": self.thesis,
            "instrument": self.instrument,
            "product_type": self.product_type.value,
            "direction": self.direction.value,
            "entry_zone": self.entry_zone.to_dict(),
            "invalidation": self.invalidation,
            "target_exit": self.target_exit,
            "max_loss": self.max_loss.to_dict(),
            "sizing_recommendation": self.sizing_recommendation.to_dict(),
            "time_horizon": self.time_horizon.to_dict(),
            "data_used": list(self.data_used),
            "confidence": self.confidence.to_dict(),
            "failure_mode": self.failure_mode,
            "do_not_trade_if": list(self.do_not_trade_if),
            "broker_ticket": self.broker_ticket.to_dict(),
        }
        # Serialized only when set: a record without an exit plan must produce
        # the exact canonical form (and record_hash) it did before this field
        # existed, so audit integrity over the pre-existing trail is preserved.
        if self.exit_plan is not None:
            payload["exit_plan"] = self.exit_plan.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TradeIdea:
        return cls(
            decision_id=payload["decision_id"],
            autonomy_mode=AutonomyMode(payload["autonomy_mode"]),
            thesis=_string_value(payload["thesis"], "thesis"),
            instrument=_string_value(payload["instrument"], "instrument"),
            product_type=ProductType(payload["product_type"]),
            direction=TradeDirection(payload["direction"]),
            entry_zone=EntryZone.from_dict(payload.get("entry_zone", {})),
            invalidation=_string_value(payload["invalidation"], "invalidation"),
            target_exit=_string_value(payload["target_exit"], "target_exit"),
            max_loss=MaxLoss.from_dict(payload.get("max_loss", {})),
            sizing_recommendation=SizingRecommendation.from_dict(
                payload.get("sizing_recommendation", {})
            ),
            time_horizon=TimeHorizon.from_dict(payload.get("time_horizon", {})),
            data_used=_string_sequence(payload.get("data_used", ()), "data_used"),
            confidence=Confidence.from_dict(payload["confidence"]),
            failure_mode=_string_value(payload["failure_mode"], "failure_mode"),
            do_not_trade_if=_string_sequence(payload.get("do_not_trade_if", ()), "do_not_trade_if"),
            broker_ticket=BrokerTicket.from_dict(payload.get("broker_ticket", {})),
            exit_plan=(
                ExitPlan.from_dict(payload["exit_plan"]) if "exit_plan" in payload else None
            ),
        )

    def record_hash(self) -> str:
        """Stable content hash used by audit events to pin the record version."""
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
