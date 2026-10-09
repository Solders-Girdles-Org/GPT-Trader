"""Tests for broker ping and degradation state health checks."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from gpt_trader.features.brokerages.coinbase.market_data_service import Ticker, TickerCache
from gpt_trader.monitoring.health_checks import (
    TICKER_CACHE_UNAVAILABLE_COUNTER,
    TICKER_FRESHNESS_CHECKS_COUNTER,
    TICKER_STALE_SYMBOLS_COUNTER,
    check_broker_ping,
    check_degradation_state,
    check_ticker_freshness,
)
from gpt_trader.monitoring.metrics_collector import get_metrics_collector, reset_all
from gpt_trader.monitoring.metrics_exporter import format_prometheus
from gpt_trader.utilities.time_provider import FakeClock


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset metrics before and after each test."""
    reset_all()
    yield
    reset_all()


class TestCheckBrokerPing:
    """Tests for check_broker_ping function."""

    def test_success_with_get_time(self) -> None:
        """Test successful ping using get_time method."""
        broker = MagicMock()
        broker.get_time.return_value = {"epoch": 1234567890}
        result = check_broker_ping(broker)
        assert result.healthy is True
        assert "latency_ms" in result.details
        assert result.details["method"] == "get_time"
        assert result.details["severity"] == "critical"
        broker.get_time.assert_called_once()

    def test_success_fallback_to_list_balances(self) -> None:
        """Test fallback to list_balances when get_time not available."""
        broker = MagicMock(spec=["list_balances"])
        broker.list_balances.return_value = [{"currency": "USD", "available": "100"}]
        result = check_broker_ping(broker)
        assert result.healthy is True
        assert result.details["method"] == "list_balances"
        broker.list_balances.assert_called_once()

    def test_failure_on_exception(self) -> None:
        """Test failure when broker call raises exception."""
        broker = MagicMock()
        broker.get_time.side_effect = ConnectionError("connection refused")
        result = check_broker_ping(broker)
        assert result.healthy is False
        assert "error" in result.details
        assert result.details["error_type"] == "ConnectionError"
        assert result.details["severity"] == "critical"

    def test_high_latency_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that high latency sets severity to warning."""
        broker = MagicMock()
        mock_time = MagicMock()
        mock_time.side_effect = [0, 2.5]
        monkeypatch.setattr(time, "perf_counter", mock_time)
        result = check_broker_ping(broker)
        assert result.healthy is True
        assert result.details["latency_ms"] == 2500.0
        assert result.details["severity"] == "warning"
        assert "warning" in result.details


class TestCheckDegradationState:
    """Tests for check_degradation_state function."""

    def test_normal_operation(self) -> None:
        """Test healthy state when no degradation."""
        degradation_state = MagicMock()
        degradation_state.get_status.return_value = {
            "global_paused": False,
            "global_reason": None,
            "paused_symbols": {},
            "global_remaining_seconds": 0,
        }
        result = check_degradation_state(degradation_state)
        assert result.healthy is True
        assert result.details["global_paused"] is False
        assert result.details["reduce_only_mode"] is False

    def test_global_paused(self) -> None:
        """Test failure when globally paused."""
        degradation_state = MagicMock()
        degradation_state.get_status.return_value = {
            "global_paused": True,
            "global_reason": "max_reconnect_attempts",
            "paused_symbols": {},
            "global_remaining_seconds": 300,
        }
        result = check_degradation_state(degradation_state)
        assert result.healthy is False
        assert result.details["global_paused"] is True
        assert result.details["severity"] == "critical"

    def test_reduce_only_mode(self) -> None:
        """Test warning when in reduce-only mode."""
        degradation_state = MagicMock()
        degradation_state.get_status.return_value = {
            "global_paused": False,
            "global_reason": None,
            "paused_symbols": {},
            "global_remaining_seconds": 0,
        }
        risk_manager = MagicMock()
        risk_manager.is_reduce_only_mode = MagicMock(return_value=True)
        risk_manager._reduce_only_mode = True
        risk_manager._reduce_only_reason = "validation_failures"
        del risk_manager._cfm_reduce_only_mode
        risk_manager.is_cfm_reduce_only_mode = MagicMock(return_value=False)
        result = check_degradation_state(degradation_state, risk_manager)
        assert result.healthy is True
        assert result.details["reduce_only_mode"] is True
        assert result.details["reduce_only_reason"] == "validation_failures"
        assert result.details["severity"] == "warning"

    def test_symbol_paused(self) -> None:
        """Test warning when specific symbols are paused."""
        degradation_state = MagicMock()
        degradation_state.get_status.return_value = {
            "global_paused": False,
            "global_reason": None,
            "paused_symbols": {"BTC-USD": {"reason": "rate_limited"}},
            "global_remaining_seconds": 0,
        }
        result = check_degradation_state(degradation_state)
        assert result.healthy is True
        assert result.details["paused_symbol_count"] == 1
        assert "BTC-USD" in result.details["paused_symbols"]
        assert result.details["severity"] == "warning"

    def test_non_dict_paused_symbols_falls_back_to_empty(self) -> None:
        """Malformed paused_symbols payload should not crash the check."""
        degradation_state = MagicMock()
        degradation_state.get_status.return_value = {
            "global_paused": False,
            "global_reason": None,
            "paused_symbols": ["BTC-USD"],
            "global_remaining_seconds": 0,
        }

        result = check_degradation_state(degradation_state)

        assert result.healthy is True
        assert result.details["paused_symbol_count"] == 0

    def test_cfm_reduce_only_attribute_fallback(self) -> None:
        """Legacy CFM reduce-only attributes should still set warning state."""
        degradation_state = MagicMock()
        degradation_state.get_status.return_value = {
            "global_paused": False,
            "global_reason": None,
            "paused_symbols": {},
            "global_remaining_seconds": 0,
        }

        class LegacyRiskManager:
            _cfm_reduce_only_mode = True
            _cfm_reduce_only_reason = "cfm_pause"

        result = check_degradation_state(degradation_state, LegacyRiskManager())

        assert result.healthy is True
        assert result.details["reduce_only_mode"] is True
        assert result.details["reduce_only_reason"] == "cfm_pause"
        assert result.details["severity"] == "warning"


class FakeMarketDataService:
    def __init__(self, symbols: list[str], ticker_cache: TickerCache) -> None:
        self._symbols = symbols
        self._ticker_cache = ticker_cache

    def get_ticker_freshness_provider(self) -> TickerCache:
        return self._ticker_cache


class FakeMarketDataServiceNoCache:
    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols


class TestCheckTickerFreshness:
    """Tests for check_ticker_freshness function."""

    def test_all_fresh(self) -> None:
        """Test healthy when all symbols are fresh."""
        base_time = datetime(2024, 1, 1, tzinfo=UTC)
        clock = FakeClock(base_time)
        cache = TickerCache(ttl_seconds=10, clock=clock)
        for symbol in ("BTC-USD", "ETH-USD"):
            cache.update(
                Ticker(
                    symbol=symbol,
                    bid=1.0,
                    ask=2.0,
                    last=1.5,
                    ts=base_time,
                )
            )
        market_data_service = FakeMarketDataService(["BTC-USD", "ETH-USD"], cache)

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is True
        assert result.details["stale_symbols"] == []
        assert result.details["stale_count"] == 0

    def test_records_profile_and_outcome_metrics(self) -> None:
        """Test ticker freshness emits profile histogram and ok outcome counter."""
        base_time = datetime(2024, 1, 1, tzinfo=UTC)
        clock = FakeClock(base_time)
        cache = TickerCache(ttl_seconds=10, clock=clock)
        for symbol in ("BTC-USD", "ETH-USD"):
            cache.update(
                Ticker(
                    symbol=symbol,
                    bid=1.0,
                    ask=2.0,
                    last=1.5,
                    ts=base_time,
                )
            )
        market_data_service = FakeMarketDataService(["BTC-USD", "ETH-USD"], cache)

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is True
        collector = get_metrics_collector()
        summary = collector.get_metrics_summary()
        histogram_key = "gpt_trader_profile_duration_seconds{phase=ticker_freshness}"
        assert histogram_key in summary["histograms"]
        ok_key = f"{TICKER_FRESHNESS_CHECKS_COUNTER}{{result=ok}}"
        assert collector.counters[ok_key] == 1
        output = format_prometheus(summary)
        assert f'{TICKER_FRESHNESS_CHECKS_COUNTER}{{result="ok"}} 1' in output
        assert 'gpt_trader_profile_duration_seconds_count{phase="ticker_freshness"} 1' in output

    def test_error_records_outcome_metric(self) -> None:
        """Test check exceptions increment error outcome counter."""

        class ExplodingProvider:
            def is_stale(self, symbol: str) -> bool:
                raise RuntimeError("boom")

        class MarketDataWithProvider:
            def __init__(self, symbols: list[str], provider: ExplodingProvider) -> None:
                self._symbols = symbols
                self._provider = provider

            def get_ticker_freshness_provider(self) -> ExplodingProvider:
                return self._provider

        market_data_service = MarketDataWithProvider(["BTC-USD"], ExplodingProvider())

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is False
        assert result.details["error_type"] == "RuntimeError"
        collector = get_metrics_collector()
        error_key = f"{TICKER_FRESHNESS_CHECKS_COUNTER}{{result=error}}"
        assert collector.counters[error_key] == 1

    def test_some_stale(self) -> None:
        """Test unhealthy when some symbols are stale."""
        base_time = datetime(2024, 1, 1, tzinfo=UTC)
        clock = FakeClock(base_time)
        cache = TickerCache(ttl_seconds=5, clock=clock)
        cache.update(
            Ticker(
                symbol="BTC-USD",
                bid=1.0,
                ask=2.0,
                last=1.5,
                ts=base_time,
            )
        )
        cache.update(
            Ticker(
                symbol="ETH-USD",
                bid=1.0,
                ask=2.0,
                last=1.5,
                ts=base_time - timedelta(seconds=10),
            )
        )
        market_data_service = FakeMarketDataService(["BTC-USD", "ETH-USD"], cache)

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is False
        assert "ETH-USD" in result.details["stale_symbols"]
        assert result.details["stale_count"] == 1

    def test_no_data_available(self) -> None:
        """Test unhealthy when no tickers are available."""
        base_time = datetime(2024, 1, 1, tzinfo=UTC)
        clock = FakeClock(base_time)
        cache = TickerCache(ttl_seconds=5, clock=clock)
        market_data_service = FakeMarketDataService(["BTC-USD", "ETH-USD"], cache)

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is False
        assert set(result.details["stale_symbols"]) == {"BTC-USD", "ETH-USD"}
        assert result.details["stale_count"] == 2

    def test_cache_unavailable_records_metric(self) -> None:
        """Test missing provider/cache increments metrics counter and is skipped/healthy."""
        market_data_service = FakeMarketDataServiceNoCache(["BTC-USD"])

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is True
        assert result.details["ticker_cache_unavailable"] is True
        collector = get_metrics_collector()
        assert collector.counters[TICKER_CACHE_UNAVAILABLE_COUNTER] == 1
        output = format_prometheus(collector.get_metrics_summary())
        assert f"{TICKER_CACHE_UNAVAILABLE_COUNTER} 1" in output

    def test_stale_symbol_metrics_incremented(self) -> None:
        """Test stale ticker observations increment metrics counter by symbol count."""
        base_time = datetime(2024, 1, 1, tzinfo=UTC)
        clock = FakeClock(base_time)
        cache = TickerCache(ttl_seconds=5, clock=clock)
        cache.update(
            Ticker(
                symbol="BTC-USD",
                bid=1.0,
                ask=2.0,
                last=1.5,
                ts=base_time - timedelta(seconds=10),
            )
        )
        market_data_service = FakeMarketDataService(["BTC-USD"], cache)

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is False
        assert result.details["stale_count"] == 1
        collector = get_metrics_collector()
        assert collector.counters[TICKER_STALE_SYMBOLS_COUNTER] == 1
        output = format_prometheus(collector.get_metrics_summary())
        assert f"{TICKER_STALE_SYMBOLS_COUNTER} 1" in output

    def test_partial_symbol_coverage(self) -> None:
        """Test missing symbols are marked stale."""
        base_time = datetime(2024, 1, 1, tzinfo=UTC)
        clock = FakeClock(base_time)
        cache = TickerCache(ttl_seconds=5, clock=clock)
        cache.update(
            Ticker(
                symbol="BTC-USD",
                bid=1.0,
                ask=2.0,
                last=1.5,
                ts=base_time,
            )
        )
        market_data_service = FakeMarketDataService(["BTC-USD", "ETH-USD"], cache)

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is False
        assert result.details["stale_symbols"] == ["ETH-USD"]
        assert result.details["stale_count"] == 1

    def test_missing_provider_is_skipped(self) -> None:
        """Test missing freshness provider is skipped and healthy."""

        class MarketDataNoProvider:
            def __init__(self, symbols: list[str]) -> None:
                self._symbols = symbols

        market_data_service = MarketDataNoProvider(["BTC-USD"])

        result = check_ticker_freshness(market_data_service)

        assert result.healthy is True
        assert result.details["skipped"] is True
        assert result.details["reason"] == "ticker_freshness_provider_unavailable"
