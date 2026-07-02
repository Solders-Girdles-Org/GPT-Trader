import threading
from dataclasses import dataclass
from datetime import datetime

from gpt_trader.core.protocols import TickerFreshnessProvider
from gpt_trader.utilities.datetime_helpers import normalize_to_utc
from gpt_trader.utilities.time_provider import Clock, SystemClock


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    ts: datetime


class TickerCache:
    """Thread-safe cache for ticker data.

    WebSocket threads update this cache while trading threads read from it.
    All operations are protected by an RLock for safe concurrent access.
    """

    def __init__(self, ttl_seconds: int = 5, *, clock: Clock | None = None):
        self.ttl = ttl_seconds
        self._lock = threading.RLock()
        self._cache: dict[str, Ticker] = {}
        self._clock = clock or SystemClock()
        self._last_ticker_timestamp: datetime | None = None

    def update(self, ticker: Ticker) -> None:
        """Update ticker data (called by WebSocket thread)."""
        with self._lock:
            if ticker.ts.tzinfo is None:
                ticker.ts = normalize_to_utc(ticker.ts)
            self._cache[ticker.symbol] = ticker
            if self._last_ticker_timestamp is None or ticker.ts > self._last_ticker_timestamp:
                self._last_ticker_timestamp = ticker.ts

    def get(self, symbol: str) -> Ticker | None:
        """Get ticker data (called by trading thread)."""
        with self._lock:
            return self._cache.get(symbol)

    def has_any(self) -> bool:
        """Return True if any ticker data has been populated."""
        with self._lock:
            return bool(self._cache)

    def get_last_ticker_timestamp(self) -> datetime | None:
        """Return the most recent ticker timestamp observed."""
        with self._lock:
            return self._last_ticker_timestamp

    def is_stale(self, symbol: str) -> bool:
        """Check if ticker data is stale (thread-safe)."""
        with self._lock:
            ticker = self._cache.get(symbol)
            if not ticker:
                return True
            return (self._clock.now() - ticker.ts).total_seconds() >= self.ttl


class CoinbaseTickerService:
    def __init__(
        self,
        symbols: list[str] | None = None,
        *,
        ticker_cache: TickerCache | None = None,
    ):
        self._symbols = symbols or []
        self._running = False
        self._thread: threading.Thread | None = None
        self._ticker_cache = ticker_cache or TickerCache()

    def start(self) -> None:
        self._running = True
        # Mock thread start
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def set_symbols(self, symbols: list[str]) -> None:
        self._symbols = symbols

    def get_ticker_freshness_provider(self) -> TickerFreshnessProvider | None:
        # Only advertise a freshness provider once something is actually populating the cache.
        # Otherwise, environments that wire this stub service without a live updater will
        # report every symbol as stale and fail /health.
        if not self._ticker_cache.has_any():
            return None
        return self._ticker_cache

    def get_last_ticker_timestamp(self) -> datetime | None:
        """Return the most recent ticker timestamp observed."""
        return self._ticker_cache.get_last_ticker_timestamp()

    def is_stale(self, symbol: str) -> bool:
        return self._ticker_cache.is_stale(symbol)

    def _run(self) -> None:
        pass

    def get_mark(self, symbol: str) -> float | None:
        # Assuming 'last' price is the mark for now, or mocked
        # Since this is mostly a stub/mock service in this context
        return None


MarketDataService = CoinbaseTickerService
