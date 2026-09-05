"""Position-targeted paper operations projected from the existing execution journal.

This module owns no storage. Confirmed receipts are facts; pending intents reserve
quantity without releasing exposure. Historical entry hashes remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from gpt_trader.core.order_intent import OrderIntent
from gpt_trader.core.trading import OrderSide, OrderType
from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import AuditAction
from gpt_trader.features.trade_ideas.closeout import (
    CloseoutAttribution,
    CloseoutResolution,
    MaxLossSnapshot,
)
from gpt_trader.features.trade_ideas.execution_journal import (
    ExecutionJournalEntry,
    ExecutionJournalIntegrityError,
)
from gpt_trader.features.trade_ideas.fill_evidence import recorded_fill_from_view
from gpt_trader.features.trade_ideas.models import TradeDirection, TradeIdea
from gpt_trader.features.trade_ideas.service_models import UnknownTradeIdeaError
from gpt_trader.features.trade_ideas.workflow import TradeIdeaState

if TYPE_CHECKING:
    from gpt_trader.features.trade_ideas.service import TradeIdeaService, TradeIdeaView


class PositionOperationAdmissionError(ValidationError):
    """A proposed reduction cannot be admitted against current target state."""


@dataclass(frozen=True)
class ReductionFact:
    operation_id: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    source: str
    resolution: CloseoutResolution = CloseoutResolution.THESIS_TARGET


@dataclass(frozen=True)
class PaperPosition:
    idea: TradeIdea
    entry_quantity: Decimal
    entry_price: Decimal
    remaining: Decimal
    reserved: Decimal
    reductions: tuple[ReductionFact, ...]

    @property
    def available(self) -> Decimal:
        return self.remaining - self.reserved

    def realized_amount(self, fact: ReductionFact) -> Decimal:
        move = fact.price - self.entry_price
        return fact.quantity * (move if self.idea.direction is TradeDirection.LONG else -move)

    @property
    def realized_total(self) -> Decimal:
        return sum((self.realized_amount(fact) for fact in self.reductions), Decimal(0))


@dataclass(frozen=True)
class _ExecutionSnapshot:
    entries: dict[str, ExecutionJournalEntry]
    resolutions: tuple[dict[str, Any], ...]
    views: dict[str, TradeIdeaView]


def _execution_snapshot(
    service: TradeIdeaService, requested_target: str | None = None
) -> _ExecutionSnapshot:
    journal = service.execution_journal
    entries = journal.entries()
    resolutions = journal.simulated_resolutions()
    targets = {
        intent.position_operation.target_decision_id
        for entry in entries.values()
        if (intent := OrderIntent.from_dict(entry.intent)).position_operation is not None
    }
    targets.update(event["target_decision_id"] for event in resolutions)
    if requested_target is not None:
        targets.add(requested_target)
    events_by_id: dict[str, list[Any]] = {}
    for event in service.audit_log.read_events():
        events_by_id.setdefault(event.decision_id, []).append(event)
    views: dict[str, TradeIdeaView] = {}
    for decision_id, events in events_by_id.items():
        idea = service.load_record_version(decision_id, events[-1].record_hash)
        admitted = idea.position_operation is not None and any(
            event.action in {AuditAction.SUBMITTED, AuditAction.FILLED} for event in events
        )
        targeted_intent = (
            decision_id in entries and "position_operation" in entries[decision_id].intent
        )
        if admitted or targeted_intent or decision_id in targets:
            views[decision_id] = service.get(decision_id)

    for view in views.values():
        if view.idea.position_operation is None:
            continue
        submissions = [event for event in view.events if event.action is AuditAction.SUBMITTED]
        entry = entries.get(view.idea.decision_id)
        if not submissions and entry is None:
            continue  # Denied/unadmitted proposals remain valid historical evidence.
        if not submissions or entry is None:
            raise ExecutionJournalIntegrityError(
                "Admitted reduction is missing its audit or journal intent"
            )
        journal.validate_binding(entry, view.idea)
        receipt = entry.receipt
        if (
            receipt is not None
            and datetime.fromisoformat(receipt["filled_at"]) < submissions[-1].timestamp
        ):
            raise ExecutionJournalIntegrityError("Reduction receipt precedes recorded submission")
        if view.state is TradeIdeaState.FILLED:
            fill = recorded_fill_from_view(view)
            if (
                receipt is None
                or receipt["status"] != "filled"
                or fill is None
                or fill.quantity != Decimal(receipt["quantity"])
                or fill.price != Decimal(receipt["price"])
                or fill.external_order_id != receipt["order_id"]
                or fill.filled_at != datetime.fromisoformat(receipt["filled_at"])
            ):
                raise ExecutionJournalIntegrityError(
                    "Audited reduction fill conflicts with durable receipt"
                )
        elif entry.reconciled:
            raise ExecutionJournalIntegrityError("Reconciled reduction has no audited fill")
    for entry in entries.values():
        if "position_operation" in entry.intent and entry.decision_id not in views:
            raise ExecutionJournalIntegrityError("Reduction intent has no audited operation")
    return _ExecutionSnapshot(entries, resolutions, views)


def position(service: TradeIdeaService, target_id: str) -> PaperPosition:
    """Read one entry and its reductions from a single validated state snapshot."""
    with service.execution_journal.repository.transaction():
        return _position_from_snapshot(service, target_id, _execution_snapshot(service, target_id))


def positions(service: TradeIdeaService) -> dict[str, PaperPosition]:
    """All targeted positions share one journal/view snapshot; no persisted cache."""
    with service.execution_journal.repository.transaction():
        snapshot = _execution_snapshot(service)
        targets = {
            intent.position_operation.target_decision_id
            for entry in snapshot.entries.values()
            if (intent := OrderIntent.from_dict(entry.intent)).position_operation is not None
        }
        targets.update(event["target_decision_id"] for event in snapshot.resolutions)
        return {
            target: _position_from_snapshot(service, target, snapshot) for target in sorted(targets)
        }


def _position_from_snapshot(
    service: TradeIdeaService, target_id: str, snapshot: _ExecutionSnapshot
) -> PaperPosition:
    """Project one entry using already validated journal and audit data."""
    with service.execution_journal.repository.transaction():
        if target_id not in snapshot.views:
            raise ExecutionJournalIntegrityError("Admitted reduction target is missing")
        view = snapshot.views[target_id]
        idea = view.idea
        fill = recorded_fill_from_view(view)
        if (
            view.state is not TradeIdeaState.FILLED
            or idea.position_operation is not None
            or idea.direction not in {TradeDirection.LONG, TradeDirection.SHORT}
            or fill is None
            or fill.quantity is None
            or fill.price is None
            or fill.filled_at is None
            or fill.corrupt_keys
            or not fill.quantity.is_finite()
            or fill.quantity <= 0
            or not fill.price.is_finite()
            or fill.price <= 0
        ):
            raise ExecutionJournalIntegrityError(
                "Target requires a confirmed entry quantity and basis"
            )
        reductions: list[ReductionFact] = []
        reserved = Decimal(0)
        for entry in snapshot.entries.values():
            intent = OrderIntent.from_dict(entry.intent)
            operation = intent.position_operation
            if operation is None or operation.target_decision_id != target_id:
                continue
            if operation.target_record_hash != idea.record_hash():
                raise ExecutionJournalIntegrityError("Reduction target hash conflicts with entry")
            operation_view = snapshot.views[entry.decision_id]
            service.execution_journal.validate_binding(entry, operation_view.idea)
            validate_target_identity(idea, operation_view.idea)
            receipt = entry.receipt
            if receipt is not None and receipt["status"] == "filled":
                reductions.append(
                    ReductionFact(
                        entry.decision_id,
                        Decimal(receipt["quantity"]),
                        Decimal(receipt["price"]),
                        datetime.fromisoformat(receipt["filled_at"]),
                        "paper_broker_receipt",
                        CloseoutResolution(operation.resolution),
                    )
                )
            elif (
                receipt is not None
                and receipt["status"] in {"rejected", "cancelled", "expired"}
                and Decimal(receipt["quantity"]) == 0
            ):
                continue  # Definitive zero-fill evidence releases this reservation.
            else:
                reserved += intent.quantity
        for event in snapshot.resolutions:
            if event["target_decision_id"] != target_id:
                continue
            if event["target_record_hash"] != idea.record_hash():
                raise ExecutionJournalIntegrityError("Simulated resolution target hash conflicts")
            reductions.append(
                ReductionFact(
                    event["operation_id"],
                    Decimal(event["quantity"]),
                    Decimal(event["price"]),
                    datetime.fromisoformat(event["resolved_at"]),
                    "simulated_candle_resolution",
                    CloseoutResolution(event["resolution"]),
                )
            )
        if any(fact.timestamp < fill.filled_at for fact in reductions):
            raise ExecutionJournalIntegrityError("Reduction precedes its target entry fill")
        remaining = fill.quantity - sum((fact.quantity for fact in reductions), Decimal(0))
        if remaining < 0 or reserved < 0 or reserved > remaining:
            raise ExecutionJournalIntegrityError("Paper reductions exceed the target quantity")
        projected = PaperPosition(
            idea, fill.quantity, fill.price, remaining, reserved, tuple(reductions)
        )
        if view.closeout_attribution is not None:
            if reductions or reserved:
                if (
                    remaining != 0
                    or reserved != 0
                    or view.closeout_attribution.realized_profit_loss_amount
                    != projected.realized_total
                    or view.closeout_attribution.timestamp
                    != max(fact.timestamp for fact in reductions)
                ):
                    raise ExecutionJournalIntegrityError(
                        "Final attribution conflicts with position facts"
                    )
            else:
                # Historical full closeouts are retained outcomes, not reconstructed exit fills.
                projected = replace(projected, remaining=Decimal(0))
        return projected


def validate_target_identity(target: TradeIdea, operation_idea: TradeIdea) -> None:
    operation = operation_idea.position_operation
    if (
        operation is None
        or operation.target_record_hash != target.record_hash()
        or operation.target_decision_id != target.decision_id
        or operation_idea.instrument != target.instrument
        or operation_idea.product_type != target.product_type
        or operation_idea.direction != target.direction
    ):
        raise ExecutionJournalIntegrityError("Position operation conflicts with immutable target")


def intent_for_idea(service: TradeIdeaService, idea: TradeIdea) -> OrderIntent:
    side = OrderSide.BUY if idea.direction is TradeDirection.LONG else OrderSide.SELL
    quantity = idea.sizing_recommendation.quantity
    operation = idea.position_operation
    if operation is not None:
        try:
            target = service.get(operation.target_decision_id)
        except UnknownTradeIdeaError as error:
            raise PositionOperationAdmissionError("Reduction target is unknown") from error
        try:
            validate_target_identity(target.idea, idea)
        except ExecutionJournalIntegrityError as error:
            raise PositionOperationAdmissionError(str(error)) from error
        fill = recorded_fill_from_view(target)
        if (
            target.state is not TradeIdeaState.FILLED
            or target.idea.position_operation is not None
            or fill is None
            or fill.price is None
            or fill.quantity is None
            or fill.filled_at is None
            or fill.corrupt_keys
        ):
            raise PositionOperationAdmissionError(
                "Target requires a confirmed entry quantity and basis"
            )
        if target.closeout_attribution is not None:
            raise PositionOperationAdmissionError("Position already has a final closeout")
        projected = position(service, operation.target_decision_id)
        if operation.action == "close":
            if quantity is not None and quantity != projected.available:
                raise PositionOperationAdmissionError(
                    "Close quantity must equal unreserved remainder"
                )
            quantity = projected.available
        if quantity is None or quantity <= 0 or quantity > projected.available:
            raise PositionOperationAdmissionError("Reduction exceeds unreserved target quantity")
        side = OrderSide.SELL if idea.direction is TradeDirection.LONG else OrderSide.BUY
    if idea.direction not in {TradeDirection.LONG, TradeDirection.SHORT} or quantity is None:
        raise PositionOperationAdmissionError("Paper intent requires direction and quantity")
    return OrderIntent(
        idea.decision_id,
        idea.instrument,
        side,
        OrderType.MARKET,
        quantity,
        reduce_only=operation is not None,
        position_operation=operation,
    )


def scaled_idea(idea: TradeIdea, ratio: Decimal) -> TradeIdea:
    """Derived exposure only; never serialize this as a revised original idea."""

    def scaled(value: Decimal | None) -> Decimal | None:
        return value * ratio if value is not None else None

    return replace(
        idea,
        max_loss=replace(
            idea.max_loss,
            amount=scaled(idea.max_loss.amount),
            percent_of_account=scaled(idea.max_loss.percent_of_account),
        ),
        sizing_recommendation=replace(
            idea.sizing_recommendation,
            quantity=scaled(idea.sizing_recommendation.quantity),
            notional=scaled(idea.sizing_recommendation.notional),
        ),
    )


def reduction_records(
    service: TradeIdeaService,
) -> tuple[tuple[TradeIdea, CloseoutAttribution], ...]:
    """Replaceable per-fill accounting view; these are not persisted closeouts."""
    records: list[tuple[TradeIdea, CloseoutAttribution]] = []
    for projected in positions(service).values():
        idea = projected.idea
        for fact in projected.reductions:
            amount = projected.realized_amount(fact)
            # Original loss snapshot retains the approval-time equity denominator.
            record = CloseoutAttribution(
                idea.decision_id,
                fact.timestamp,
                "system",
                fact.source,
                fact.operation_id,
                idea.record_hash(),
                fact.resolution,
                MaxLossSnapshot(
                    idea.max_loss.amount,
                    idea.max_loss.percent_of_account,
                    idea.max_loss.assumptions,
                ),
                realized_profit_loss_amount=amount,
                evidence=(
                    f"position_reduction:{fact.operation_id}",
                    f"quantity={fact.quantity}",
                    f"entry_price={projected.entry_price}",
                    f"exit_price={fact.price}",
                    f"source={fact.source}",
                ),
            )
            portion = scaled_idea(idea, fact.quantity / projected.entry_quantity)
            portion = replace(
                portion,
                sizing_recommendation=replace(
                    portion.sizing_recommendation,
                    quantity=fact.quantity,
                    notional=fact.quantity * projected.entry_price,
                ),
            )
            records.append((portion, record))
    return tuple(records)


def finalize_position(
    service: TradeIdeaService,
    target_id: str,
    *,
    actor_id: str,
    resolution: CloseoutResolution = CloseoutResolution.THESIS_TARGET,
    evidence: tuple[str, ...] = (),
) -> CloseoutAttribution | None:
    """Publish exactly one aggregate only after all confirmed quantity is closed."""
    projected = position(service, target_id)
    if projected.remaining != 0 or projected.reserved != 0:
        return None
    existing = service.get(target_id).closeout_attribution
    if existing is not None:
        if existing.realized_profit_loss_amount != projected.realized_total:
            raise ExecutionJournalIntegrityError("Final closeout conflicts with reduction facts")
        return existing
    from gpt_trader.features.trade_ideas.audit import ActorType

    return service.record_closeout_attribution(
        target_id,
        actor_id=actor_id,
        actor_type=ActorType.SYSTEM,
        resolution=resolution,
        realized_profit_loss_amount=projected.realized_total,
        evidence=evidence
        + tuple(f"position_reduction:{fact.operation_id}" for fact in projected.reductions),
        resolved_at=max(fact.timestamp for fact in projected.reductions),
    )


def record_simulated_close(
    service: TradeIdeaService,
    target_id: str,
    *,
    quantity: Decimal,
    price: Decimal,
    resolved_at: datetime,
    observed_at: datetime,
    resolution: CloseoutResolution,
    evidence: tuple[str, ...],
    actor_id: str,
) -> CloseoutAttribution:
    """Commit a candle-scored resolution, explicitly without a broker receipt."""
    journal = service.execution_journal
    with journal.repository.transaction(write=True):
        existing = service.get(target_id).closeout_attribution
        if existing is not None:
            prior = next(
                (
                    event
                    for event in journal.simulated_resolutions()
                    if event["target_decision_id"] == target_id
                ),
                None,
            )
            if (
                prior is None
                or Decimal(prior["quantity"]) != quantity
                or Decimal(prior["price"]) != price
                or datetime.fromisoformat(prior["resolved_at"]) != resolved_at
                or prior["resolution"] != resolution.value
            ):
                raise ExecutionJournalIntegrityError(
                    "Conflicting replay of an already closed target"
                )
            return existing
        projected = position(service, target_id)
        if projected.reserved or quantity != projected.remaining or quantity <= 0:
            raise ExecutionJournalIntegrityError(
                "Simulated close conflicts with remaining/reserved quantity"
            )
        if (
            resolved_at.utcoffset() is None
            or observed_at.utcoffset() is None
            or resolved_at > observed_at
        ):
            raise ExecutionJournalIntegrityError(
                "Simulated close requires an observed resolution time"
            )
        if any(fact.timestamp > resolved_at for fact in projected.reductions):
            raise ExecutionJournalIntegrityError(
                "Simulated close precedes a confirmed partial reduction"
            )
        journal.record_simulated_resolution(
            {
                "operation_id": f"simulated-close-{projected.idea.record_hash()}",
                "target_decision_id": target_id,
                "target_record_hash": projected.idea.record_hash(),
                "quantity": str(quantity),
                "price": str(price),
                "resolved_at": resolved_at.isoformat(),
                "observed_at": observed_at.isoformat(),
                "resolution": resolution.value,
                "source": "simulated_candle_resolution",
                "actor_id": actor_id,
                "evidence": list(evidence),
            }
        )
        result = finalize_position(
            service, target_id, actor_id=actor_id, resolution=resolution, evidence=evidence
        )
        if result is None:
            raise ExecutionJournalIntegrityError("Simulated final close did not flatten its target")
        return result
