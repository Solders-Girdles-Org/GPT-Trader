"""Session-aware trading calendar for market-hours questions.

Implements the trading-calendar service scoped in
``docs/decisions/venue-neutrality-posture.md`` (the first leak-watch item):
for a session identifier, answer whether the market is open at instant T,
which session date contains T, and when the next open/close occurs.

Two sessions are supported:

- ``24x7`` — crypto. Always open; the session date is the UTC calendar
  date, matching ``trading_day`` in ``risk_units.py`` (which stays as-is;
  this module is the session-aware counterpart).
- ``XNYS`` — US equities, regular hours only, backed by the
  ``exchange_calendars`` package. The dependency stays behind this module;
  callers only ever see stdlib ``datetime``/``date`` values.

Timezone rule follows the repo convention (``risk_units.py``): naive
datetimes are treated as UTC, never host-local time.

Consumers (ratchet day boundary, snapshot staleness, scheduler windows) are
NOT wired here — that migration is venue phase P1-E.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

from gpt_trader.core.instruments import AssetClass, Instrument

if TYPE_CHECKING:
    import pandas

SESSION_24X7 = "24x7"
SESSION_XNYS = "XNYS"

SUPPORTED_SESSIONS = (SESSION_24X7, SESSION_XNYS)

# Phase-1 venue assumption (issue #1224): every equity instrument trades US
# regular hours, every crypto instrument trades around the clock. When a
# second equity venue arrives this becomes instrument metadata, not a map.
SESSION_ID_BY_ASSET_CLASS: dict[AssetClass, str] = {
    AssetClass.CRYPTO: SESSION_24X7,
    AssetClass.EQUITY: SESSION_XNYS,
}


def _as_utc(moment: datetime) -> datetime:
    """Normalize ``moment`` to aware UTC; naive datetimes are treated as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


class TradingCalendar(Protocol):
    """Market-hours questions for one trading session.

    ``next_open``/``next_close`` return the earliest boundary strictly after
    ``moment`` (asking at the exact open instant yields the following
    session's open), or ``None`` when the session has no such boundary
    (a 24/7 market never opens or closes).
    """

    @property
    def session_id(self) -> str:
        """Identifier of the session this calendar answers for."""
        ...

    def is_open(self, moment: datetime) -> bool:
        """Return True when the market is open at ``moment``.

        The open instant counts as open; the close instant counts as closed.
        """
        ...

    def session_date(self, moment: datetime) -> date:
        """Return the date of the session containing ``moment``.

        When the market is closed at ``moment``, this is the most recent
        session at or before ``moment`` — activity between sessions belongs
        to the session that just ended.
        """
        ...

    def next_open(self, moment: datetime) -> datetime | None:
        """Return the earliest open strictly after ``moment``, if any."""
        ...

    def next_close(self, moment: datetime) -> datetime | None:
        """Return the earliest close strictly after ``moment``, if any."""
        ...


SessionCalendarResolver = Callable[[str], TradingCalendar]
"""Maps an instrument string to the session calendar it trades on.

Injected wherever session decisions are made (cycle runner, paper executor,
exit monitor) so deterministic tests can pin session state; the default is
``get_calendar_for_instrument``. May raise ``InstrumentParseError`` for
unclassifiable instruments — callers must turn that into a loud skip or a
typed refusal, never a silent pass.
"""


class AlwaysOpenCalendar:
    """The ``24x7`` session (crypto): always open, session date = UTC date."""

    @property
    def session_id(self) -> str:
        return SESSION_24X7

    def is_open(self, moment: datetime) -> bool:
        return True

    def session_date(self, moment: datetime) -> date:
        return _as_utc(moment).date()

    def next_open(self, moment: datetime) -> datetime | None:
        return None

    def next_close(self, moment: datetime) -> datetime | None:
        return None


class ExchangeBackedCalendar:
    """A regular-hours exchange session backed by ``exchange_calendars``.

    Wraps the third-party calendar so the dependency does not leak: inputs
    and outputs are stdlib ``datetime``/``date``. Queries outside the backing
    calendar's bounds (roughly twenty years back to one year ahead) raise
    ``ValueError`` from the underlying package.
    """

    def __init__(self, session_id: str) -> None:
        # Deliberate lazy import: gpt_trader.core stays stdlib-only at import
        # time, and the dependency remains an implementation detail.
        import exchange_calendars

        self._session_id = session_id
        self._calendar = exchange_calendars.get_calendar(session_id)

    @property
    def session_id(self) -> str:
        return self._session_id

    def is_open(self, moment: datetime) -> bool:
        return bool(self._calendar.is_open_at_time(self._as_timestamp(moment)))

    def session_date(self, moment: datetime) -> date:
        session = self._calendar.minute_to_session(
            self._as_timestamp(moment).floor("min"), direction="previous"
        )
        return date(session.year, session.month, session.day)

    def next_open(self, moment: datetime) -> datetime | None:
        return self._as_datetime(self._calendar.next_open(self._as_timestamp(moment)))

    def next_close(self, moment: datetime) -> datetime | None:
        return self._as_datetime(self._calendar.next_close(self._as_timestamp(moment)))

    @staticmethod
    def _as_timestamp(moment: datetime) -> pandas.Timestamp:
        import pandas

        return pandas.Timestamp(_as_utc(moment))

    @staticmethod
    def _as_datetime(timestamp: pandas.Timestamp) -> datetime:
        # pd.Timestamp subclasses datetime; hand callers the plain stdlib type.
        return timestamp.to_pydatetime().astimezone(UTC)


@lru_cache(maxsize=None)
def get_trading_calendar(session_id: str) -> TradingCalendar:
    """Return the calendar for ``session_id`` (one of ``SUPPORTED_SESSIONS``).

    Instances are cached: building an exchange-backed calendar materializes
    years of schedule, so repeated lookups share one instance.
    """
    if session_id == SESSION_24X7:
        return AlwaysOpenCalendar()
    if session_id == SESSION_XNYS:
        return ExchangeBackedCalendar(SESSION_XNYS)
    supported = ", ".join(SUPPORTED_SESSIONS)
    raise ValueError(f"Unknown trading session {session_id!r} (supported: {supported})")


def get_calendar_for_instrument(instrument: str) -> TradingCalendar:
    """Return the session calendar an instrument string trades on.

    Classification comes from the structured taxonomy
    (:meth:`~gpt_trader.core.instruments.Instrument.parse`), never ad-hoc
    string sniffing. The lookup is case-insensitive to match the repo's
    casefolded instrument keying (busy tracking, snapshot marks); the
    instrument string itself is never rewritten. Raises
    :class:`~gpt_trader.core.instruments.InstrumentParseError` when the
    string does not classify — callers decide whether that is a loud skip
    or a hard failure.
    """
    asset_class = Instrument.parse(instrument.upper()).asset_class
    return get_trading_calendar(SESSION_ID_BY_ASSET_CLASS[asset_class])


__all__ = [
    "SESSION_24X7",
    "SESSION_ID_BY_ASSET_CLASS",
    "SESSION_XNYS",
    "SUPPORTED_SESSIONS",
    "AlwaysOpenCalendar",
    "ExchangeBackedCalendar",
    "SessionCalendarResolver",
    "TradingCalendar",
    "get_calendar_for_instrument",
    "get_trading_calendar",
]
