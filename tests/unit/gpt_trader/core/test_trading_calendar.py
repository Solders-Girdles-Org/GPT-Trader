"""Tests for the session-aware trading calendar.

These pin the service scoped by docs/decisions/venue-neutrality-posture.md
and issue #1228: the 24x7 session must reproduce the existing UTC
``trading_day`` semantics, and XNYS must answer regular-hours questions
(holidays and early closes included) without leaking the backing package's
types.

XNYS fixtures use historical facts that never change: EDT regular hours are
13:30-20:00 UTC, 2026-07-03 is the observed Independence Day holiday
(2026-07-04 is a Saturday), and 2025-12-24 is a 13:00 ET early close.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from gpt_trader.core.instruments import InstrumentParseError
from gpt_trader.core.risk_units import trading_day
from gpt_trader.core.trading_calendar import (
    SESSION_24X7,
    SESSION_XNYS,
    AlwaysOpenCalendar,
    ExchangeBackedCalendar,
    TradingCalendar,
    advance_by_open_time,
    get_calendar_for_instrument,
    get_trading_calendar,
)

# A regular XNYS trading day: Monday 2026-07-06, 09:30-16:00 EDT.
_MONDAY_OPEN = datetime(2026, 7, 6, 13, 30, tzinfo=UTC)
_MONDAY_MIDDAY = datetime(2026, 7, 6, 18, 0, tzinfo=UTC)
_MONDAY_CLOSE = datetime(2026, 7, 6, 20, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def xnys() -> TradingCalendar:
    return ExchangeBackedCalendar(SESSION_XNYS)


class TestAlwaysOpenSession:
    def test_is_always_open(self) -> None:
        calendar = AlwaysOpenCalendar()
        assert calendar.session_id == SESSION_24X7
        assert calendar.is_open(datetime(2026, 7, 4, 3, 0, tzinfo=UTC))  # weekend
        assert calendar.is_open(datetime(2025, 12, 25, 12, 0))  # holiday, naive

    def test_session_date_matches_trading_day(self) -> None:
        calendar = AlwaysOpenCalendar()
        moments = [
            datetime(2026, 7, 3, 23, 59, 59, tzinfo=UTC),
            datetime(2026, 7, 3, 23, 30),  # naive -> UTC, not host-local
            datetime(2026, 7, 3, 20, 0, tzinfo=timezone(timedelta(hours=-5))),
        ]
        for moment in moments:
            assert calendar.session_date(moment) == trading_day(moment)

    def test_no_open_or_close_boundaries(self) -> None:
        calendar = AlwaysOpenCalendar()
        moment = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
        assert calendar.next_open(moment) is None
        assert calendar.next_close(moment) is None

    def test_open_time_matches_wall_clock(self) -> None:
        start = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
        assert advance_by_open_time(AlwaysOpenCalendar(), start, timedelta(hours=3)) == (
            start + timedelta(hours=3)
        )


class TestXnysSession:
    def test_regular_hours(self, xnys: TradingCalendar) -> None:
        assert xnys.session_id == SESSION_XNYS
        assert not xnys.is_open(datetime(2026, 7, 6, 13, 0, tzinfo=UTC))  # pre-open
        assert xnys.is_open(_MONDAY_OPEN)  # open instant counts as open
        assert xnys.is_open(_MONDAY_MIDDAY)
        assert not xnys.is_open(_MONDAY_CLOSE)  # close instant counts as closed

    def test_closed_on_weekends_and_holidays(self, xnys: TradingCalendar) -> None:
        assert not xnys.is_open(datetime(2026, 7, 4, 15, 0, tzinfo=UTC))  # Saturday
        # Independence Day 2026 falls on a Saturday; observed Friday 07-03.
        assert not xnys.is_open(datetime(2026, 7, 3, 15, 0, tzinfo=UTC))
        assert not xnys.is_open(datetime(2025, 12, 25, 15, 0, tzinfo=UTC))

    def test_early_close_half_day(self, xnys: TradingCalendar) -> None:
        # Christmas Eve 2025 closes at 13:00 EST (18:00 UTC).
        assert xnys.is_open(datetime(2025, 12, 24, 17, 0, tzinfo=UTC))
        assert not xnys.is_open(datetime(2025, 12, 24, 19, 0, tzinfo=UTC))
        early_close = xnys.next_close(datetime(2025, 12, 24, 15, 0, tzinfo=UTC))
        assert early_close == datetime(2025, 12, 24, 18, 0, tzinfo=UTC)

    def test_session_date_during_session(self, xnys: TradingCalendar) -> None:
        assert xnys.session_date(_MONDAY_MIDDAY) == date(2026, 7, 6)

    def test_session_date_when_closed_is_most_recent_session(self, xnys: TradingCalendar) -> None:
        # After Monday's close the session is still Monday's.
        assert xnys.session_date(datetime(2026, 7, 6, 22, 0, tzinfo=UTC)) == date(2026, 7, 6)
        # Saturday 07-04 reaches back across the observed holiday to Thursday.
        assert xnys.session_date(datetime(2026, 7, 4, 15, 0, tzinfo=UTC)) == date(2026, 7, 2)

    def test_next_open(self, xnys: TradingCalendar) -> None:
        saturday = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
        assert xnys.next_open(saturday) == _MONDAY_OPEN
        # Strictly after: during Monday's session the next open is Tuesday's.
        assert xnys.next_open(_MONDAY_MIDDAY) == datetime(2026, 7, 7, 13, 30, tzinfo=UTC)

    def test_next_close(self, xnys: TradingCalendar) -> None:
        assert xnys.next_close(_MONDAY_MIDDAY) == _MONDAY_CLOSE

    def test_naive_datetimes_are_treated_as_utc(self, xnys: TradingCalendar) -> None:
        # 18:00 naive means 18:00 UTC (14:00 EDT, mid-session) on any host.
        naive_midday = datetime(2026, 7, 6, 18, 0)
        assert xnys.is_open(naive_midday)
        assert xnys.session_date(naive_midday) == date(2026, 7, 6)
        assert xnys.next_close(naive_midday) == _MONDAY_CLOSE

    def test_offset_datetimes_are_normalized(self, xnys: TradingCalendar) -> None:
        # 09:31 US/Eastern (EDT, UTC-4) is one minute into the session.
        eastern_open = datetime(2026, 7, 6, 9, 31, tzinfo=timezone(timedelta(hours=-4)))
        assert xnys.is_open(eastern_open)

    def test_backing_package_types_do_not_leak(self, xnys: TradingCalendar) -> None:
        next_open = xnys.next_open(_MONDAY_MIDDAY)
        assert type(next_open) is datetime  # not a pandas.Timestamp subclass
        assert next_open is not None and next_open.tzinfo == UTC
        assert type(xnys.session_date(_MONDAY_MIDDAY)) is date

    def test_open_time_pauses_across_weekend(self, xnys: TradingCalendar) -> None:
        # Friday 2026-07-10 has 30 open minutes left; the other 30 minutes
        # resume after Monday's open.
        friday = datetime(2026, 7, 10, 19, 30, tzinfo=UTC)
        assert advance_by_open_time(xnys, friday, timedelta(hours=1)) == datetime(
            2026, 7, 13, 14, 0, tzinfo=UTC
        )

    def test_open_time_respects_early_close(self, xnys: TradingCalendar) -> None:
        christmas_eve = datetime(2025, 12, 24, 17, 30, tzinfo=UTC)
        assert advance_by_open_time(xnys, christmas_eve, timedelta(hours=1)) == datetime(
            2025, 12, 26, 15, 0, tzinfo=UTC
        )

    def test_zero_open_time_preserves_start(self, xnys: TradingCalendar) -> None:
        closed = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
        assert advance_by_open_time(xnys, closed, timedelta(0)) == closed


class TestGetTradingCalendar:
    def test_returns_supported_sessions(self) -> None:
        assert get_trading_calendar(SESSION_24X7).session_id == SESSION_24X7
        assert get_trading_calendar(SESSION_XNYS).session_id == SESSION_XNYS

    def test_instances_are_cached(self) -> None:
        assert get_trading_calendar(SESSION_XNYS) is get_trading_calendar(SESSION_XNYS)

    def test_unknown_session_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown trading session"):
            get_trading_calendar("XNAS")


class TestGetCalendarForInstrument:
    def test_crypto_pair_maps_to_24x7(self) -> None:
        assert get_calendar_for_instrument("BTC-USD").session_id == SESSION_24X7

    def test_equity_ticker_maps_to_xnys(self) -> None:
        assert get_calendar_for_instrument("AAPL").session_id == SESSION_XNYS

    def test_lookup_is_case_insensitive_like_instrument_keying(self) -> None:
        # Busy tracking and snapshot marks key on casefolded instrument
        # strings, so session resolution must accept the same spellings.
        assert get_calendar_for_instrument("btc-usd").session_id == SESSION_24X7
        assert get_calendar_for_instrument("aapl").session_id == SESSION_XNYS

    def test_unclassifiable_instrument_raises(self) -> None:
        with pytest.raises(InstrumentParseError):
            get_calendar_for_instrument("BTC-USD-PERP")
