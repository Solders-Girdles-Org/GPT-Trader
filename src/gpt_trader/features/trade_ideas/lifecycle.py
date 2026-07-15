"""Canonical lifecycle read classification for trade-idea views (#1212).

FILLED is a terminal workflow state (an open paper position never transitions
again; closure is expressed by a ``CloseoutAttribution``), so state alone
conflates a legitimately open position with a missing closeout. This read model
is the one place that distinction is made; report, scorecard, and CLI
consumers all classify through it.

Overdue reuses the existing expiry/exit-monitor contract rather than inventing
a second lifecycle clock: the exit monitor marks an unresolved fill to market
once ``now`` reaches the idea's ``expires_at``, so an unclosed fill past that
instant (or without any expiry to resolve against) is an overdue evidence
failure. Any other terminal idea without attribution is overdue immediately —
the cycle's auto-attribution leg owes it a closeout in the same turn.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from gpt_trader.features.trade_ideas.service_models import TradeIdeaView
from gpt_trader.features.trade_ideas.workflow import TERMINAL_STATES, TradeIdeaState


class LifecycleClassification(str, Enum):
    """Where one idea stands between proposal and attributed closeout."""

    NOT_APPLICABLE = "not_applicable"
    OPEN_FILLED = "open_filled"
    CLOSED = "closed"
    OVERDUE_UNATTRIBUTED = "overdue_unattributed"


def classify_lifecycle(view: TradeIdeaView, *, now: datetime) -> LifecycleClassification:
    """Classify one view; ``now`` decides whether an open fill is overdue."""
    if view.state not in TERMINAL_STATES:
        return LifecycleClassification.NOT_APPLICABLE
    if view.closeout_attribution is not None:
        return LifecycleClassification.CLOSED
    if view.state is TradeIdeaState.FILLED:
        expires_at = view.idea.time_horizon.expires_at
        if expires_at is not None and now < expires_at:
            return LifecycleClassification.OPEN_FILLED
    return LifecycleClassification.OVERDUE_UNATTRIBUTED


def unattributed_reason(view: TradeIdeaView, *, now: datetime) -> str | None:
    """Explain an overdue classification; ``None`` for every other class."""
    if classify_lifecycle(view, now=now) is not LifecycleClassification.OVERDUE_UNATTRIBUTED:
        return None
    if view.state is not TradeIdeaState.FILLED:
        return (
            f"terminal state '{view.state.value}' has no closeout attribution; "
            "awaiting the cycle's auto-attribution leg"
        )
    expires_at = view.idea.time_horizon.expires_at
    if expires_at is None:
        return "filled idea has no expiry, so exit monitoring cannot resolve it"
    return (
        f"filled idea expired {expires_at.isoformat()} without a closeout; "
        "the exit monitor records its blocking cause per turn as "
        "exit_monitor_unresolved on the cycle manifest"
    )
