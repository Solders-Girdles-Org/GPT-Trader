"""Paper idea executor: the machine lane from APPROVED idea to paper fill.

This module carries the lane's structural guarantees (issue #1144, first PR)
— the constructor contract that makes live brokers unreachable and the
refusal logic that admits only APPROVED, unexpired ideas — plus the execution
logic built on them: ``execute`` places one simulated market order per idea
(``client_order_id`` = decision id) and records the SUBMITTED/FILLED
lifecycle only through ``TradeIdeaService``, so every machine action lands on
the append-only audit log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from gpt_trader.core import OrderStatus
from gpt_trader.errors import ValidationError
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.brokerages.paper import HybridPaperBroker
from gpt_trader.features.trade_ideas import (
    ActorType,
    PaperFillEvent,
    PaperFillReconciler,
    PaperFillReconciliationEntry,
    TicketVenue,
    TradeDirection,
    TradeIdea,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaView,
)

# The exhaustive set of broker types this lane may drive. Membership is
# checked by exact type, not isinstance: a subclass could override fill
# behavior into a live call path, and a duck-typed lookalike could wrap a
# live client, so neither is admitted.
PAPER_BROKER_TYPES: tuple[type, ...] = (DeterministicBroker, HybridPaperBroker)

PaperBroker = DeterministicBroker | HybridPaperBroker

PAPER_EXECUTION_VENUE = TicketVenue.PAPER.value
DEFAULT_PAPER_EXECUTION_ACTOR_ID = "paper-idea-executor"


class PaperOnlyLaneError(ValidationError):
    """Raised when a non-paper broker is offered to the paper execution lane."""


class IdeaNotExecutableError(ValidationError):
    """Raised when an idea is not in an executable state for this lane."""


class PaperExecutionError(ValidationError):
    """Raised when an admitted idea's paper order does not reach a recorded fill.

    By the time this is raised the idea is already SUBMITTED on the audit log,
    so admission refuses reruns; the operator resolves it with ``ideas cancel``
    or ``ideas mark-filled`` rather than by executing again.
    """


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    """Outcome of one paper execution: the order placed and the audit trail it left."""

    decision_id: str
    client_order_id: str
    order_id: str
    symbol: str
    side: str
    quantity: Decimal
    fill_price: Decimal | None
    final_state: str
    reconciliation: PaperFillReconciliationEntry

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "client_order_id": self.client_order_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "fill_price": str(self.fill_price) if self.fill_price is not None else None,
            "final_state": self.final_state,
            "reconciliation": self.reconciliation.to_dict(),
        }


def _require_paper_broker(broker: object) -> PaperBroker:
    if type(broker) not in PAPER_BROKER_TYPES:
        allowed = ", ".join(sorted(t.__name__ for t in PAPER_BROKER_TYPES))
        raise PaperOnlyLaneError(
            "Paper execution lane accepts only paper/mock brokers "
            f"({allowed}); got {type(broker).__name__}",
            field="broker",
            value=type(broker).__name__,
        )
    return broker  # type: ignore[return-value]


class PaperIdeaExecutor:
    """Executes APPROVED trade ideas against a paper broker.

    Construction enforces the paper-only boundary; ``resolve_approved_idea``
    enforces the workflow-state boundary. Both are deliberate refusals, not
    conveniences — tests pin them so later execution logic cannot loosen the
    lane by accident.
    """

    def __init__(
        self,
        service: TradeIdeaService,
        broker: PaperBroker,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._service = service
        self._broker = _require_paper_broker(broker)
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    @property
    def broker(self) -> PaperBroker:
        return self._broker

    def resolve_approved_idea(self, decision_id: str) -> TradeIdeaView:
        """Load an idea and admit it to the lane, or refuse with a typed error.

        Admission requires workflow state APPROVED and an unexpired
        ``time_horizon.expires_at``. Every other state — including SUBMITTED
        (already being executed) and FILLED — is refused so the lane can never
        double-execute or resurrect a terminal record.
        """
        view = self._service.get(decision_id)

        if view.state is not TradeIdeaState.APPROVED:
            raise IdeaNotExecutableError(
                f"Idea {decision_id} is not executable: state is "
                f"{view.state.value}, lane requires {TradeIdeaState.APPROVED.value}",
                field="state",
                value=view.state.value,
            )

        expires_at = view.idea.time_horizon.expires_at
        if expires_at is not None and expires_at <= self._now_factory():
            raise IdeaNotExecutableError(
                f"Idea {decision_id} is not executable: expired at " f"{expires_at.isoformat()}",
                field="expires_at",
                value=expires_at.isoformat(),
            )

        return view

    def execute(
        self,
        decision_id: str,
        *,
        actor_id: str = DEFAULT_PAPER_EXECUTION_ACTOR_ID,
    ) -> PaperExecutionResult:
        """Execute one APPROVED idea as a paper market order and audit the lifecycle.

        The submission is recorded before the broker is touched: if the process
        dies in between, the idea is SUBMITTED and admission refuses a rerun, so
        the lane can never place the same idea twice. The resulting fill is
        recorded through ``PaperFillReconciler`` — the same code path that
        reconciles persisted paper fills — so its payload-conflict and dedupe
        checks also guard the machine leg.
        """
        view = self.resolve_approved_idea(decision_id)
        symbol = view.idea.instrument
        side = _order_side(view.idea)
        quantity = _order_quantity(view.idea)
        client_order_id = decision_id

        self._service.record_submission(
            decision_id,
            actor_id=actor_id,
            venue=PAPER_EXECUTION_VENUE,
            external_order_id=client_order_id,
            reason=f"Paper executor submitting market {side} {quantity} {symbol}",
            actor_type=ActorType.SYSTEM,
        )

        order = self._broker.place_order(
            symbol,
            side=side,
            order_type="market",
            quantity=quantity,
            client_id=client_order_id,
        )

        if order.status is not OrderStatus.FILLED:
            raise PaperExecutionError(
                f"Paper broker did not fill order for idea {decision_id}: "
                f"status is {order.status.value}; idea remains submitted",
                field="order_status",
                value=order.status.value,
            )

        fill_event = PaperFillEvent(
            order_id=order.id,
            client_order_id=order.client_id or client_order_id,
            symbol=order.symbol,
            side=order.side.value.lower(),
            quantity=order.filled_quantity,
            price=order.avg_fill_price,
            status="filled",
            decision_id=decision_id,
        )
        report = PaperFillReconciler(
            self._service,
            actor_id=actor_id,
            venue=PAPER_EXECUTION_VENUE,
        ).reconcile_fills((fill_event,), apply=True)

        if report.recorded_count != 1:
            entries = (*report.matched, *report.unmatched, *report.skipped)
            reason = entries[0].reason if entries else "no reconciliation entry produced"
            raise PaperExecutionError(
                f"Paper fill for idea {decision_id} was not recorded: {reason}; "
                "idea remains submitted",
                field="reconciliation",
                value=reason,
            )

        final_view = self._service.get(decision_id)
        return PaperExecutionResult(
            decision_id=decision_id,
            client_order_id=client_order_id,
            order_id=order.id,
            symbol=order.symbol,
            side=side,
            quantity=order.filled_quantity,
            fill_price=order.avg_fill_price,
            final_state=final_view.state.value,
            reconciliation=report.matched[0],
        )


def _order_side(idea: TradeIdea) -> str:
    if idea.direction is TradeDirection.LONG:
        return "buy"
    if idea.direction is TradeDirection.SHORT:
        return "sell"
    raise IdeaNotExecutableError(
        f"Idea {idea.decision_id} is not executable: direction must be long or "
        f"short, got {idea.direction.value}",
        field="direction",
        value=idea.direction.value,
    )


def _order_quantity(idea: TradeIdea) -> Decimal:
    quantity = idea.sizing_recommendation.quantity
    if quantity is None or quantity <= 0:
        raise IdeaNotExecutableError(
            f"Idea {idea.decision_id} is not executable: "
            f"sizing_recommendation.quantity must be positive, got {quantity}",
            field="sizing_recommendation.quantity",
            value=str(quantity),
        )
    return quantity
