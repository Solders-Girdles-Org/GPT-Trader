"""Daily equity candles for the recorder's venue-neutral spine.

Recorder-owned equities market data (issues #1229/#1238): concrete
``HistoricalCandleSource`` implementations beside the Coinbase fetcher,
feeding the trade-idea spine with daily stock bars for replay and paper
evidence. Read-only market data; no account or order paths.

Two vendors behind the same protocol:

- ``AlpacaDailyCandleFetcher`` — the vendor of record (#1238): official,
  documented Alpaca Market Data API, free-tier IEX feed, keyed with
  market-data-only credentials that carry no order authority.
- ``StooqDailyCandleFetcher`` — the original keyless CSV source, dormant
  since stooq.com fronted the endpoint with a bot gate (#1238); kept as a
  second source because spine source labels are per-vendor by design.

Granularity: ``ONE_DAY`` only for both vendors; intraday equity data is a
recorded follow-up decision. Intraday requests are rejected loudly — these
sources never fabricate bars they do not have.

Timestamp semantics (shared by both vendors): a daily equity bar summarizes
one exchange session, not a UTC calendar day. Each candle is therefore timestamped at the US session
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
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

from gpt_trader.core import Candle
from gpt_trader.errors import ValidationError

DEFAULT_ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
DEFAULT_STOOQ_BASE_URL = "https://stooq.com"
DAILY_GRANULARITY = "ONE_DAY"

ALPACA_KEY_ID_ENV = "ALPACA_API_KEY_ID"
ALPACA_SECRET_KEY_ENV = "ALPACA_API_SECRET_KEY"

_US_EQUITY_SESSION_CLOSE = time(16, 0)
_US_EQUITY_SESSION_TIMEZONE = ZoneInfo("America/New_York")
_EXPECTED_CSV_HEADER = ("Date", "Open", "High", "Low", "Close", "Volume")


class EquitiesCandleFeedError(ValidationError):
    """Raised when a daily equity-candle feed cannot serve a request."""


@dataclass(frozen=True)
class AlpacaDataCredentials:
    """Alpaca market-data API keys — read-only, no account or order authority."""

    key_id: str
    secret_key: str

    @classmethod
    def from_env(cls) -> AlpacaDataCredentials:
        key_id = os.getenv(ALPACA_KEY_ID_ENV, "").strip()
        secret_key = os.getenv(ALPACA_SECRET_KEY_ENV, "").strip()
        if not key_id or not secret_key:
            raise EquitiesCandleFeedError(
                "Alpaca credentials not found. Set "
                f"{ALPACA_KEY_ID_ENV} and {ALPACA_SECRET_KEY_ENV} to free-tier "
                "market-data API keys (read-only; no order authority).",
                field="credentials",
            )
        return cls(key_id=key_id, secret_key=secret_key)


class AlpacaDailyCandleFetcher:
    """Fetch completed daily equity candles from Alpaca's Market Data API.

    Implements the recorder's ``HistoricalCandleSource`` protocol for plain
    equity tickers at ``ONE_DAY`` granularity: free-tier IEX feed,
    split-adjusted bars, one authenticated HTTP GET per call with no
    connection state kept between calls. The keys are market-data-only and
    carry no order authority.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_ALPACA_DATA_BASE_URL,
        http_get_text: Callable[[str], str] | None = None,
        credentials: AlpacaDataCredentials | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Initialize the fetcher.

        Args:
            base_url: Alpaca data endpoint root (overridable for tests).
            http_get_text: Transport returning the response body for a URL;
                defaults to an authenticated ``requests`` GET. Injected so
                tests never touch the network or need credentials.
            credentials: Keys for the default transport; loaded from the
                environment when omitted. Ignored when ``http_get_text``
                is injected.
            timeout_seconds: Timeout for the default transport.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        if http_get_text is None:
            http_get_text = self._build_default_transport(
                credentials or AlpacaDataCredentials.from_env()
            )
        self._http_get_text = http_get_text

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
                f"Alpaca equities source serves {DAILY_GRANULARITY} candles only; "
                f"got '{granularity}'. Intraday equity bars are a recorded "
                "follow-up decision — refusing rather than fabricating bars.",
                field="granularity",
            )
        ticker = _plain_ticker(symbol)
        url = (
            f"{self._base_url}/v2/stocks/{ticker}/bars"
            "?timeframe=1Day&adjustment=split&feed=iex&limit=10000&sort=asc"
            f"&start={start.astimezone(UTC):%Y-%m-%d}&end={end.astimezone(UTC):%Y-%m-%d}"
        )
        body = await asyncio.to_thread(self._http_get_text, url)
        candles = _parse_alpaca_bars(body, symbol=ticker)
        return [candle for candle in candles if start <= candle.ts < end]

    def _build_default_transport(self, credentials: AlpacaDataCredentials) -> Callable[[str], str]:
        def http_get_text(url: str) -> str:
            import requests

            response = requests.get(
                url,
                timeout=self._timeout_seconds,
                headers={
                    "APCA-API-KEY-ID": credentials.key_id,
                    "APCA-API-SECRET-KEY": credentials.secret_key,
                    "User-Agent": ("gpt-trader-recorder/1.0 (paper-trading research; read-only)"),
                },
            )
            response.raise_for_status()
            return response.text

        return http_get_text


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


def _plain_ticker(symbol: str) -> str:
    """Validate a venue-neutral plain ticker (``AAPL``) and normalize to uppercase."""
    normalized = symbol.strip().upper()
    if not normalized or "." in normalized:
        raise EquitiesCandleFeedError(
            f"Equity symbols must be plain tickers like AAPL or SPY; got '{symbol}'",
            field="symbol",
        )
    return normalized


def _stooq_symbol(symbol: str) -> str:
    """Map a venue-neutral ticker (``AAPL``) to Stooq's US form (``aapl.us``)."""
    return f"{_plain_ticker(symbol).lower()}.us"


def _parse_alpaca_bars(body: str, *, symbol: str) -> list[Candle]:
    try:
        payload = json.loads(body, parse_float=Decimal)
    except json.JSONDecodeError as error:
        raise EquitiesCandleFeedError(
            f"Alpaca returned a non-JSON response for '{symbol}': {body[:200]!r}",
            field="candles",
        ) from error
    if not isinstance(payload, dict) or "bars" not in payload:
        detail = payload.get("message") if isinstance(payload, dict) else None
        raise EquitiesCandleFeedError(
            f"Alpaca did not return bars for '{symbol}': "
            f"{detail if detail else f'unexpected payload {body[:200]!r}'}",
            field="candles",
        )
    if payload.get("next_page_token"):
        raise EquitiesCandleFeedError(
            f"Alpaca truncated the daily-candle response for '{symbol}' "
            "(next_page_token present). Narrow the requested window; this "
            "source never silently drops bars.",
            field="candles",
        )
    bars = payload["bars"] or []
    if not bars:
        raise EquitiesCandleFeedError(
            f"Alpaca returned no daily candles for '{symbol}': unknown symbol " "or empty range",
            field="candles",
        )
    candles = [_parse_alpaca_bar(bar, symbol=symbol) for bar in bars]
    candles.sort(key=lambda candle: candle.ts)
    return candles


def _parse_alpaca_bar(bar: Any, *, symbol: str) -> Candle:
    try:
        opened = datetime.fromisoformat(str(bar["t"]).replace("Z", "+00:00"))
        if opened.tzinfo is None:
            raise ValueError("bar timestamp missing timezone")
        open_, high, low, close, volume = (Decimal(bar[key]) for key in "ohlcv")
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise EquitiesCandleFeedError(
            f"Malformed Alpaca candle bar for '{symbol}': {bar!r}",
            field="candles",
        ) from error
    # Alpaca stamps daily bars at the session open; the spine convention is
    # the session close (module docstring), so re-derive it from the session date.
    session_date = opened.astimezone(_US_EQUITY_SESSION_TIMEZONE).date()
    return Candle(
        ts=_session_close_utc(session_date),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


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
