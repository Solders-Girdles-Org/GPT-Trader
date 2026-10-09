# ruff: noqa: F403
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    load_dotenv = None  # type: ignore

import gpt_trader.features.brokerages.coinbase.client.websocket_mixin as websocket_mixin_module
from gpt_trader.app.config import BotConfig
from gpt_trader.features.brokerages.coinbase.market_data_service import MarketDataService
from gpt_trader.features.brokerages.coinbase.models import APIConfig
from gpt_trader.features.brokerages.coinbase.utilities import ProductCatalog
from tests.unit.gpt_trader.features.brokerages.coinbase.message_factories import *
from tests.unit.gpt_trader.features.brokerages.coinbase.transport_fixtures import *

# Autouse fixtures in this module patch time.sleep and COINBASE_FAST_RETRY for faster retries.
from tests.unit.gpt_trader.features.brokerages.coinbase.websocket_fixtures import *


@dataclass(frozen=True)
class CDPCredentials:
    api_key: str
    private_key: str
    skip_reason: str | None = None


@pytest.fixture(autouse=True)
def fast_retry_sleep(fake_clock, monkeypatch):
    """Auto-use deterministic clock so retry loops advance instantly."""
    monkeypatch.setattr("time.sleep", fake_clock.sleep)
    return fake_clock


@pytest.fixture(autouse=True)
def fast_retry_env(monkeypatch, request):
    """Ensure Coinbase client retries run with zero delay for faster tests."""
    if request.node.get_closest_marker("e2e"):
        monkeypatch.delenv("COINBASE_FAST_RETRY", raising=False)
        yield
        return
    monkeypatch.setenv("COINBASE_FAST_RETRY", "1")
    yield


@pytest.fixture
def coinbase_cdp_credentials() -> CDPCredentials:
    """Provide Coinbase CDP API credentials or skip when unavailable."""

    if load_dotenv is not None:
        load_dotenv()

    api_key = os.getenv("COINBASE_PROD_CDP_API_KEY")
    private_key = os.getenv("COINBASE_PROD_CDP_PRIVATE_KEY")

    if not api_key or not private_key:
        skip_reason = "COINBASE_PROD_CDP_* credentials not set"
        pytest.skip(skip_reason)

    return CDPCredentials(api_key=api_key, private_key=private_key)


@pytest.fixture
def mock_websocket_class(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_ws_class = MagicMock()
    monkeypatch.setattr(websocket_mixin_module, "CoinbaseWebSocket", mock_ws_class)
    return mock_ws_class


@pytest.fixture
def fake_clock():
    """Provides a controllable time source for testing."""
    import time

    class FakeClock:
        def __init__(self):
            self._current = time.time()

        def time(self):
            return self._current

        def sleep(self, duration):
            self._current += duration

    return FakeClock()


# =============================================================================
# MARKET DATA TEST INFRASTRUCTURE
# =============================================================================


@pytest.fixture
def mock_api_config() -> APIConfig:
    """Mock API configuration for testing."""
    return APIConfig(
        api_key="test_key",
        api_secret="test_secret",
        passphrase="test_passphrase",
        base_url="https://api.coinbase.com",
        sandbox=True,
    )


@pytest.fixture
def mock_bot_config(bot_config_factory) -> BotConfig:
    """Mock bot config for Coinbase brokerage tests."""
    return bot_config_factory(
        coinbase_sandbox_enabled=True,
        coinbase_api_mode="sandbox",
    )


@pytest.fixture
def mock_product_catalog() -> ProductCatalog:
    """Mock product catalog with common trading pairs."""
    catalog = Mock(spec=ProductCatalog)
    catalog.get_symbol.return_value = "BTC-USD"
    catalog.is_supported.return_value = True
    catalog.get_quote_currency.return_value = "USD"
    return catalog


@pytest.fixture
def market_data_service() -> MarketDataService:
    """Create MarketDataService instance for testing."""
    return MarketDataService()


# Cache and Data Management Helpers
@pytest.fixture
def mock_mark_cache():
    """Mock mark cache with deterministic behavior."""
    cache = Mock()
    cache.get.return_value = Decimal("50000.00")
    cache.set.return_value = None
    cache.is_valid.return_value = True
    cache.invalidate.return_value = None
    return cache


@pytest.fixture
def sample_market_data():
    """Sample market data for testing."""
    return {
        "BTC-USD": {
            "bid": Decimal("49900.00"),
            "ask": Decimal("50100.00"),
            "last": Decimal("50000.00"),
            "volume": Decimal("123.45"),
            "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        },
        "ETH-USD": {
            "bid": Decimal("2990.00"),
            "ask": Decimal("3010.00"),
            "last": Decimal("3000.00"),
            "volume": Decimal("567.89"),
            "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        },
    }


# Time-based Testing Helpers
@pytest.fixture
def time_helpers():
    """Helper functions for time-based testing."""

    class TimeHelpers:
        @staticmethod
        def utc_now() -> datetime:
            return datetime.now(UTC)

        @staticmethod
        def seconds_ago(seconds: int) -> datetime:
            return datetime.now(UTC) - timedelta(seconds=seconds)

        @staticmethod
        def minutes_ago(minutes: int) -> datetime:
            return datetime.now(UTC) - timedelta(minutes=minutes)

        @staticmethod
        def is_stale(timestamp: datetime, staleness_seconds: int = 30) -> bool:
            return (datetime.now(UTC) - timestamp).total_seconds() > staleness_seconds

    return TimeHelpers()
