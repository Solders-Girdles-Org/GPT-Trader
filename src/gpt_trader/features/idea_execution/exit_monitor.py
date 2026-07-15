"""Resolve filled paper ideas into audited closeouts (issue #1218).

A ``FILLED`` idea is an open paper position. This monitor resolves it against
the candles recorded after the venue-confirmed fill — first touch of the plan's
target or stop, or a mark-to-market once past expiry — and records the outcome
plus realized profit and loss on the audit trail through ``TradeIdeaService``.

Exit evaluation is anchored on the recorded fill (#1212), not on a replay of the
proposal: ``score_filled_trade_idea`` inspects only candles at/after the fill
timestamp, so a confirmed fill outside the planned entry zone still resolves.
The entry price is, in order of evidentiary strength: the fill price on the
FILLED audit event's evidence, a caller-supplied durable fallback (e.g. the
paper cycle's manifest execution rows, for fills recorded before evidence
persistence existed), or the plan's zone midpoint — the documented sizing
assumption — with the source disclosed on the closeout evidence. Realized P&L
is recorded as a dollar amount (``quantity`` times the price move), which is
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
    CloseoutAttribution,
    CloseoutResolution,
    MarketSnapshot,
    RecordedFill,
    ReplayOutcome,
    ReplayScoringError,
    TradeDirection,
    TradeIdeaService,
    TradeIdeaState,
    TradeIdeaView,
    exit_plan_scoring_levels,
    recorded_fill_from_view,
    score_filled_trade_idea,
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
    """One exit-monitor pass: closeouts recorded plus positions left open loudly.

    ``unresolved`` carries the concrete blocking cause for every position the
    pass could not close even though closure is owed: permanent defects
    (unscoreable exit levels, no recorded quantity, no expiry) always, and
    missing-data conditions (no candles, no post-fill window) once the idea has
    expired. A position quietly waiting inside its horizon is not unresolved.
    """

    recorded: tuple[CloseoutAttribution, ...]
    skipped_closed_sessions: tuple[dict[str, str], ...] = ()
    unresolved: tuple[dict[str, str], ...] = ()


def resolve_filled_ideas(
    service: TradeIdeaService,
    snapshot: MarketSnapshot,
    *,
    now: datetime,
    actor_id: str = DEFAULT_EXIT_MONITOR_ACTOR_ID,
    session_calendar_resolver: SessionCalendarResolver | None = None,
    fallback_fills: Mapping[str, RecordedFill] | None = None,
) -> ExitMonitorPass:
    """Close every resolvable filled idea against ``snapshot``'s candles.

    ``fallback_fills`` supplies durable fill facts (price/quantity) by decision
    id for fills whose FILLED audit event predates fill-evidence persistence;
    audit-trail evidence always takes precedence.

    Returns the closeout attributions recorded this pass plus the ideas it
    refused to resolve because their market session is closed at ``now``
    (issue #1232): resolving an equity position against a closed session
    would time it out or mark it against stale data, so the position stays
    ``FILLED`` — loudly, with the skip on the pass record — and resolves at
    the next open against that turn's own candles. Ideas that merely cannot
    be resolved yet (no candles for the instrument, no size, no exit levels,
    no post-fill candles, or an unexpired end-of-candles) are likewise left
    ``FILLED`` for a later turn.
    """
    resolver = session_calendar_resolver or get_calendar_for_instrument
    candles_by_instrument = {
        series.symbol.casefold(): series.candles for series in snapshot.series if series.candles
    }
    recorded: list[CloseoutAttribution] = []
    skipped_closed: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    for view in service.list_views(TradeIdeaState.FILLED):
        if view.closeout_attribution is not None:
            continue
        closed_skip = _closed_session_skip(view, resolver, now)
        if closed_skip is not None:
            skipped_closed.append(closed_skip)
            continue
        attribution, unresolved_reason = _resolve_one(
            view,
            candles_by_instrument,
            snapshot=snapshot,
            now=now,
            service=service,
            actor_id=actor_id,
            fallback_fill=(fallback_fills or {}).get(view.idea.decision_id),
        )
        if attribution is not None:
            recorded.append(attribution)
        elif unresolved_reason is not None:
            unresolved.append(
                {
                    "decision_id": view.idea.decision_id,
                    "instrument": view.idea.instrument,
                    "reason": unresolved_reason,
                }
            )
    return ExitMonitorPass(
        recorded=tuple(recorded),
        skipped_closed_sessions=tuple(skipped_closed),
        unresolved=tuple(unresolved),
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
    fallback_fill: RecordedFill | None = None,
) -> tuple[CloseoutAttribution | None, str | None]:
    """Close one position, or explain why it stays open.

    Returns ``(attribution, None)`` when a closeout was recorded,
    ``(None, reason)`` when closure is owed but blocked (see
    ``ExitMonitorPass.unresolved``), and ``(None, None)`` for a position
    legitimately waiting inside its horizon.
    """
    idea = view.idea
    candles = candles_by_instrument.get(idea.instrument.casefold())
    expires_at = idea.time_horizon.expires_at
    recorded_fill = recorded_fill_from_view(view)
    if recorded_fill is None or recorded_fill.filled_at is None:
        # A FILLED view always carries a FILLED event with a timestamp; this
        # branch guards data that bypassed the service.
        return None, "no recorded fill event to anchor exit evaluation"
    if expires_at is None:
        return None, "idea has no expiry, so exit monitoring can never time it out"
    expired = now >= expires_at
    if not candles:
        return None, ("no candles for instrument in this turn's snapshot" if expired else None)
    filled_at = recorded_fill.filled_at

    fill_price, entry_price_source = _fill_price_and_source(recorded_fill, fallback_fill)
    quantity = _fill_quantity(view, recorded_fill, fallback_fill)
    if quantity is None:
        return None, "no fill or sizing quantity recorded; realized P&L cannot be computed"

    try:
        result = score_filled_trade_idea(
            idea,
            filled_at=filled_at,
            fill_price=fill_price,
            future_candles=candles,
            level_extractor=exit_plan_scoring_levels,
        )
    except ReplayScoringError as error:
        return None, f"exit levels not scoreable: {error}"

    resolution = _OUTCOME_TO_RESOLUTION.get(result.outcome)
    if resolution is None:
        # NO_FUTURE_DATA: no candles at/after the fill (and before expiry).
        return None, (
            "no post-fill candles inside the idea's horizon are available" if expired else None
        )
    # A timeout is only a real exit once the idea has expired; before that it is
    # merely the end of the candles recorded so far.
    if result.outcome is ReplayOutcome.TIMED_OUT and not expired:
        return None, None
    if result.entry_price is None or result.exit_price is None:
        return None, "scoring produced no entry/exit price"

    realized_amount = _realized_amount(
        idea.direction, quantity, result.entry_price, result.exit_price
    )
    evidence = [
        f"exit_monitor:{result.outcome.value}",
        f"entry_price={result.entry_price}",
        f"entry_price_source={entry_price_source}",
        f"fill_time={filled_at.isoformat()}",
        f"exit_price={result.exit_price}",
        f"quantity={quantity}",
        f"snapshot_as_of={snapshot.as_of.isoformat()}",
    ]
    if recorded_fill.corrupt_keys:
        # Destroyed evidence must never masquerade as a by-design estimate.
        evidence.append(f"evidence_corrupt_keys={','.join(recorded_fill.corrupt_keys)}")
    attribution = service.record_closeout_attribution(
        idea.decision_id,
        actor_id=actor_id,
        actor_type=ActorType.SYSTEM,
        resolution=resolution,
        realized_profit_loss_amount=realized_amount,
        evidence=tuple(evidence),
    )
    return attribution, None


def _fill_price_and_source(
    recorded_fill: RecordedFill,
    fallback_fill: RecordedFill | None,
) -> tuple[Decimal | None, str]:
    """Pick the entry price by evidentiary strength and name its source."""
    if recorded_fill.price is not None:
        return recorded_fill.price, "recorded_fill"
    if fallback_fill is not None and fallback_fill.price is not None:
        return fallback_fill.price, fallback_fill.source
    return None, "planned_zone_midpoint"


def _fill_quantity(
    view: TradeIdeaView,
    recorded_fill: RecordedFill,
    fallback_fill: RecordedFill | None,
) -> Decimal | None:
    if recorded_fill.quantity is not None:
        return recorded_fill.quantity
    if fallback_fill is not None and fallback_fill.quantity is not None:
        return fallback_fill.quantity
    return view.idea.sizing_recommendation.quantity


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
