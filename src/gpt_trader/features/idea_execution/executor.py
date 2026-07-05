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

import os
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
    AUTO_APPROVAL_ACTOR_ID,
    ActorType,
    AuditAction,
    AuditEvent,
    AutonomyResolution,
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
# Actor id stamped on the event-driven lane's kernel approvals/executions
# (#1191). Defined here rather than in event_lane.py so the admission gate
# below can recognize it without a circular import.
EVENT_LANE_ACTOR_ID = "event-idea-lane"
# System approvals the Stage 2 execution gate recognizes: the batch
# auto-approval sweep and the in-process event-driven lane. Any other
# non-human approval is refused.
SYSTEM_APPROVAL_ACTOR_IDS = frozenset({AUTO_APPROVAL_ACTOR_ID, EVENT_LANE_ACTOR_ID})
AUTO_EXECUTION_ENV_VAR = "GPT_TRADER_IDEAS_AUTO_EXECUTION"
_AUTO_EXECUTION_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


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
class _PaperExecutionAdmission:
    view: TradeIdeaView
    submission_evidence: tuple[str, ...] = ()


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


def resolve_auto_execution_enabled() -> bool:
    """True only when the operator explicitly enabled system-approved paper execution.

    Deliberately environment-only, with no argument override: enabling the
    Stage 2 execution gate is an operator configuration act, never something
    an interface can pass in (docs/decisions/stage2-execution-gate.md).
    """
    configured = os.environ.get(AUTO_EXECUTION_ENV_VAR, "").strip().casefold()
    return configured in _AUTO_EXECUTION_ENABLED_VALUES


def _latest_approval_event(view: TradeIdeaView) -> AuditEvent | None:
    for event in reversed(view.events):
        if event.action is AuditAction.APPROVED:
            return event
    return None


def _human_approval_required_error(decision_id: str, actor: str) -> IdeaNotExecutableError:
    return IdeaNotExecutableError(
        f"Idea {decision_id} is not executable: latest approval "
        f"actor_type is '{actor}', paper execution requires human approval",
        field="approval_actor_type",
        value=actor,
    )


def _gate_evidence(
    approval_event: AuditEvent,
    resolution: AutonomyResolution,
) -> tuple[str, ...]:
    version = resolution.version if resolution.version is not None else "none"
    return (
        f"{AUTO_EXECUTION_ENV_VAR}=enabled",
        f"autonomy_state version {version} mode={resolution.mode.value} "
        f"(source={resolution.source})",
        f"approval_actor actor_type={approval_event.actor_type.value} "
        f"actor_id={approval_event.actor_id}",
    )


def paper_auto_execution_gate_evidence(
    service: TradeIdeaService,
    approval_event: AuditEvent | None,
    *,
    now: datetime,
) -> tuple[str, ...] | None:
    """Return submission evidence when a system approval passes the Stage 2 gate.

    The autonomy re-check is the risk kernel's execution gate; this lane adds
    only its own admission preconditions (a system approval from a recognized
    actor — the batch sweep or the event-driven lane — and the operator-set
    auto-execution flag) and the env-specific evidence line.
    """
    if approval_event is None:
        return None
    if approval_event.actor_type is not ActorType.SYSTEM:
        return None
    if approval_event.actor_id not in SYSTEM_APPROVAL_ACTOR_IDS:
        return None
    if not resolve_auto_execution_enabled():
        return None
    check = service.kernel.check_execution(
        approval_event.decision_id,
        actor_type=ActorType.SYSTEM,
        now=now,
    )
    if not check.admitted:
        return None
    return _gate_evidence(approval_event, check.autonomy)


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
        return self._resolve_execution_admission(decision_id).view

    def _resolve_execution_admission(self, decision_id: str) -> _PaperExecutionAdmission:
        view = self._service.get(decision_id)

        if view.state is not TradeIdeaState.APPROVED:
            raise IdeaNotExecutableError(
                f"Idea {decision_id} is not executable: state is "
                f"{view.state.value}, lane requires {TradeIdeaState.APPROVED.value}",
                field="state",
                value=view.state.value,
            )

        approval_event = _latest_approval_event(view)
        submission_evidence: tuple[str, ...] = ()
        if approval_event is None or approval_event.actor_type is not ActorType.HUMAN:
            actor = approval_event.actor_type.value if approval_event else "none"
            submission_evidence = (
                paper_auto_execution_gate_evidence(
                    self._service,
                    approval_event,
                    now=self._now_factory(),
                )
                or ()
            )
            if not submission_evidence:
                raise _human_approval_required_error(decision_id, actor)

        expires_at = view.idea.time_horizon.expires_at
        if expires_at is not None and expires_at <= self._now_factory():
            raise IdeaNotExecutableError(
                f"Idea {decision_id} is not executable: expired at " f"{expires_at.isoformat()}",
                field="expires_at",
                value=expires_at.isoformat(),
            )

        return _PaperExecutionAdmission(view=view, submission_evidence=submission_evidence)

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
        admission = self._resolve_execution_admission(decision_id)
        view = admission.view
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
            evidence=admission.submission_evidence,
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
