"""Keyless daily equity candles from Stooq's public CSV endpoint.

Recorder-owned equities market data (issue #1229): the second concrete
``HistoricalCandleSource`` beside the Coinbase fetcher, feeding the
venue-neutral trade-idea spine with daily stock bars for replay and paper
evidence. Read-only public data; no credentials, no account or order paths.

Granularity: ``ONE_DAY`` only. Stooq's keyless CSV endpoint serves daily
session bars; intraday equity data requires a keyed vendor and is a recorded
follow-up decision. Intraday requests are rejected loudly — this source never
fabricates bars it does not have.

Timestamp semantics: a daily equity bar summarizes one exchange session, not
a UTC calendar day. Each candle is therefore timestamped at the US session
close (16:00 America/New_York, DST-aware) converted to UTC, so a bar's
timestamp never precedes the trading it summarizes and point-in-time
snapshot bounds hold. The snapshot builder counts a candle as completed once
``ts + ONE_DAY <= as_of``, so under this convention a session's bar becomes
snapshot-eligible one calendar day after its close — deliberately
conservative: it may lag one session but can never leak a bar from a session
still in progress.

Symbols stay venue-neutral: callers pass plain tickers (``AAPL``, ``SPY``);
the Stooq-specific ``<ticker>.us`` form is an internal mapping here and
never appears on the spine.
"""

from __future__ import annotations

import asyncio
import csv
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from io import StringIO
from zoneinfo import ZoneInfo

from gpt_trader.core import Candle
from gpt_trader.errors import ValidationError

DEFAULT_STOOQ_BASE_URL = "https://stooq.com"
DAILY_GRANULARITY = "ONE_DAY"

_US_EQUITY_SESSION_CLOSE = time(16, 0)
_US_EQUITY_SESSION_TIMEZONE = ZoneInfo("America/New_York")
_EXPECTED_CSV_HEADER = ("Date", "Open", "High", "Low", "Close", "Volume")


class EquitiesCandleFeedError(ValidationError):
    """Raised when the Stooq daily-candle feed cannot serve a request."""


class StooqDailyCandleFetcher:
    """Fetch completed daily equity candles from Stooq's keyless CSV endpoint.

    Implements the recorder's ``HistoricalCandleSource`` protocol for plain
    equity tickers at ``ONE_DAY`` granularity. One unauthenticated HTTP GET
    per call; no connection state is kept between calls.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_STOOQ_BASE_URL,
        http_get_text: Callable[[str], str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Initialize the fetcher.

        Args:
            base_url: Stooq endpoint root (overridable for tests).
            http_get_text: Transport returning the response body for a URL;
                defaults to a ``requests`` GET. Injected so tests never touch
                the network.
            timeout_seconds: Timeout for the default transport.
        """
        self._base_url = base_url.rstrip("/")
        self._http_get_text = http_get_text or self._default_http_get_text
        self._timeout_seconds = timeout_seconds

    async def fetch_candles(
        self,
        *,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Return daily candles for ``symbol`` with session-close timestamps in ``[start, end)``."""
        if granularity != DAILY_GRANULARITY:
            raise EquitiesCandleFeedError(
                f"Stooq equities source serves {DAILY_GRANULARITY} candles only; "
                f"got '{granularity}'. Intraday equity bars require a keyed vendor "
                "(recorded follow-up decision) — refusing rather than fabricating bars.",
                field="granularity",
            )
        stooq_symbol = _stooq_symbol(symbol)
        url = (
            f"{self._base_url}/q/d/l/?s={stooq_symbol}&i=d"
            f"&d1={start.astimezone(UTC):%Y%m%d}&d2={end.astimezone(UTC):%Y%m%d}"
        )
        body = await asyncio.to_thread(self._http_get_text, url)
        candles = _parse_daily_csv(body, symbol=symbol, stooq_symbol=stooq_symbol)
        return [candle for candle in candles if start <= candle.ts < end]

    def _default_http_get_text(self, url: str) -> str:
        import requests

        # Honest client identification; Stooq 404s the generic library UA.
        response = requests.get(
            url,
            timeout=self._timeout_seconds,
            headers={"User-Agent": "gpt-trader-recorder/1.0 (paper-trading research; read-only)"},
        )
        response.raise_for_status()
        return response.text


def _stooq_symbol(symbol: str) -> str:
    """Map a venue-neutral ticker (``AAPL``) to Stooq's US form (``aapl.us``)."""
    normalized = symbol.strip().lower()
    if not normalized or "." in normalized:
        raise EquitiesCandleFeedError(
            f"Equity symbols must be plain tickers like AAPL or SPY; got '{symbol}'",
            field="symbol",
        )
    return f"{normalized}.us"


def _parse_daily_csv(body: str, *, symbol: str, stooq_symbol: str) -> list[Candle]:
    stripped = body.strip()
    if stripped.startswith("<") or stripped.lower() in {"access denied", "odmowa dostępu"}:
        raise EquitiesCandleFeedError(
            f"Stooq refused programmatic access for '{symbol}' (requested as "
            f"'{stooq_symbol}'): the endpoint answered with a bot-detection "
            f"challenge or an access denial instead of CSV. Retry later or "
            f"revisit the vendor choice; this source never fabricates bars.",
            field="candles",
        )
    rows = [row for row in csv.reader(StringIO(body)) if row]
    header_ok = bool(rows) and tuple(cell.strip() for cell in rows[0]) == _EXPECTED_CSV_HEADER
    if not header_ok or len(rows) < 2:
        raise EquitiesCandleFeedError(
            f"Stooq returned no daily candles for '{symbol}' (requested as "
            f"'{stooq_symbol}'): unknown symbol, empty range, or unexpected "
            f"response format",
            field="candles",
        )
    candles = [_parse_daily_row(row, symbol=symbol) for row in rows[1:]]
    candles.sort(key=lambda candle: candle.ts)
    return candles


def _parse_daily_row(row: list[str], *, symbol: str) -> Candle:
    if len(row) != len(_EXPECTED_CSV_HEADER):
        raise EquitiesCandleFeedError(
            f"Malformed Stooq candle row for '{symbol}': {row!r}",
            field="candles",
        )
    try:
        session_date = date.fromisoformat(row[0].strip())
        open_, high, low, close, volume = (Decimal(cell.strip()) for cell in row[1:6])
    except (ValueError, InvalidOperation) as error:
        raise EquitiesCandleFeedError(
            f"Malformed Stooq candle row for '{symbol}': {row!r}",
            field="candles",
        ) from error
    return Candle(
        ts=_session_close_utc(session_date),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _session_close_utc(session_date: date) -> datetime:
    """Timestamp a session's daily bar at the US market close, expressed in UTC."""
    close_local = datetime.combine(
        session_date,
        _US_EQUITY_SESSION_CLOSE,
        tzinfo=_US_EQUITY_SESSION_TIMEZONE,
    )
    return close_local.astimezone(UTC)
