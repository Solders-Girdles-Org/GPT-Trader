"""Paper idea executor: broker boundary and executability checks.

This module ships the lane's structural guarantees ahead of any execution
logic (issue #1144, first PR): the constructor contract that makes live
brokers unreachable, and the refusal logic that admits only APPROVED,
unexpired ideas. Order placement lands in a follow-up PR on top of these
guarantees; no submission code exists here yet.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from gpt_trader.errors import ValidationError
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.brokerages.paper import HybridPaperBroker
from gpt_trader.features.trade_ideas import (
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


class PaperOnlyLaneError(ValidationError):
    """Raised when a non-paper broker is offered to the paper execution lane."""


class IdeaNotExecutableError(ValidationError):
    """Raised when an idea is not in an executable state for this lane."""


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
