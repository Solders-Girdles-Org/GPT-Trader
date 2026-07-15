"""Immutable actual-fill evidence on the FILLED audit event (#1212).

Paper reconciliation historically dropped the venue-confirmed fill price,
quantity, and timestamp: the FILLED audit event carried only the order id, so
exit monitoring had to reconstruct entry from the proposal's planned entry
zone. These tests pin the provider-neutral evidence codec: structured
``key=value`` evidence strings on the FILLED event, decoded back into a
``RecordedFill`` for exit evaluation, with the audit event timestamp as the
anchor when no explicit fill time was recorded (legacy fills).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    reconciliation_service as _service,
)
from tests.unit.gpt_trader.features.trade_ideas.conftest import (
    submitted_idea as _submitted_idea,
)

from gpt_trader.features.trade_ideas import (
    RecordedFill,
    encode_fill_evidence,
    recorded_fill_from_view,
)

FILL_TIME = datetime(2026, 6, 12, 10, 30, tzinfo=UTC)


def test_encode_fill_evidence_emits_structured_strings() -> None:
    evidence = encode_fill_evidence(
        price=Decimal("60750"),
        quantity=Decimal("0.1"),
        filled_at=FILL_TIME,
    )

    assert evidence == (
        "fill_price=60750",
        "fill_quantity=0.1",
        "fill_time=2026-06-12T10:30:00+00:00",
    )


def test_encode_fill_evidence_omits_unknown_facts() -> None:
    assert encode_fill_evidence(price=None, quantity=None, filled_at=None) == ()
    assert encode_fill_evidence(price=Decimal("101.5"), quantity=None, filled_at=None) == (
        "fill_price=101.5",
    )


def test_recorded_fill_round_trips_through_audit_evidence(tmp_path: Path) -> None:
    service = _service(tmp_path / "ideas")
    decision_id = _submitted_idea(service, external_order_id="MOCK_000009")
    service.record_fill(
        decision_id,
        actor_id="paper-fill-reconciler",
        venue="paper",
        external_order_id="MOCK_000009",
        evidence=encode_fill_evidence(
            price=Decimal("60750"),
            quantity=Decimal("0.1"),
            filled_at=FILL_TIME,
        ),
    )

    recorded = recorded_fill_from_view(service.get(decision_id))

    assert recorded == RecordedFill(
        filled_at=FILL_TIME,
        price=Decimal("60750"),
        quantity=Decimal("0.1"),
        venue="paper",
        external_order_id="MOCK_000009",
        source="audit_evidence",
    )


def test_recorded_fill_falls_back_to_audit_event_timestamp(tmp_path: Path) -> None:
    """A pre-evidence FILLED event still anchors exit evaluation at its timestamp."""
    service = _service(tmp_path / "ideas")
    decision_id = _submitted_idea(service, external_order_id="MOCK_000001")
    service.record_fill(
        decision_id,
        actor_id="paper-fill-reconciler",
        venue="paper",
        external_order_id="MOCK_000001",
    )
    view = service.get(decision_id)

    recorded = recorded_fill_from_view(view)

    assert recorded == RecordedFill(
        filled_at=view.events[-1].timestamp,
        price=None,
        quantity=None,
        venue="paper",
        external_order_id="MOCK_000001",
        source="audit_event",
    )


def test_recorded_fill_is_none_for_unfilled_ideas(tmp_path: Path) -> None:
    service = _service(tmp_path / "ideas")
    decision_id = _submitted_idea(service)

    assert recorded_fill_from_view(service.get(decision_id)) is None


def test_encode_coerces_naive_fill_time_to_utc() -> None:
    """A naive timestamp must never reach tz-aware candle comparisons."""
    evidence = encode_fill_evidence(
        price=None,
        quantity=None,
        filled_at=datetime(2026, 6, 12, 10, 30),  # naive
    )

    assert evidence == ("fill_time=2026-06-12T10:30:00+00:00",)


def test_corrupt_evidence_values_are_reported_not_silently_absent(tmp_path: Path) -> None:
    """Destroyed evidence is distinguishable from evidence that never existed."""
    service = _service(tmp_path / "ideas")
    decision_id = _submitted_idea(service, external_order_id="MOCK_000009")
    service.record_fill(
        decision_id,
        actor_id="paper-fill-reconciler",
        venue="paper",
        external_order_id="MOCK_000009",
        evidence=(
            "fill_price=12.3.4",  # unparseable
            "fill_quantity=NaN",  # non-finite
            "fill_time=2026-06-12T10:30:00",  # naive: rejected as corrupt
        ),
    )
    view = service.get(decision_id)

    recorded = recorded_fill_from_view(view)

    assert recorded is not None
    assert recorded.price is None
    assert recorded.quantity is None
    assert recorded.filled_at == view.events[-1].timestamp  # anchor falls back
    assert recorded.source == "audit_evidence"
    assert recorded.corrupt_keys == ("fill_price", "fill_quantity", "fill_time")
