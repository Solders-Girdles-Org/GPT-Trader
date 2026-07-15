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
from datetime import UTC, datetime
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

    ``corrupt_keys`` names evidence keys that were present on the FILLED event
    but failed to decode: destroyed evidence must stay distinguishable from
    evidence that never existed, so downstream consumers can disclose the
    degradation instead of silently reporting an estimate as by-design.
    """

    filled_at: datetime | None
    price: Decimal | None
    quantity: Decimal | None
    venue: str
    external_order_id: str
    source: str
    corrupt_keys: tuple[str, ...] = ()


def encode_fill_evidence(
    *,
    price: Decimal | None,
    quantity: Decimal | None,
    filled_at: datetime | None,
) -> tuple[str, ...]:
    """Encode known fill facts as audit evidence strings; unknown facts are omitted.

    A naive ``filled_at`` is coerced to UTC: paper/mock timestamps are UTC by
    construction, and a naive value written into evidence would later raise on
    comparison against tz-aware candle timestamps.
    """
    evidence: list[str] = []
    if price is not None:
        evidence.append(f"{FILL_PRICE_EVIDENCE_KEY}={price}")
    if quantity is not None:
        evidence.append(f"{FILL_QUANTITY_EVIDENCE_KEY}={quantity}")
    if filled_at is not None:
        if filled_at.tzinfo is None:
            filled_at = filled_at.replace(tzinfo=UTC)
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

    corrupt: list[str] = []
    price = _decimal_evidence(filled_event, FILL_PRICE_EVIDENCE_KEY, corrupt)
    quantity = _decimal_evidence(filled_event, FILL_QUANTITY_EVIDENCE_KEY, corrupt)
    filled_at = _datetime_evidence(filled_event, FILL_TIME_EVIDENCE_KEY, corrupt)
    has_evidence = (
        price is not None or quantity is not None or filled_at is not None or bool(corrupt)
    )
    return RecordedFill(
        filled_at=filled_at if filled_at is not None else filled_event.timestamp,
        price=price,
        quantity=quantity,
        venue=filled_event.venue,
        external_order_id=filled_event.external_order_id,
        source=FILL_SOURCE_AUDIT_EVIDENCE if has_evidence else FILL_SOURCE_AUDIT_EVENT,
        corrupt_keys=tuple(corrupt),
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


def _decimal_evidence(event: AuditEvent, key: str, corrupt: list[str]) -> Decimal | None:
    raw = _evidence_value(event, key)
    if raw is None:
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        corrupt.append(key)
        return None
    if not parsed.is_finite():
        corrupt.append(key)
        return None
    return parsed


def _datetime_evidence(event: AuditEvent, key: str, corrupt: list[str]) -> datetime | None:
    raw = _evidence_value(event, key)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        corrupt.append(key)
        return None
    if parsed.tzinfo is None:
        # A naive anchor would raise on comparison against tz-aware candles;
        # the encoder always writes UTC offsets, so naive == corrupt.
        corrupt.append(key)
        return None
    return parsed
