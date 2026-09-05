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

from gpt_trader.core import OrderSide, OrderStatus, OrderType
from gpt_trader.core.instruments import InstrumentParseError
from gpt_trader.core.order_intent import OrderIntent
from gpt_trader.core.trading_calendar import (
    SessionCalendarResolver,
    get_calendar_for_instrument,
)
from gpt_trader.errors import ValidationError
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.brokerages.paper import HybridPaperBroker
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ACTOR_ID,
    ActorType,
    AuditAction,
    AuditEvent,
    AutonomyResolution,
    CloseoutResolution,
    PaperFillEvent,
    PaperFillReconciler,
    PaperFillReconciliationEntry,
    TicketVenue,
    TradeDirection,
    TradeIdea,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaView,
    recorded_fill_from_view,
)
from gpt_trader.features.trade_ideas.execution_journal import (
    ExecutionJournalIntegrityError,
    PaperReceiptConflictError,
)
from gpt_trader.features.trade_ideas.policy import PolicyViolationError

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
        session_calendar_resolver: SessionCalendarResolver | None = None,
    ) -> None:
        self._service = service
        self._broker = _require_paper_broker(broker)
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._session_calendar_resolver = session_calendar_resolver or get_calendar_for_instrument

    @property
    def broker(self) -> PaperBroker:
        return self._broker

    def resolve_approved_idea(self, decision_id: str) -> TradeIdeaView:
        """Load an idea and admit it to the lane, or refuse with a typed error.

        Admission requires workflow state APPROVED, an unexpired
        ``time_horizon.expires_at``, and an open market session for the
        idea's instrument (issue #1232). Every other state — including
        SUBMITTED (already being executed) and FILLED — is refused so the
        lane can never double-execute or resurrect a terminal record.
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

        if view.idea.position_operation is not None:
            if (
                type(self._broker) is HybridPaperBroker
                and view.idea.direction is TradeDirection.SHORT
            ):
                raise IdeaNotExecutableError(
                    "HybridPaperBroker cannot reduce a short target: spot-long inventory only"
                )
            from gpt_trader.features.trade_ideas.policy import ApprovalPolicy

            evaluated_at = self._now_factory()
            budget = self._service.current_budget()
            context = self._service.approval_budget_context(
                exclude_decision_id=decision_id, now=evaluated_at
            )
            resolution = self._service.decision_autonomy(
                now=evaluated_at, budget=budget, budget_context=context
            )
            violations = [
                *self._service.position_operation_violations(view.idea),
                *ApprovalPolicy(resolution.mode).approval_violations(
                    view.idea,
                    actor_type=approval_event.actor_type if approval_event else ActorType.SYSTEM,
                    budget=budget,
                    open_approved_count=max(0, self._service.open_approved_count() - 1),
                    now=evaluated_at,
                    budget_context=context,
                    position_operation_validated=not self._service.position_operation_violations(
                        view.idea
                    ),
                ),
            ]
            if violations:
                raise IdeaNotExecutableError(
                    "Position operation no longer admitted: " + "; ".join(violations)
                )
        session_refusal = self._closed_session_refusal(decision_id, view.idea.instrument)
        if session_refusal is not None:
            raise session_refusal

        return _PaperExecutionAdmission(view=view, submission_evidence=submission_evidence)

    def _closed_session_refusal(
        self, decision_id: str, instrument: str
    ) -> IdeaNotExecutableError | None:
        """Refuse a sessioned instrument outside its market hours (issue #1232).

        A fill against a closed session would be priced from stale
        closed-market data, so the lane refuses loudly and the idea stays
        APPROVED. The scheduled cycle records the typed refusal as a skip and
        retries; execution resumes at the next open against that turn's own
        snapshot marks, so any overnight/weekend gap lands in the attributed
        fill price — honest, never smoothed or backdated.
        """
        try:
            calendar = self._session_calendar_resolver(instrument)
        except InstrumentParseError as error:
            return IdeaNotExecutableError(
                f"Idea {decision_id} is not executable: instrument "
                f"{instrument!r} is not classifiable to a trading session: {error}",
                field="instrument",
                value=instrument,
            )
        now = self._now_factory()
        try:
            if calendar.is_open(now):
                return None
            next_open = calendar.next_open(now)
        except ValueError as error:
            return IdeaNotExecutableError(
                f"Idea {decision_id} is not executable: session calendar "
                f"{calendar.session_id} cannot evaluate {now.isoformat()}: {error}",
                field="session",
                value=calendar.session_id,
            )
        detail = f"; next open {next_open.isoformat()}" if next_open is not None else ""
        return IdeaNotExecutableError(
            f"Idea {decision_id} is not executable: market closed for session "
            f"{calendar.session_id} at {now.isoformat()}{detail}",
            field="session",
            value=calendar.session_id,
        )

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
        journal = self._service.execution_journal
        with journal.repository.admission_transaction(
            (IdeaNotExecutableError, PolicyViolationError)
        ):
            admission = self._resolve_execution_admission(decision_id)
            view = admission.view
            symbol = view.idea.instrument
            side = _order_side(view.idea)
            if view.idea.position_operation is not None:
                from gpt_trader.features.trade_ideas.position_operations import intent_for_idea

                intent = intent_for_idea(self._service, view.idea)
                side = intent.side.value.lower()
                quantity = intent.quantity
            else:
                quantity = _order_quantity(view.idea)
                intent = OrderIntent(
                    decision_id, symbol, OrderSide(side.upper()), OrderType.MARKET, quantity
                )
            client_order_id = decision_id
            if view.idea.position_operation is not None:
                journal.record_intent(decision_id, view.idea.record_hash(), intent.to_dict())
            self._service.record_submission(
                decision_id,
                actor_id=actor_id,
                venue=PAPER_EXECUTION_VENUE,
                external_order_id=client_order_id,
                reason=f"Paper executor submitting market {side} {quantity} {symbol}",
                actor_type=ActorType.SYSTEM,
                evidence=admission.submission_evidence,
            )
            if view.idea.position_operation is None:
                journal.record_intent(decision_id, view.idea.record_hash(), intent.to_dict())

        order = self._broker.place_order(**intent.broker_kwargs())
        try:
            intent.validate_receipt(order)
        except ValueError as error:
            raise PaperExecutionError(
                f"Paper fill was not recorded: receipt conflicts with admitted intent: {error}",
                field="receipt",
            ) from error

        fill_event = PaperFillEvent(
            order_id=order.id,
            client_order_id=order.client_id or client_order_id,
            symbol=order.symbol,
            side=order.side.value.lower(),
            quantity=order.filled_quantity,
            price=order.avg_fill_price,
            status=order.status.value.lower(),
            decision_id=decision_id,
            filled_at=order.updated_at or order.submitted_at or self._now_factory(),
        )
        receipt_payload = fill_event.to_dict()
        if view.idea.position_operation is not None:
            observed_at = self._now_factory()
            submitted_at = self._service.get(decision_id).events[-1].timestamp
            if (
                fill_event.filled_at is None
                or fill_event.filled_at.utcoffset() is None
                or fill_event.filled_at > observed_at
                or fill_event.filled_at < submitted_at
            ):
                raise PaperExecutionError(
                    "Targeted receipt has impossible execution time", field="receipt"
                )
            receipt_payload["observed_at"] = observed_at.isoformat()
        # Commit the observed broker result before any lifecycle reconciliation.
        # A crash after this point can recover without another broker call.
        try:
            journal.record_receipt(decision_id, receipt_payload)
        except PaperReceiptConflictError as error:
            raise PaperExecutionError(
                f"Paper fill for idea {decision_id} was not recorded: {error}", field="receipt"
            ) from error
        if order.status is not OrderStatus.FILLED:
            raise PaperExecutionError(
                f"Paper broker did not fill order for idea {decision_id}: "
                f"status is {order.status.value}; idea remains submitted",
                field="order_status",
                value=order.status.value,
            )
        reconciliation = self._reconcile_receipt(decision_id, actor_id=actor_id)
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
            reconciliation=reconciliation,
        )

    def _reconcile_receipt(
        self, decision_id: str, *, actor_id: str
    ) -> PaperFillReconciliationEntry:
        journal = self._service.execution_journal
        with journal.repository.transaction(write=True):
            entry = journal.entries()[decision_id]
            receipt = entry.receipt
            if receipt is None or receipt["status"] != "filled":
                raise PaperExecutionError("No terminal fill receipt available", field="receipt")
            view = self._service.get(decision_id)
            journal.validate_binding(entry, view.idea)
            if view.state is TradeIdeaState.FILLED:
                fill = recorded_fill_from_view(view)
                if (
                    fill is None
                    or fill.price != Decimal(receipt["price"])
                    or fill.quantity != Decimal(receipt["quantity"])
                    or fill.external_order_id != receipt["order_id"]
                    or fill.filled_at != datetime.fromisoformat(receipt["filled_at"])
                ):
                    raise ExecutionJournalIntegrityError(
                        "Audited fill conflicts with durable broker receipt"
                    )
            event = PaperFillEvent(
                order_id=receipt["order_id"],
                client_order_id=receipt["client_order_id"],
                symbol=receipt["symbol"],
                side=receipt["side"],
                quantity=Decimal(receipt["quantity"]),
                price=Decimal(receipt["price"]),
                status=receipt["status"],
                decision_id=decision_id,
                filled_at=datetime.fromisoformat(receipt["filled_at"]),
            )
            report = PaperFillReconciler(
                self._service, actor_id=actor_id, venue=PAPER_EXECUTION_VENUE
            ).reconcile_fills((event,), apply=True)
            if report.unmatched or (not report.matched and not report.skipped):
                raise ExecutionJournalIntegrityError(
                    "Durable receipt cannot reconcile with current workflow"
                )
            if self._service.get(decision_id).state is not TradeIdeaState.FILLED:
                raise ExecutionJournalIntegrityError(
                    "Reconciliation did not produce an audited fill"
                )
            journal.mark_reconciled(decision_id)
            if view.idea.position_operation is not None:
                from gpt_trader.features.trade_ideas.position_operations import finalize_position

                finalize_position(
                    self._service,
                    view.idea.position_operation.target_decision_id,
                    actor_id=actor_id,
                    resolution=CloseoutResolution(view.idea.position_operation.resolution),
                )
            return (report.matched or report.skipped)[0]

    def recover_receipts(
        self, *, actor_id: str = DEFAULT_PAPER_EXECUTION_ACTOR_ID
    ) -> dict[str, Any]:
        """Replay only durable observed fills; never query or resubmit to a broker."""
        entries = self._service.execution_journal.entries()
        recovered = []
        unresolved = []
        for entry in entries.values():
            if entry.reconciled:
                continue
            if entry.receipt is not None and entry.receipt["status"] == "filled":
                result = self._reconcile_receipt(entry.decision_id, actor_id=actor_id)
                if result.recorded_fill:
                    recovered.append(entry.decision_id)
        for view in self._service.list_views(TradeIdeaState.SUBMITTED):
            pending_entry = entries.get(view.idea.decision_id)
            receipt = pending_entry.receipt if pending_entry is not None else None
            unresolved.append(
                {
                    "decision_id": view.idea.decision_id,
                    "reason": (
                        "submission has no durable broker receipt; never automatically resend"
                        if receipt is None
                        else f"broker receipt status={receipt['status']}; no terminal fill"
                    ),
                }
            )
        return {"recovered_decision_ids": recovered, "unresolved": unresolved}


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
