"""In-process event-driven idea lane: propose → auto-approve → paper-execute per event.

The centerpiece of docs/decisions/adopt-event-driven-execution-topology.md
(#1191): the live_trade engine hands each already-proposed strategy idea to
this lane, which carries it through the risk kernel — system approval, then
the execution-time autonomy re-check — and into ``PaperIdeaExecutor`` in the
same process, with no queue latency and no cron heartbeat. The lane owns no
gating logic: every admit/deny is a ``RiskKernel`` check, and every outcome
lands on the same append-only audit trail as the batch lane, so idea-level
evidence is topology-agnostic.

The Stage 2 operator gates are shared, not duplicated: without
``GPT_TRADER_IDEAS_AUTO_APPROVAL`` the idea stays proposed for the human
queue / batch sweep, and without ``GPT_TRADER_IDEAS_AUTO_EXECUTION`` an
approved idea awaits the batch executor. Kill-switch and ratchet are
effective mid-stream because autonomy is re-resolved by the kernel at both
the approval and the execution boundary of every event; a denial between
propose and execute is recorded as an audited ``auto_execution_skipped``
event, never silently dropped.

Paper only: the lane accepts the same paper/mock broker set as the executor
and prices each fill from the event's own mark via ``set_mark``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.idea_execution.executor import (
    AUTO_EXECUTION_ENV_VAR,
    EVENT_LANE_ACTOR_ID,
    IdeaNotExecutableError,
    PaperExecutionError,
    PaperExecutionResult,
    PaperIdeaExecutor,
    resolve_auto_execution_enabled,
)
from gpt_trader.features.trade_ideas import (
    AUTO_APPROVAL_ENV_VAR,
    ActorType,
    TradeIdeaService,
    TradeIdeaView,
    resolve_auto_approval_enabled,
)

EVENT_LANE_REASON_PREFIX = "event-driven lane: "


class EventLaneStage(str, Enum):
    """Terminal stage one event reached inside the lane."""

    QUEUED = "queued"
    APPROVAL_DENIED = "approval_denied"
    APPROVED = "approved"
    EXECUTION_DENIED = "execution_denied"
    EXECUTION_REFUSED = "execution_refused"
    EXECUTED = "executed"


@dataclass(frozen=True, slots=True)
class EventLaneOutcome:
    """Audited outcome of routing one proposed idea through the lane."""

    decision_id: str
    stage: EventLaneStage
    detail: str
    violations: tuple[str, ...] = ()
    execution: PaperExecutionResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "stage": self.stage.value,
            "detail": self.detail,
            "violations": list(self.violations),
            "execution": self.execution.to_dict() if self.execution is not None else None,
        }


class EventDrivenIdeaLane:
    """Carries one proposed idea through kernel admit → approval → paper execution.

    Composition mirrors ``PaperCycleRunner``: the service (and through it the
    kernel), the deterministic paper broker, and the clock are injected. The
    broker is deterministic specifically because the lane prices each fill
    from the event's own mark — the in-process analogue of the batch turn's
    snapshot-priced honesty contract.
    """

    def __init__(
        self,
        service: TradeIdeaService,
        broker: DeterministicBroker,
        *,
        actor_id: str = EVENT_LANE_ACTOR_ID,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._service = service
        self._broker = broker
        self._actor_id = actor_id
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._executor = PaperIdeaExecutor(service, broker, now_factory=self._now_factory)

    @property
    def actor_id(self) -> str:
        return self._actor_id

    def process(self, view: TradeIdeaView, *, mark: Decimal) -> EventLaneOutcome:
        """Route one freshly proposed idea to its audited terminal stage.

        Every deny is recorded on the idea's audit trail through the kernel;
        the returned outcome is a log/telemetry summary, never the record of
        truth.
        """
        idea = view.idea
        decision_id = idea.decision_id

        if not resolve_auto_approval_enabled():
            return EventLaneOutcome(
                decision_id=decision_id,
                stage=EventLaneStage.QUEUED,
                detail=(
                    f"{AUTO_APPROVAL_ENV_VAR} is off; idea remains proposed "
                    "for human review or the batch sweep"
                ),
            )

        approval_check = self._service.kernel.check_approval(idea, actor_type=ActorType.SYSTEM)
        if not approval_check.admitted:
            self._service.kernel.record_denied_approval(
                idea,
                approval_check,
                actor_id=self._actor_id,
                reason=(
                    f"{EVENT_LANE_REASON_PREFIX}skipped because approval-policy "
                    "violations remain"
                ),
            )
            return EventLaneOutcome(
                decision_id=decision_id,
                stage=EventLaneStage.APPROVAL_DENIED,
                detail="kernel denied system approval; idea remains proposed",
                violations=approval_check.violations,
            )

        self._service.kernel.record_approval(
            idea,
            approval_check,
            actor_id=self._actor_id,
            reason=(
                f"{EVENT_LANE_REASON_PREFIX}zero approval-policy violations inside "
                f"the budget envelope of risk budget version {approval_check.budget_version}"
            ),
            evidence=approval_check.admission_evidence(),
        )

        if not resolve_auto_execution_enabled():
            return EventLaneOutcome(
                decision_id=decision_id,
                stage=EventLaneStage.APPROVED,
                detail=(
                    f"{AUTO_EXECUTION_ENV_VAR} is off; approved idea awaits " "the batch executor"
                ),
            )

        execution_check = self._service.kernel.check_execution(
            decision_id, actor_type=ActorType.SYSTEM
        )
        if not execution_check.admitted:
            self._service.kernel.record_denied_execution(
                idea,
                execution_check,
                actor_id=self._actor_id,
                reason=(
                    f"{EVENT_LANE_REASON_PREFIX}execution denied at the kernel's "
                    "execution gate; idea remains approved"
                ),
            )
            return EventLaneOutcome(
                decision_id=decision_id,
                stage=EventLaneStage.EXECUTION_DENIED,
                detail="kernel denied execution; idea remains approved",
                violations=execution_check.violations,
            )

        self._broker.set_mark(idea.instrument, mark)
        try:
            result = self._executor.execute(decision_id, actor_id=self._actor_id)
        except (IdeaNotExecutableError, PaperExecutionError) as error:
            # The executor's own admission raced or refused (for example the
            # autonomy mode dropped between the kernel check above and the
            # executor's re-check). Typed refusals are the lane's rules
            # working; surface them without masking as success.
            return EventLaneOutcome(
                decision_id=decision_id,
                stage=EventLaneStage.EXECUTION_REFUSED,
                detail=str(error),
            )

        return EventLaneOutcome(
            decision_id=decision_id,
            stage=EventLaneStage.EXECUTED,
            detail=f"paper-executed at mark {mark}",
            execution=result,
        )


__all__ = [
    "EVENT_LANE_ACTOR_ID",
    "EVENT_LANE_REASON_PREFIX",
    "EventDrivenIdeaLane",
    "EventLaneOutcome",
    "EventLaneStage",
]
