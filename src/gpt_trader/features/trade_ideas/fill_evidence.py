"""Immutable actual-fill evidence carried on FILLED audit events (#1212).

Paper reconciliation confirms a venue fill but historically recorded only the
external order id, so exit monitoring had to reconstruct entry from the
proposal's planned entry zone — and a confirmed fill outside that zone could
never resolve. This module defines the provider-neutral codec for the fill
facts: structured ``key=value`` evidence strings appended to the FILLED audit
event (the same convention closeout evidence uses), decoded back into a
``RecordedFill`` for exit evaluation.

Existing audit records are untouched: a pre-evidence FILLED event decodes into
a ``RecordedFill`` anchored at the audit event timestamp with unknown price and
quantity (``source="audit_event"``), so legacy fills still resolve — via
durable fallback evidence or the documented zone-midpoint estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from gpt_trader.features.trade_ideas.audit import AuditAction, AuditEvent
from gpt_trader.features.trade_ideas.service_models import TradeIdeaView

FILL_PRICE_EVIDENCE_KEY = "fill_price"
FILL_QUANTITY_EVIDENCE_KEY = "fill_quantity"
FILL_TIME_EVIDENCE_KEY = "fill_time"

#: ``RecordedFill.source`` values, in decreasing evidentiary strength.
FILL_SOURCE_AUDIT_EVIDENCE = "audit_evidence"
FILL_SOURCE_AUDIT_EVENT = "audit_event"


@dataclass(frozen=True, slots=True)
class RecordedFill:
    """Provider-neutral facts of one venue-confirmed fill.

    ``filled_at`` is ``None`` only for fallback facts (e.g. cycle-manifest
    execution rows) that know the price/quantity but not the exact fill time;
    a fill decoded from the audit trail always carries an anchor timestamp.
    """

    filled_at: datetime | None
    price: Decimal | None
    quantity: Decimal | None
    venue: str
    external_order_id: str
    source: str


def encode_fill_evidence(
    *,
    price: Decimal | None,
    quantity: Decimal | None,
    filled_at: datetime | None,
) -> tuple[str, ...]:
    """Encode known fill facts as audit evidence strings; unknown facts are omitted."""
    evidence: list[str] = []
    if price is not None:
        evidence.append(f"{FILL_PRICE_EVIDENCE_KEY}={price}")
    if quantity is not None:
        evidence.append(f"{FILL_QUANTITY_EVIDENCE_KEY}={quantity}")
    if filled_at is not None:
        evidence.append(f"{FILL_TIME_EVIDENCE_KEY}={filled_at.isoformat()}")
    return tuple(evidence)


def recorded_fill_from_view(view: TradeIdeaView) -> RecordedFill | None:
    """Decode the actual-fill facts from a view's FILLED audit event.

    Returns ``None`` when the idea never filled. A FILLED event without fill
    evidence (recorded before evidence persistence existed) yields a fill
    anchored at the audit event timestamp with unknown price and quantity.
    """
    filled_event = _filled_event(view)
    if filled_event is None:
        return None

    price = _decimal_evidence(filled_event, FILL_PRICE_EVIDENCE_KEY)
    quantity = _decimal_evidence(filled_event, FILL_QUANTITY_EVIDENCE_KEY)
    filled_at = _datetime_evidence(filled_event, FILL_TIME_EVIDENCE_KEY)
    has_evidence = price is not None or quantity is not None or filled_at is not None
    return RecordedFill(
        filled_at=filled_at if filled_at is not None else filled_event.timestamp,
        price=price,
        quantity=quantity,
        venue=filled_event.venue,
        external_order_id=filled_event.external_order_id,
        source=FILL_SOURCE_AUDIT_EVIDENCE if has_evidence else FILL_SOURCE_AUDIT_EVENT,
    )


def _filled_event(view: TradeIdeaView) -> AuditEvent | None:
    for event in reversed(view.events):
        if event.action is AuditAction.FILLED:
            return event
    return None


def _evidence_value(event: AuditEvent, key: str) -> str | None:
    prefix = f"{key}="
    for item in event.evidence:
        if item.startswith(prefix):
            return item[len(prefix) :]
    return None


def _decimal_evidence(event: AuditEvent, key: str) -> Decimal | None:
    raw = _evidence_value(event, key)
    if raw is None:
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _datetime_evidence(event: AuditEvent, key: str) -> datetime | None:
    raw = _evidence_value(event, key)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
