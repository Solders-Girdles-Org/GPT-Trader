"""Reconcile paper/mock fills into the trade-idea audit trail.

The reconciler is intentionally read-model driven: it consumes already-persisted
paper/mock trade events and records lifecycle facts only through
``TradeIdeaService``. It never reads a broker or submits/cancels orders.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import AbstractSet, Any

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import ActorType, AuditAction
from gpt_trader.features.trade_ideas.fill_evidence import encode_fill_evidence
from gpt_trader.features.trade_ideas.models import TradeDirection, is_safe_decision_id
from gpt_trader.features.trade_ideas.service import TradeIdeaService, TradeIdeaView
from gpt_trader.features.trade_ideas.workflow import TradeIdeaState

PAPER_RECONCILIATION_PROFILES = frozenset({"dev", "mock", "paper"})
_FILLED_STATUSES = frozenset({"filled"})


class PaperFillProfileError(ValidationError):
    """Raised when paper-fill reconciliation is attempted for a live profile."""


@dataclass(frozen=True, slots=True)
class PaperFillEvent:
    """Normalized paper/mock fill event read from existing runtime persistence."""

    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal | None
    price: Decimal | None
    status: str
    decision_id: str | None = None
    source_index: int | None = None
    filled_at: datetime | None = None

    @property
    def external_order_id(self) -> str:
        return self.order_id or self.client_order_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "price": str(self.price) if self.price is not None else None,
            "status": self.status,
            "decision_id": self.decision_id,
            "source_index": self.source_index,
            "filled_at": self.filled_at.isoformat() if self.filled_at is not None else None,
        }


@dataclass(frozen=True, slots=True)
class PaperFillReconciliationEntry:
    """One reconciler decision for a fill event."""

    event: PaperFillEvent
    status: str
    reason: str
    decision_id: str | None = None
    match_method: str | None = None
    recorded_submission: bool = False
    recorded_fill: bool = False
    final_state: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "decision_id": self.decision_id,
            "match_method": self.match_method,
            "recorded_submission": self.recorded_submission,
            "recorded_fill": self.recorded_fill,
            "final_state": self.final_state,
            "context": self.context,
            "event": self.event.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PaperFillReconciliationReport:
    """Summary of a paper-fill reconciliation pass."""

    mode: str
    matched: tuple[PaperFillReconciliationEntry, ...]
    unmatched: tuple[PaperFillReconciliationEntry, ...]
    skipped: tuple[PaperFillReconciliationEntry, ...]

    @property
    def matched_count(self) -> int:
        return len(self.matched)

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def recorded_count(self) -> int:
        return sum(1 for entry in self.matched if entry.recorded_fill)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "matched_count": self.matched_count,
            "unmatched_count": self.unmatched_count,
            "skipped_count": self.skipped_count,
            "recorded_count": self.recorded_count,
            "matched": [entry.to_dict() for entry in self.matched],
            "unmatched": [entry.to_dict() for entry in self.unmatched],
            "skipped": [entry.to_dict() for entry in self.skipped],
        }


def validate_paper_reconciliation_profile(profile: str) -> str:
    """Return a normalized allowed paper/dev/mock profile or raise."""
    normalized = str(profile).strip().lower()
    if normalized in PAPER_RECONCILIATION_PROFILES:
        return normalized
    raise PaperFillProfileError(
        "Paper fill reconciliation is available only for paper/dev/mock profiles",
        field="profile",
        value=profile,
    )


def paper_fill_events_from_store_events(
    store_events: Iterable[Mapping[str, Any]],
) -> tuple[PaperFillEvent, ...]:
    """Extract filled trade events from EventStore rows."""
    fills: list[PaperFillEvent] = []
    for index, event in enumerate(store_events):
        if event.get("type") != "trade":
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue
        status = _text(data.get("status")).lower()
        if status not in _FILLED_STATUSES:
            continue
        fills.append(
            PaperFillEvent(
                order_id=_text(data.get("order_id") or data.get("id")),
                client_order_id=_text(
                    data.get("client_order_id")
                    or data.get("client_id")
                    or data.get("clientOrderId")
                ),
                symbol=_text(data.get("symbol") or data.get("product_id")),
                side=_text(data.get("side")).lower(),
                quantity=_optional_decimal(data.get("quantity") or data.get("filled_quantity")),
                price=_optional_decimal(
                    data.get("price") or data.get("avg_fill_price") or data.get("fill_price")
                ),
                status=status,
                decision_id=_safe_text_or_none(data.get("decision_id")),
                source_index=index,
                filled_at=_optional_datetime(
                    data.get("filled_at") or data.get("timestamp") or data.get("created_at")
                ),
            )
        )
    return tuple(fills)


class PaperFillReconciler:
    """Match paper/mock fills to approved trade ideas and record audit events."""

    def __init__(
        self,
        service: TradeIdeaService,
        *,
        actor_id: str = "paper-fill-reconciler",
        venue: str = "manual",
    ) -> None:
        self._service = service
        self._actor_id = actor_id
        self._venue = venue

    def reconcile_store_events(
        self,
        store_events: Iterable[Mapping[str, Any]],
        *,
        apply: bool = False,
    ) -> PaperFillReconciliationReport:
        """Reconcile filled trade events from EventStore-style rows."""
        return self.reconcile_fills(
            paper_fill_events_from_store_events(store_events),
            apply=apply,
        )

    def reconcile_fills(
        self,
        fills: Iterable[PaperFillEvent],
        *,
        apply: bool = False,
    ) -> PaperFillReconciliationReport:
        """Reconcile normalized paper fill events."""
        views = tuple(self._service.list_views())
        view_by_decision_id = {view.idea.decision_id: view for view in views}
        preview_filled_decision_ids: set[str] = set()
        audited_fill_order_ids = _audited_fill_order_ids(views)

        matched: list[PaperFillReconciliationEntry] = []
        unmatched: list[PaperFillReconciliationEntry] = []
        skipped: list[PaperFillReconciliationEntry] = []

        for event in fills:
            recordable_views = _recordable_views(
                view_by_decision_id.values(),
                preview_filled_decision_ids=preview_filled_decision_ids,
            )
            decision_id, method, reason = self._match_event(
                event,
                view_by_decision_id=view_by_decision_id,
                recordable_views=recordable_views,
            )
            if decision_id is None:
                unmatched.append(
                    PaperFillReconciliationEntry(
                        event=event,
                        status="unmatched",
                        reason=reason,
                    )
                )
                continue

            view = view_by_decision_id[decision_id]
            if method in {"decision_id", "client_order_id"}:
                payload_conflict = _matched_payload_conflict(view, event)
                if payload_conflict is not None:
                    conflict_reason, conflict_context = payload_conflict
                    unmatched.append(
                        PaperFillReconciliationEntry(
                            event=event,
                            status="unmatched",
                            reason=conflict_reason,
                            decision_id=decision_id,
                            match_method=method,
                            final_state=view.state.value,
                            context=conflict_context,
                        )
                    )
                    continue

            if decision_id in preview_filled_decision_ids:
                skipped.append(
                    PaperFillReconciliationEntry(
                        event=event,
                        status="skipped",
                        reason="trade idea is already terminal filled",
                        decision_id=decision_id,
                        match_method=method,
                        final_state=TradeIdeaState.FILLED.value,
                    )
                )
                continue

            if self._already_reconciled(
                view, event, audited_fill_order_ids, is_legacy=method == "symbol_side"
            ):
                skipped.append(
                    PaperFillReconciliationEntry(
                        event=event,
                        status="skipped",
                        reason="fill already recorded on trade-idea audit trail",
                        decision_id=decision_id,
                        match_method=method,
                        final_state=view.state.value,
                    )
                )
                continue

            if view.state is TradeIdeaState.FILLED:
                skipped.append(
                    PaperFillReconciliationEntry(
                        event=event,
                        status="skipped",
                        reason="trade idea is already terminal filled",
                        decision_id=decision_id,
                        match_method=method,
                        final_state=view.state.value,
                    )
                )
                continue

            if view.state not in {TradeIdeaState.APPROVED, TradeIdeaState.SUBMITTED}:
                unmatched.append(
                    PaperFillReconciliationEntry(
                        event=event,
                        status="unmatched",
                        reason=f"trade idea state is {view.state.value}, not approved/submitted",
                        decision_id=decision_id,
                        match_method=method,
                        final_state=view.state.value,
                    )
                )
                continue

            recorded_submission = False
            recorded_fill = False
            final_state = view.state.value
            if apply:
                if view.state is TradeIdeaState.APPROVED:
                    view = self._service.record_submission(
                        decision_id,
                        actor_id=self._actor_id,
                        venue=self._venue,
                        external_order_id=event.external_order_id,
                        reason=_submission_reason(event),
                        actor_type=ActorType.SYSTEM,
                    )
                    recorded_submission = True
                view = self._service.record_fill(
                    decision_id,
                    actor_id=self._actor_id,
                    venue=self._venue,
                    external_order_id=event.external_order_id,
                    reason=_fill_reason(event),
                    actor_type=ActorType.VENUE,
                    evidence=encode_fill_evidence(
                        price=event.price,
                        quantity=event.quantity,
                        filled_at=event.filled_at,
                    ),
                )
                recorded_fill = True
                final_state = view.state.value
                view_by_decision_id[decision_id] = view
            else:
                final_state = TradeIdeaState.FILLED.value

            matched.append(
                PaperFillReconciliationEntry(
                    event=event,
                    status="matched",
                    reason=(
                        "recorded paper/mock fill on trade-idea audit trail"
                        if recorded_fill
                        else "would record paper/mock fill on trade-idea audit trail"
                    ),
                    decision_id=decision_id,
                    match_method=method,
                    recorded_submission=recorded_submission,
                    recorded_fill=recorded_fill,
                    final_state=final_state,
                )
            )
            if not apply:
                preview_filled_decision_ids.add(decision_id)
            if event.external_order_id:
                audited_fill_order_ids.add(event.external_order_id)

        return PaperFillReconciliationReport(
            mode="apply" if apply else "dry_run",
            matched=tuple(matched),
            unmatched=tuple(unmatched),
            skipped=tuple(skipped),
        )

    def _match_event(
        self,
        event: PaperFillEvent,
        *,
        view_by_decision_id: Mapping[str, TradeIdeaView],
        recordable_views: tuple[TradeIdeaView, ...],
    ) -> tuple[str | None, str | None, str]:
        explicit_decision_id = _safe_text_or_none(event.decision_id)
        if explicit_decision_id is not None:
            if explicit_decision_id not in view_by_decision_id:
                return None, None, f"explicit decision_id not found: {explicit_decision_id}"
            return explicit_decision_id, "decision_id", "matched explicit decision id"

        client_order_id = _safe_text_or_none(event.client_order_id)
        if client_order_id is not None:
            client_match = _match_client_order_id(
                event.client_order_id,
                view_by_decision_id=view_by_decision_id,
            )
            if client_match is not None:
                return client_match, "client_order_id", "matched client_order_id"
            # A fill that carries a client order id expressed explicit intent. If
            # it does not resolve (stale/moved decision), do not guess via
            # symbol/side -- that could attach it to an unrelated same-symbol/side
            # idea. The symbol/side fallback is only for truly id-less fills.
            return (
                None,
                None,
                f"client_order_id did not match a known trade idea: {client_order_id}",
            )

        symbol_side_matches = [
            view
            for view in recordable_views
            if _symbol_matches(view, event)
            and _side_matches(view, event)
            and _submission_order_id_allows(view, event)
        ]
        if len(symbol_side_matches) == 1:
            decision_id = symbol_side_matches[0].idea.decision_id
            return decision_id, "symbol_side", "matched unique approved symbol/side"
        if len(symbol_side_matches) > 1:
            return None, None, "multiple approved ideas match symbol/side"
        return None, None, "no approved idea matched fill"

    def _already_reconciled(
        self,
        view: TradeIdeaView,
        event: PaperFillEvent,
        audited_fill_order_ids: AbstractSet[str],
        *,
        is_legacy: bool,
    ) -> bool:
        external_order_id = event.external_order_id
        if not external_order_id:
            return view.state is TradeIdeaState.FILLED
        if is_legacy:
            # Legacy id-less fills are matched only by symbol/side, which can
            # resolve to a different idea on a rerun over the full event store.
            # Dedupe against every filled audit event so such a fill is not
            # re-recorded onto a newer same-symbol/side idea.
            return external_order_id in audited_fill_order_ids
        # Explicitly matched fills (decision/client id) belong to the matched
        # idea, so dedupe only against its own audit trail. A reused mock
        # order id (DeterministicBroker resets its counter per run) carrying a
        # new decision/client id must not be skipped as already reconciled.
        return any(
            audit_event.action is AuditAction.FILLED
            and audit_event.external_order_id == external_order_id
            for audit_event in view.events
        )


def _match_client_order_id(
    client_order_id: str,
    *,
    view_by_decision_id: Mapping[str, TradeIdeaView],
) -> str | None:
    if not client_order_id:
        return None
    if is_safe_decision_id(client_order_id) and client_order_id in view_by_decision_id:
        return client_order_id
    matches = [
        decision_id
        for decision_id in view_by_decision_id
        if _client_order_id_contains_decision_id(client_order_id, decision_id)
    ]
    return matches[0] if len(matches) == 1 else None


def _submission_order_id_allows(view: TradeIdeaView, event: PaperFillEvent) -> bool:
    """Gate the symbol/side fallback for already-submitted ideas.

    An approved idea has no submission yet, so symbol/side alone is enough. A
    submitted idea already carries an ``external_order_id``; recording a legacy
    fill with a different id against it would make the idea look submitted as one
    order and filled as another. Only allow the fallback when the fill's id
    matches the submitted order id.
    """
    if view.state is not TradeIdeaState.SUBMITTED:
        return True
    submitted_order_id = _submitted_external_order_id(view)
    if not submitted_order_id:
        return True
    return event.external_order_id == submitted_order_id


def _submitted_external_order_id(view: TradeIdeaView) -> str | None:
    for audit_event in reversed(view.events):
        if audit_event.action is AuditAction.SUBMITTED and audit_event.external_order_id:
            return audit_event.external_order_id
    return None


def _audited_fill_order_ids(views: Iterable[TradeIdeaView]) -> set[str]:
    """Collect external order ids already recorded on any filled audit event."""
    return {
        audit_event.external_order_id
        for view in views
        for audit_event in view.events
        if audit_event.action is AuditAction.FILLED and audit_event.external_order_id
    }


def _recordable_views(
    views: Iterable[TradeIdeaView],
    *,
    preview_filled_decision_ids: AbstractSet[str],
) -> tuple[TradeIdeaView, ...]:
    return tuple(
        view
        for view in views
        if view.state in {TradeIdeaState.APPROVED, TradeIdeaState.SUBMITTED}
        and view.idea.decision_id not in preview_filled_decision_ids
    )


def _client_order_id_contains_decision_id(client_order_id: str, decision_id: str) -> bool:
    if client_order_id == decision_id:
        return True
    separators = ("-", "_", ":")
    return any(
        client_order_id.startswith(f"{decision_id}{separator}")
        or client_order_id.endswith(f"{separator}{decision_id}")
        or f"{separator}{decision_id}{separator}" in client_order_id
        for separator in separators
    )


def _symbol_matches(view: TradeIdeaView, event: PaperFillEvent) -> bool:
    return bool(event.symbol) and view.idea.instrument.upper() == event.symbol.upper()


def _side_matches(view: TradeIdeaView, event: PaperFillEvent) -> bool:
    side = event.side.lower()
    if view.idea.direction is TradeDirection.LONG:
        return side == "buy"
    if view.idea.direction is TradeDirection.SHORT:
        return side == "sell"
    return False


def _matched_payload_conflict(
    view: TradeIdeaView,
    event: PaperFillEvent,
) -> tuple[str, dict[str, Any]] | None:
    if event.symbol and not _symbol_matches(view, event):
        return (
            "fill symbol conflicts with matched trade idea",
            {
                "field": "symbol",
                "event_symbol": event.symbol,
                "idea_symbol": view.idea.instrument,
            },
        )
    if event.side and not _side_matches(view, event):
        return (
            "fill side conflicts with matched trade idea",
            {
                "field": "side",
                "event_side": event.side,
                "idea_direction": view.idea.direction.value,
                "expected_side": _expected_side(view),
            },
        )
    return None


def _expected_side(view: TradeIdeaView) -> str | None:
    if view.idea.direction is TradeDirection.LONG:
        return "buy"
    if view.idea.direction is TradeDirection.SHORT:
        return "sell"
    return None


def _submission_reason(event: PaperFillEvent) -> str:
    return f"Paper/mock order submitted during reconciliation ({event.external_order_id})"


def _fill_reason(event: PaperFillEvent) -> str:
    return f"Paper/mock fill reconciled from order event ({event.external_order_id})"


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or value == "market":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _optional_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _safe_text_or_none(value: Any) -> str | None:
    text = _text(value)
    return text if text else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
