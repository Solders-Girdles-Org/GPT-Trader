"""Trade-idea workflow state machine.

States and transitions follow the approval workflow in
docs/DIRECTION.md. Execution may proceed only from
``APPROVED``, and approval is always a human event in
``human_approved_execution`` mode.
"""

from __future__ import annotations

from enum import Enum

from gpt_trader.errors import ValidationError


class TradeIdeaState(str, Enum):
    PROPOSED = "proposed"
    NEEDS_CHANGES = "needs_changes"
    REJECTED = "rejected"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ALLOWED_TRANSITIONS: dict[TradeIdeaState, frozenset[TradeIdeaState]] = {
    TradeIdeaState.PROPOSED: frozenset(
        {
            TradeIdeaState.PROPOSED,
            TradeIdeaState.NEEDS_CHANGES,
            TradeIdeaState.APPROVED,
            TradeIdeaState.REJECTED,
            TradeIdeaState.EXPIRED,
        }
    ),
    TradeIdeaState.NEEDS_CHANGES: frozenset(
        {
            TradeIdeaState.PROPOSED,
            TradeIdeaState.REJECTED,
            TradeIdeaState.EXPIRED,
        }
    ),
    TradeIdeaState.APPROVED: frozenset(
        {
            TradeIdeaState.SUBMITTED,
            TradeIdeaState.CANCELLED,
            TradeIdeaState.EXPIRED,
        }
    ),
    TradeIdeaState.SUBMITTED: frozenset(
        {
            TradeIdeaState.FILLED,
            TradeIdeaState.CANCELLED,
        }
    ),
    TradeIdeaState.REJECTED: frozenset(),
    TradeIdeaState.FILLED: frozenset(),
    TradeIdeaState.CANCELLED: frozenset(),
    TradeIdeaState.EXPIRED: frozenset(),
}

TERMINAL_STATES: frozenset[TradeIdeaState] = frozenset(
    state for state, targets in ALLOWED_TRANSITIONS.items() if not targets
)


class InvalidTransitionError(ValidationError):
    """Raised when a workflow transition violates the state machine."""


def validate_transition(before: TradeIdeaState | None, after: TradeIdeaState) -> None:
    """Validate a workflow transition, raising ``InvalidTransitionError`` if illegal.

    ``before=None`` represents record creation; the only legal initial state is
    ``PROPOSED`` (AI-generated ideas always start unapproved).
    """
    if before is None:
        if after is not TradeIdeaState.PROPOSED:
            raise InvalidTransitionError(
                f"Trade ideas must be created in state '{TradeIdeaState.PROPOSED.value}', "
                f"got '{after.value}'",
                field="after_state",
                value=after.value,
            )
        return

    if after not in ALLOWED_TRANSITIONS[before]:
        raise InvalidTransitionError(
            f"Illegal trade-idea transition '{before.value}' -> '{after.value}'",
            field="after_state",
            value=after.value,
        )
