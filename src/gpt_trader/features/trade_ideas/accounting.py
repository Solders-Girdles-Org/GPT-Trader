"""Read-only paper accounting derived from the budget log and closeouts.

Paper equity, the high-water mark, and drawdown-from-peak are the accountant
view named in docs/decisions/adopt-operator-web-console.md. Everything here is
a pure computation over two durable artifacts — budget-log entries and
closeout attribution records — so the numbers are reproducible from evidence
alone and nothing mutates storage.

The equity ledger folds two kinds of events in timestamp order:

- An *attestation* is a budget-log entry whose ``account_equity`` differs from
  the previous entry's value. It sets the equity level outright — the operator
  measured the account, superseding anything derived. A lever change that
  carries the same equity forward is not an attestation and does not reset
  the ledger.
- A closeout with a realized profit/loss amount adjusts the level. It folds at
  the time the trade actually resolved, as best the artifacts can say: the
  caller maps ``terminal_event_id`` to the audit timestamp for terminal events
  that *are* the resolution (expiry / cancellation / rejection), so a
  late-entered attribution sorts before an attestation that already reflects
  it. A ``FILLED`` audit event is the entry fill, not the later market close,
  so filled trades deliberately keep their attribution timestamp — record
  attributions promptly, and before re-attesting equity, to keep the ledger
  exact. Closeouts whose amount is unavailable are counted but cannot move
  the ledger, and closeouts that resolved before the first attestation
  contribute to the realized total only — there is no level for them to
  adjust yet.

The high-water mark is the peak of the whole ledger: a re-attestation moves
the level but never erases the historical peak, so drawdown-from-peak stays
honest across resets.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from gpt_trader.features.trade_ideas.budget import BudgetLogEntry
from gpt_trader.features.trade_ideas.closeout import CloseoutAttribution


@dataclass(frozen=True, slots=True)
class EquityAttestation:
    """One operator-attested equity level from the budget log."""

    timestamp: datetime
    actor_id: str
    equity: Decimal
    budget_version: int


@dataclass(frozen=True, slots=True)
class EquityLedgerPoint:
    """One resolved equity level in the paper ledger, with its running peak.

    Points exist only once an attested basis exists: attestations and the
    closeouts that adjust an attested level each produce a point. Drawdown is
    measured from the historical peak of the whole ledger (never reset by a
    re-attestation). ``drawdown_percent`` is ``None`` only when the peak is
    non-positive (percent-of-peak is undefined).
    """

    timestamp: datetime
    equity: Decimal
    high_water_mark: Decimal
    drawdown_amount: Decimal
    drawdown_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class PaperAccountingSummary:
    """Paper equity ledger totals; ``None`` means no attested basis exists."""

    attestation: EquityAttestation | None
    current_equity: Decimal | None
    high_water_mark: Decimal | None
    drawdown_amount: Decimal | None
    drawdown_percent: Decimal | None
    realized_profit_loss_total: Decimal
    realized_profit_loss_since_attestation: Decimal | None
    closeout_count: int
    closeout_amount_unavailable_count: int


def _attestations(entries: Iterable[BudgetLogEntry]) -> list[EquityAttestation]:
    attestations: list[EquityAttestation] = []
    previous_equity: Decimal | None = None
    for entry in entries:
        equity = entry.budget.account_equity
        if equity is not None and equity != previous_equity:
            attestations.append(
                EquityAttestation(
                    timestamp=entry.timestamp,
                    actor_id=entry.actor_id,
                    equity=equity,
                    budget_version=entry.budget.version,
                )
            )
        previous_equity = equity
    return attestations


def _merged_ledger_events(
    budget_entries: Iterable[BudgetLogEntry],
    closeouts: Iterable[CloseoutAttribution],
    terminal_times: Mapping[str, datetime] | None,
) -> tuple[
    list[EquityAttestation], list[tuple[datetime, int, EquityAttestation | CloseoutAttribution]]
]:
    attestations = _attestations(budget_entries)
    resolved_terminal_times = terminal_times or {}

    def _closeout_time(record: CloseoutAttribution) -> datetime:
        return resolved_terminal_times.get(record.terminal_event_id, record.timestamp)

    # Merge in timestamp order; on a tie the attestation applies first, so a
    # closeout stamped at the same instant adjusts the freshly attested level.
    events: list[tuple[datetime, int, EquityAttestation | CloseoutAttribution]] = [
        (attestation.timestamp, 0, attestation) for attestation in attestations
    ] + [(_closeout_time(record), 1, record) for record in closeouts]
    events.sort(key=lambda event: (event[0], event[1]))
    return attestations, events


def equity_ledger(
    budget_entries: Iterable[BudgetLogEntry],
    closeouts: Iterable[CloseoutAttribution],
    *,
    terminal_times: Mapping[str, datetime] | None = None,
) -> list[EquityLedgerPoint]:
    """Fold the trail into the resolved equity/peak series, one point per move.

    Same fold as ``compute_paper_accounting`` (same merge order, same
    pre-attestation and amount-unavailable rules) but keeps every intermediate
    level so windowed questions — e.g. max drawdown-from-peak over the
    scorecard's observation window — read from the trail, not from any stored
    monitor state.
    """
    _attestation_list, events = _merged_ledger_events(budget_entries, closeouts, terminal_times)
    points: list[EquityLedgerPoint] = []
    equity: Decimal | None = None
    high_water_mark: Decimal | None = None

    def _point(timestamp: datetime) -> EquityLedgerPoint:
        assert equity is not None and high_water_mark is not None
        drawdown_amount = max(high_water_mark - equity, Decimal("0"))
        drawdown_percent = (
            drawdown_amount / high_water_mark * Decimal("100") if high_water_mark > 0 else None
        )
        return EquityLedgerPoint(
            timestamp=timestamp,
            equity=equity,
            high_water_mark=high_water_mark,
            drawdown_amount=drawdown_amount,
            drawdown_percent=drawdown_percent,
        )

    for timestamp, _order, event in events:
        if isinstance(event, EquityAttestation):
            equity = event.equity
            high_water_mark = equity if high_water_mark is None else max(high_water_mark, equity)
            points.append(_point(timestamp))
            continue
        amount = event.realized_profit_loss_amount
        if amount is None or equity is None:
            continue
        equity += amount
        if high_water_mark is not None:
            high_water_mark = max(high_water_mark, equity)
        points.append(_point(timestamp))
    return points


def max_drawdown_from_peak_percent(
    points: Iterable[EquityLedgerPoint],
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> Decimal | None:
    """Worst drawdown-from-peak percent among ledger points inside the window.

    ``None`` bounds are open. Returns ``None`` when no point in the window has
    a measurable percent — no attested basis, or a non-positive peak.
    """
    worst: Decimal | None = None
    for point in points:
        if window_start is not None and point.timestamp < window_start:
            continue
        if window_end is not None and point.timestamp > window_end:
            continue
        if point.drawdown_percent is None:
            continue
        if worst is None or point.drawdown_percent > worst:
            worst = point.drawdown_percent
    return worst


def compute_paper_accounting(
    budget_entries: Iterable[BudgetLogEntry],
    closeouts: Iterable[CloseoutAttribution],
    *,
    terminal_times: Mapping[str, datetime] | None = None,
) -> PaperAccountingSummary:
    """Fold attestations and closeout amounts into the paper equity ledger.

    ``terminal_times`` maps a closeout's ``terminal_event_id`` to the audit
    timestamp of that terminal event; a closeout without a mapping falls back
    to its attribution timestamp.
    """
    attestations, events = _merged_ledger_events(budget_entries, closeouts, terminal_times)

    equity: Decimal | None = None
    high_water_mark: Decimal | None = None
    realized_total = Decimal("0")
    realized_since_attestation: Decimal | None = None
    closeout_count = 0
    unavailable_count = 0

    for _timestamp, _order, event in events:
        if isinstance(event, EquityAttestation):
            equity = event.equity
            high_water_mark = equity if high_water_mark is None else max(high_water_mark, equity)
            realized_since_attestation = Decimal("0")
            continue
        closeout_count += 1
        amount = event.realized_profit_loss_amount
        if amount is None:
            unavailable_count += 1
            continue
        realized_total += amount
        if equity is None:
            continue
        equity += amount
        if realized_since_attestation is not None:
            realized_since_attestation += amount
        if high_water_mark is not None:
            high_water_mark = max(high_water_mark, equity)

    drawdown_amount: Decimal | None = None
    drawdown_percent: Decimal | None = None
    if equity is not None and high_water_mark is not None and high_water_mark > 0:
        drawdown_amount = max(high_water_mark - equity, Decimal("0"))
        drawdown_percent = drawdown_amount / high_water_mark * Decimal("100")

    return PaperAccountingSummary(
        attestation=attestations[-1] if attestations else None,
        current_equity=equity,
        high_water_mark=high_water_mark,
        drawdown_amount=drawdown_amount,
        drawdown_percent=drawdown_percent,
        realized_profit_loss_total=realized_total,
        realized_profit_loss_since_attestation=realized_since_attestation,
        closeout_count=closeout_count,
        closeout_amount_unavailable_count=unavailable_count,
    )
