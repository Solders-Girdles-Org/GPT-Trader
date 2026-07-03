"""Canonical unit primitives for the risk-limit vocabulary.

Implements the shared primitives from the accepted decision
``docs/decisions/canonical-risk-limit-vocabulary.md`` (Option A): one
normalization owning the percent-points <-> fraction conversion, and one
trading-day boundary shared by the approval gate's same-day realized-loss
window and the runtime daily-loss breaker.

Unit convention (``docs/naming.md``): fields suffixed ``_pct`` hold percent
points (``Decimal("10")`` means 10%); fields suffixed ``_fraction`` hold unit
fractions (``Decimal("0.10")`` means 10%). These converters are the only
sanctioned crossing between the two.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

_ONE_HUNDRED = Decimal("100")


def pct_points_to_fraction(value: Decimal) -> Decimal:
    """Convert percent points (``Decimal("10")`` = 10%) to a unit fraction."""
    return value / _ONE_HUNDRED


def fraction_to_pct_points(value: Decimal) -> Decimal:
    """Convert a unit fraction (``Decimal("0.10")`` = 10%) to percent points."""
    return value * _ONE_HUNDRED


def trading_day(moment: datetime) -> date:
    """Return the trading day containing ``moment``.

    The trading day is the UTC calendar date. Naive datetimes are treated as
    UTC rather than host-local time, so the boundary never depends on the
    machine the process happens to run on.
    """
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(UTC).date()


def same_trading_day(first: datetime, second: datetime) -> bool:
    """Return True when both datetimes fall on the same trading day."""
    return trading_day(first) == trading_day(second)


__all__ = [
    "fraction_to_pct_points",
    "pct_points_to_fraction",
    "same_trading_day",
    "trading_day",
]
