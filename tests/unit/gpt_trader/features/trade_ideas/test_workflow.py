from __future__ import annotations

import pytest

from gpt_trader.features.trade_ideas import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    TradeIdeaState,
    validate_transition,
)


def test_creation_must_start_proposed() -> None:
    validate_transition(None, TradeIdeaState.PROPOSED)


@pytest.mark.parametrize(
    "state",
    [state for state in TradeIdeaState if state is not TradeIdeaState.PROPOSED],
)
def test_creation_in_any_other_state_is_rejected(state: TradeIdeaState) -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition(None, state)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (TradeIdeaState.PROPOSED, TradeIdeaState.APPROVED),
        (TradeIdeaState.PROPOSED, TradeIdeaState.PROPOSED),
        (TradeIdeaState.PROPOSED, TradeIdeaState.NEEDS_CHANGES),
        (TradeIdeaState.PROPOSED, TradeIdeaState.REJECTED),
        (TradeIdeaState.PROPOSED, TradeIdeaState.EXPIRED),
        (TradeIdeaState.NEEDS_CHANGES, TradeIdeaState.PROPOSED),
        (TradeIdeaState.APPROVED, TradeIdeaState.SUBMITTED),
        (TradeIdeaState.APPROVED, TradeIdeaState.CANCELLED),
        (TradeIdeaState.APPROVED, TradeIdeaState.EXPIRED),
        (TradeIdeaState.SUBMITTED, TradeIdeaState.FILLED),
        (TradeIdeaState.SUBMITTED, TradeIdeaState.CANCELLED),
    ],
)
def test_allowed_transitions(before: TradeIdeaState, after: TradeIdeaState) -> None:
    validate_transition(before, after)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (TradeIdeaState.PROPOSED, TradeIdeaState.SUBMITTED),
        (TradeIdeaState.PROPOSED, TradeIdeaState.FILLED),
        (TradeIdeaState.NEEDS_CHANGES, TradeIdeaState.APPROVED),
        (TradeIdeaState.SUBMITTED, TradeIdeaState.APPROVED),
        (TradeIdeaState.SUBMITTED, TradeIdeaState.EXPIRED),
    ],
)
def test_blocked_transitions(before: TradeIdeaState, after: TradeIdeaState) -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition(before, after)


def test_execution_requires_prior_approval() -> None:
    submit_sources = [
        before
        for before, targets in ALLOWED_TRANSITIONS.items()
        if TradeIdeaState.SUBMITTED in targets
    ]

    assert submit_sources == [TradeIdeaState.APPROVED]


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda state: state.value))
def test_terminal_states_allow_no_transitions(terminal: TradeIdeaState) -> None:
    for after in TradeIdeaState:
        with pytest.raises(InvalidTransitionError):
            validate_transition(terminal, after)


def test_expected_terminal_states() -> None:
    assert TERMINAL_STATES == {
        TradeIdeaState.REJECTED,
        TradeIdeaState.FILLED,
        TradeIdeaState.CANCELLED,
        TradeIdeaState.EXPIRED,
    }
