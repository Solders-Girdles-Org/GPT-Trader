"""Deterministic broker-neutral ticket export payloads.

These payloads are render-only artifacts derived from approved trade-idea
records and audit state. They do not submit, modify, cancel, or preview broker
orders.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import AuditAction, AuditEvent
from gpt_trader.features.trade_ideas.budget import RiskBudget
from gpt_trader.features.trade_ideas.models import (
    AutonomyMode,
    BrokerTicket,
    TicketStatus,
    TicketVenue,
    TradeDirection,
    TradeIdea,
)
from gpt_trader.features.trade_ideas.workflow import (
    TERMINAL_STATES,
    InvalidTransitionError,
    TradeIdeaState,
)

TICKET_PAYLOAD_SCHEMA_VERSION = "gpt-trader.trade_idea_ticket.v1"
DEFAULT_VENUE_ORDER_TYPE = "operator_selected"
DEFAULT_TIME_IN_FORCE = "operator_selected"
EXPORT_TICKET_VENUES = frozenset({TicketVenue.COINBASE, TicketVenue.MANUAL, TicketVenue.ROBINHOOD})
MAX_CLIENT_ORDER_ID_LENGTH = 128

_CLIENT_ORDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class BrokerTicketExportRequest:
    """Broker-neutral venue placeholders requested by the operator."""

    venue: TicketVenue
    venue_order_type: str = DEFAULT_VENUE_ORDER_TYPE
    time_in_force: str = DEFAULT_TIME_IN_FORCE
    client_order_id: str | None = None

    @classmethod
    def from_values(
        cls,
        *,
        venue: str,
        venue_order_type: str = DEFAULT_VENUE_ORDER_TYPE,
        time_in_force: str = DEFAULT_TIME_IN_FORCE,
        client_order_id: str | None = None,
    ) -> BrokerTicketExportRequest:
        try:
            parsed_venue = TicketVenue(venue)
        except ValueError as error:
            allowed = ", ".join(sorted(item.value for item in EXPORT_TICKET_VENUES))
            raise ValidationError(
                f"Unsupported ticket export venue '{venue}'; expected one of: {allowed}",
                field="venue",
                value=venue,
            ) from error
        if parsed_venue not in EXPORT_TICKET_VENUES:
            allowed = ", ".join(sorted(item.value for item in EXPORT_TICKET_VENUES))
            raise ValidationError(
                f"Unsupported ticket export venue '{venue}'; expected one of: {allowed}",
                field="venue",
                value=venue,
            )

        order_type = _non_empty_text(venue_order_type, "venue_order_type")
        force = _non_empty_text(time_in_force, "time_in_force")
        normalized_client_id = _optional_client_order_id(client_order_id)
        return cls(
            venue=parsed_venue,
            venue_order_type=order_type,
            time_in_force=force,
            client_order_id=normalized_client_id,
        )


def build_broker_neutral_ticket_payload(
    *,
    idea: TradeIdea,
    state: TradeIdeaState,
    events: tuple[AuditEvent, ...],
    request: BrokerTicketExportRequest,
    budget: RiskBudget,
    budget_source: str,
    active_autonomy_mode: AutonomyMode,
    active_autonomy_source: str,
    export_time: datetime,
    approval_policy_violations: list[str],
) -> dict[str, Any]:
    """Build a stable broker-neutral ticket payload from audited local state."""
    _require_exportable_state(idea.decision_id, state, events, venue=request.venue)
    latest_event = events[-1]
    approval_event = _latest_event(events, AuditAction.APPROVED)
    terminal_event = latest_event if state in TERMINAL_STATES else None
    record_hash = idea.record_hash()
    client_order_id = request.client_order_id or _default_client_order_id(
        idea.decision_id,
        request.venue,
        record_hash,
    )
    order_side = _order_side_for_direction(idea.direction)
    exported_ticket = BrokerTicket(
        venue=request.venue,
        status=_ticket_status_for_state(state),
    )

    payload: dict[str, Any] = {
        "schema_version": TICKET_PAYLOAD_SCHEMA_VERSION,
        "generated_at": latest_event.timestamp.isoformat(),
        "decision_id": idea.decision_id,
        "record_hash": record_hash,
        "broker_ticket": {
            "source_record": idea.broker_ticket.to_dict(),
            "exported": exported_ticket.to_dict(),
        },
        "decision_metadata": {
            "autonomy_mode": idea.autonomy_mode.value,
            "instrument": idea.instrument,
            "product_type": idea.product_type.value,
            "direction": idea.direction.value,
            "confidence": idea.confidence.to_dict(),
            "thesis": idea.thesis,
        },
        "risk_sizing_snapshot": {
            "entry_zone": idea.entry_zone.to_dict(),
            "max_loss": idea.max_loss.to_dict(),
            "sizing_recommendation": idea.sizing_recommendation.to_dict(),
        },
        "timing_invalidation_constraints": {
            "time_horizon": idea.time_horizon.to_dict(),
            "invalidation": idea.invalidation,
            "target_exit": idea.target_exit,
            "failure_mode": idea.failure_mode,
            "do_not_trade_if": list(idea.do_not_trade_if),
        },
        "policy_budget_snapshot": {
            "evaluated_at": export_time.isoformat(),
            # The mode the export's violations were evaluated against — the
            # resolved active level from the autonomy state log, which can
            # differ from the mode the idea claimed at proposal time
            # (decision_metadata.autonomy_mode preserves that record field).
            "autonomy_mode": active_autonomy_mode.value,
            "autonomy_mode_source": active_autonomy_source,
            "risk_budget_source": budget_source,
            "risk_budget": budget.to_dict(),
            "approval_policy_violations": approval_policy_violations,
        },
        "venue_request": {
            "venue": request.venue.value,
            "venue_order_type": request.venue_order_type,
            "time_in_force": request.time_in_force,
            "client_order_id": client_order_id,
        },
        "venue_payload": {
            "venue": request.venue.value,
            "instrument": idea.instrument,
            "direction": idea.direction.value,
            "order_side": order_side,
            "quantity": idea.sizing_recommendation.to_dict()["quantity"],
            "notional": idea.sizing_recommendation.to_dict()["notional"],
            "entry_zone": idea.entry_zone.to_dict(),
            "venue_order_type": request.venue_order_type,
            "time_in_force": request.time_in_force,
            "client_order_id": client_order_id,
        },
        "provenance": {
            "created_event": _event_summary(events[0]),
            "approval_event": _event_summary(approval_event) if approval_event else None,
            "latest_event": _event_summary(latest_event),
            "terminal_event": _event_summary(terminal_event) if terminal_event else None,
            "audit_event_count": len(events),
            "data_used": list(idea.data_used),
        },
    }
    return _with_ticket_hash(payload)


def canonical_ticket_json(payload: dict[str, Any]) -> str:
    """Return byte-stable JSON for a ticket payload."""
    return json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


def _with_ticket_hash(payload: dict[str, Any]) -> dict[str, Any]:
    ticket_hash = hashlib.sha256(canonical_ticket_json(payload).encode("utf-8")).hexdigest()
    return {**payload, "ticket_hash": ticket_hash}


def _require_exportable_state(
    decision_id: str,
    state: TradeIdeaState,
    events: tuple[AuditEvent, ...],
    *,
    venue: TicketVenue,
) -> None:
    if venue is TicketVenue.ROBINHOOD and state is not TradeIdeaState.APPROVED:
        raise InvalidTransitionError(
            f"Trade idea '{decision_id}' must be approved for render-only Robinhood "
            f"ticket export; got '{state.value}'",
            field="after_state",
            value=state.value,
        )
    if state in {
        TradeIdeaState.APPROVED,
        TradeIdeaState.SUBMITTED,
        TradeIdeaState.FILLED,
        TradeIdeaState.CANCELLED,
    }:
        return
    if state is TradeIdeaState.EXPIRED and _latest_event(events, AuditAction.APPROVED):
        return
    allowed = "approved, submitted, filled, cancelled, or expired after approval"
    raise InvalidTransitionError(
        f"Trade idea '{decision_id}' must be {allowed} before ticket export; "
        f"got '{state.value}'",
        field="after_state",
        value=state.value,
    )


def _ticket_status_for_state(state: TradeIdeaState) -> TicketStatus:
    if state is TradeIdeaState.APPROVED:
        return TicketStatus.APPROVED
    if state in {TradeIdeaState.SUBMITTED, TradeIdeaState.FILLED}:
        return TicketStatus.SUBMITTED
    return TicketStatus.CANCELLED


def _latest_event(events: tuple[AuditEvent, ...], action: AuditAction) -> AuditEvent | None:
    for event in reversed(events):
        if event.action is action:
            return event
    return None


def _event_summary(event: AuditEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "actor_type": event.actor_type.value,
        "actor_id": event.actor_id,
        "action": event.action.value,
        "before_state": event.before_state.value if event.before_state else None,
        "after_state": event.after_state.value,
        "reason": event.reason,
        "record_hash": event.record_hash,
        "venue": event.venue or None,
        "external_order_id_present": bool(event.external_order_id),
    }


def _default_client_order_id(
    decision_id: str,
    venue: TicketVenue,
    record_hash: str,
) -> str:
    prefix = f"gpt-trader-{venue.value}-"
    decision_hash = hashlib.sha256(decision_id.encode("utf-8")).hexdigest()[:12]
    suffix = f"-{decision_hash}-{record_hash[:12]}"
    max_decision_length = MAX_CLIENT_ORDER_ID_LENGTH - len(prefix) - len(suffix)
    decision_fragment = decision_id[:max_decision_length]
    generated = f"{prefix}{decision_fragment}{suffix}"
    normalized = _optional_client_order_id(generated)
    if normalized is None:  # pragma: no cover - generated ids are always present
        raise AssertionError("generated client_order_id unexpectedly empty")
    return normalized


def _order_side_for_direction(direction: TradeDirection) -> str:
    if direction is TradeDirection.LONG:
        return "buy"
    if direction is TradeDirection.SHORT:
        return "sell"
    raise ValidationError(
        "Ticket export requires an orderable direction before emitting a venue payload; "
        f"expected long or short, got '{direction.value}'",
        field="direction",
        value=direction.value,
    )


def _non_empty_text(value: str, field: str) -> str:
    normalized = value.strip()
    if normalized:
        return normalized
    raise ValidationError(f"{field} must be non-empty", field=field, value=value)


def _optional_client_order_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValidationError("client_order_id must be non-empty", field="client_order_id")
    if _CLIENT_ORDER_ID.fullmatch(normalized):
        return normalized
    raise ValidationError(
        "client_order_id must start with an alphanumeric character and contain only "
        "letters, numbers, '.', '_', ':', or '-'",
        field="client_order_id",
        value=normalized,
    )
