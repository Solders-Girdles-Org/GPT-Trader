"""Tests for the canonical risk-unit primitives.

These pin the shared vocabulary from
docs/decisions/canonical-risk-limit-vocabulary.md: one percent-points <->
fraction conversion and one trading-day boundary.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from gpt_trader.core.risk_units import (
    fraction_to_pct_points,
    pct_points_to_fraction,
    same_trading_day,
    trading_day,
)


def test_pct_points_to_fraction() -> None:
    assert pct_points_to_fraction(Decimal("10")) == Decimal("0.1")
    assert pct_points_to_fraction(Decimal("100")) == Decimal("1")
    assert pct_points_to_fraction(Decimal("0")) == Decimal("0")
    assert pct_points_to_fraction(Decimal("2.5")) == Decimal("0.025")


def test_fraction_to_pct_points() -> None:
    assert fraction_to_pct_points(Decimal("0.05")) == Decimal("5")
    assert fraction_to_pct_points(Decimal("1")) == Decimal("100")
    assert fraction_to_pct_points(Decimal("0")) == Decimal("0")


def test_conversion_round_trip() -> None:
    for raw in ("0", "5", "10", "37.5", "100"):
        value = Decimal(raw)
        assert fraction_to_pct_points(pct_points_to_fraction(value)) == value


def test_trading_day_is_the_utc_calendar_date() -> None:
    aware_utc = datetime(2026, 7, 3, 23, 59, 59, tzinfo=UTC)
    assert trading_day(aware_utc) == date(2026, 7, 3)


def test_trading_day_converts_offsets_before_taking_the_date() -> None:
    # 20:00 in UTC-5 is already 01:00 the next day in UTC.
    late_evening_est = datetime(2026, 7, 3, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert trading_day(late_evening_est) == date(2026, 7, 4)


def test_trading_day_treats_naive_datetimes_as_utc() -> None:
    # Naive input must not be interpreted as host-local time; the boundary
    # cannot depend on the machine the process runs on.
    naive = datetime(2026, 7, 3, 23, 30)
    assert trading_day(naive) == date(2026, 7, 3)


def test_same_trading_day_at_the_utc_boundary() -> None:
    before_midnight = datetime(2026, 7, 3, 23, 59, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 4, 0, 0, 0, tzinfo=UTC)
    assert not same_trading_day(before_midnight, after_midnight)
    assert same_trading_day(before_midnight, before_midnight - timedelta(hours=23))


def test_same_trading_day_normalizes_mixed_offsets() -> None:
    # 21:00 UTC-4 and 01:00 UTC next day are the same instant's day in UTC.
    est_evening = datetime(2026, 7, 3, 21, 0, tzinfo=timezone(timedelta(hours=-4)))
    utc_next_morning = datetime(2026, 7, 4, 1, 0, tzinfo=UTC)
    assert same_trading_day(est_evening, utc_next_morning)


def test_same_trading_day_pairs_naive_with_aware_as_utc() -> None:
    naive = datetime(2026, 7, 4, 1, 0)
    aware = datetime(2026, 7, 3, 21, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert same_trading_day(naive, aware)
