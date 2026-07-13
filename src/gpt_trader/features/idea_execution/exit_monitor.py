"""Resolve filled paper ideas into audited closeouts (issue #1218).

A ``FILLED`` idea is an open paper position. This monitor resolves it against the
candles recorded after entry — first touch of the plan's target or stop, or a
mark-to-market once past expiry — and records the outcome plus realized profit
and loss on the audit trail through ``TradeIdeaService``.

It reuses the replay scorer (``score_trade_idea``) so a live closeout and the
scorecard's replay evidence share one resolution methodology; the structured
exit plan (#1218a) supplies the levels via ``exit_plan_scoring_levels``. Realized
P&L is recorded as a dollar amount (``quantity`` times the price move), which is
exactly what the Stage 1->2 calibration / expectancy / benchmark-edge gates read
(``realized_profit_loss_amount`` vs the idea's recorded ``max_loss.amount``). The
percent field is deliberately left unset to avoid conflating a position return
with a percent-of-account figure.

Paper-only and read-model driven: it records lifecycle facts only through the
service, never touches a broker, and closes an idea only once it is genuinely
resolved (a bare end-of-candles is not a timeout until the idea has expired).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from gpt_trader.core import Candle
from gpt_trader.core.instruments import InstrumentParseError
from gpt_trader.core.trading_calendar import (
    SessionCalendarResolver,
    get_calendar_for_instrument,
)
from gpt_trader.features.trade_ideas import (
    ActorType,
    AuditAction,
    CloseoutAttribution,
    CloseoutResolution,
    MarketSnapshot,
    ReplayOutcome,
    ReplayScoringError,
    TradeDirection,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaView,
    exit_plan_scoring_levels,
    score_trade_idea,
)

DEFAULT_EXIT_MONITOR_ACTOR_ID = "exit-monitor"

# A resolved replay outcome maps to the closeout resolution that describes it;
# NOT_FILLED / NO_FUTURE_DATA are absent because they mean "still open".
_OUTCOME_TO_RESOLUTION: dict[ReplayOutcome, CloseoutResolution] = {
    ReplayOutcome.TARGET_HIT: CloseoutResolution.THESIS_TARGET,
    ReplayOutcome.STOP_HIT: CloseoutResolution.INVALIDATION,
    ReplayOutcome.TIMED_OUT: CloseoutResolution.EXPIRY,
}


@dataclass(frozen=True, slots=True)
class ExitMonitorPass:
    """One exit-monitor pass: closeouts recorded plus session skips left open."""

    recorded: tuple[CloseoutAttribution, ...]
    skipped_closed_sessions: tuple[dict[str, str], ...] = ()


def resolve_filled_ideas(
    service: TradeIdeaService,
    snapshot: MarketSnapshot,
    *,
    now: datetime,
    actor_id: str = DEFAULT_EXIT_MONITOR_ACTOR_ID,
    session_calendar_resolver: SessionCalendarResolver | None = None,
) -> ExitMonitorPass:
    """Close every resolvable filled idea against ``snapshot``'s candles.

    Returns the closeout attributions recorded this pass plus the ideas it
    refused to resolve because their market session is closed at ``now``
    (issue #1232): resolving an equity position against a closed session
    would time it out or mark it against stale data, so the position stays
    ``FILLED`` — loudly, with the skip on the pass record — and resolves at
    the next open against that turn's own candles. Ideas that merely cannot
    be resolved yet (no candles for the instrument, no size, no exit levels,
    entry not reached in the recorded window, or an unexpired end-of-candles)
    are likewise left ``FILLED`` for a later turn.
    """
    resolver = session_calendar_resolver or get_calendar_for_instrument
    candles_by_instrument = {
        series.symbol.casefold(): series.candles for series in snapshot.series if series.candles
    }
    recorded: list[CloseoutAttribution] = []
    skipped_closed: list[dict[str, str]] = []
    for view in service.list_views(TradeIdeaState.FILLED):
        if view.closeout_attribution is not None:
            continue
        closed_skip = _closed_session_skip(view, resolver, now)
        if closed_skip is not None:
            skipped_closed.append(closed_skip)
            continue
        attribution = _resolve_one(
            view,
            candles_by_instrument,
            snapshot=snapshot,
            now=now,
            service=service,
            actor_id=actor_id,
        )
        if attribution is not None:
            recorded.append(attribution)
    return ExitMonitorPass(
        recorded=tuple(recorded),
        skipped_closed_sessions=tuple(skipped_closed),
    )


def _closed_session_skip(
    view: TradeIdeaView,
    resolver: SessionCalendarResolver,
    now: datetime,
) -> dict[str, str] | None:
    """Return a skip entry when the idea's market session is closed at ``now``."""
    instrument = view.idea.instrument
    decision_id = view.idea.decision_id
    try:
        calendar = resolver(instrument)
    except InstrumentParseError as error:
        return {
            "decision_id": decision_id,
            "instrument": instrument,
            "reason": f"instrument is not classifiable to a trading session: {error}",
        }
    try:
        if calendar.is_open(now):
            return None
        next_open = calendar.next_open(now)
    except ValueError as error:
        return {
            "decision_id": decision_id,
            "instrument": instrument,
            "reason": (
                f"session calendar {calendar.session_id} cannot evaluate "
                f"{now.isoformat()}: {error}"
            ),
        }
    detail = f"; next open {next_open.isoformat()}" if next_open is not None else ""
    return {
        "decision_id": decision_id,
        "instrument": instrument,
        "reason": (f"market closed for session {calendar.session_id} at {now.isoformat()}{detail}"),
    }


def _resolve_one(
    view: TradeIdeaView,
    candles_by_instrument: Mapping[str, Sequence[Candle]],
    *,
    snapshot: MarketSnapshot,
    now: datetime,
    service: TradeIdeaService,
    actor_id: str,
) -> CloseoutAttribution | None:
    idea = view.idea
    candles = candles_by_instrument.get(idea.instrument.casefold())
    quantity = idea.sizing_recommendation.quantity
    expires_at = idea.time_horizon.expires_at
    proposed_at = _proposed_at(view)
    if not candles or quantity is None or expires_at is None or proposed_at is None:
        return None

    try:
        result = score_trade_idea(
            idea,
            as_of=proposed_at,
            future_candles=candles,
            level_extractor=exit_plan_scoring_levels,
        )
    except ReplayScoringError:
        return None

    resolution = _OUTCOME_TO_RESOLUTION.get(result.outcome)
    if resolution is None:
        return None  # NOT_FILLED / NO_FUTURE_DATA -> position not resolvable yet
    # A timeout is only a real exit once the idea has expired; before that it is
    # merely the end of the candles recorded so far.
    if result.outcome is ReplayOutcome.TIMED_OUT and now < expires_at:
        return None
    if result.entry_price is None or result.exit_price is None:
        return None

    realized_amount = _realized_amount(
        idea.direction, quantity, result.entry_price, result.exit_price
    )
    return service.record_closeout_attribution(
        idea.decision_id,
        actor_id=actor_id,
        actor_type=ActorType.SYSTEM,
        resolution=resolution,
        realized_profit_loss_amount=realized_amount,
        evidence=(
            f"exit_monitor:{result.outcome.value}",
            f"entry_price={result.entry_price}",
            f"exit_price={result.exit_price}",
            f"quantity={quantity}",
            f"snapshot_as_of={snapshot.as_of.isoformat()}",
        ),
    )


def _realized_amount(
    direction: TradeDirection,
    quantity: Decimal,
    entry_price: Decimal,
    exit_price: Decimal,
) -> Decimal:
    move = (
        exit_price - entry_price if direction is TradeDirection.LONG else entry_price - exit_price
    )
    return quantity * move


def _proposed_at(view: TradeIdeaView) -> datetime | None:
    for event in view.events:
        if event.action is AuditAction.PROPOSED:
            return event.timestamp
    return None
