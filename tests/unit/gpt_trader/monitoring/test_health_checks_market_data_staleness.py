"""Tests for market data staleness health signal."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gpt_trader.utilities.time_provider import FakeClock


class TestCheckMarketDataStalenessSignal:
    """Tests for check_market_data_staleness_signal function."""

    def test_market_data_service_missing(self) -> None:
        """Missing market data service returns UNKNOWN with details."""
        from gpt_trader.monitoring.health_checks import check_market_data_staleness_signal
        from gpt_trader.monitoring.health_signals import HealthStatus

        signal = check_market_data_staleness_signal(None)

        assert signal.name == "market_data_staleness"
        assert signal.status == HealthStatus.UNKNOWN
        assert signal.details.get("market_data_service_unavailable") is True

    def test_missing_timestamp_data(self) -> None:
        """Missing ticker timestamp returns UNKNOWN without crashing."""
        from gpt_trader.features.brokerages.coinbase.market_data_service import TickerCache
        from gpt_trader.monitoring.health_checks import check_market_data_staleness_signal
        from gpt_trader.monitoring.health_signals import HealthStatus

        class StubService:
            def __init__(self, cache: TickerCache) -> None:
                self.ticker_cache = cache

        service = StubService(TickerCache())

        signal = check_market_data_staleness_signal(service)

        assert signal.status == HealthStatus.UNKNOWN
        assert signal.details.get("timestamp_unavailable") is True

    def test_fresh_market_data(self) -> None:
        """Recent ticker updates should be OK."""
        from gpt_trader.features.brokerages.coinbase.market_data_service import (
            CoinbaseTickerService,
            Ticker,
            TickerCache,
        )
        from gpt_trader.monitoring.health_checks import check_market_data_staleness_signal
        from gpt_trader.monitoring.health_signals import HealthStatus, HealthThresholds

        clock = FakeClock(start_time=1000.0)
        cache = TickerCache()
        cache.update(
            Ticker(
                symbol="BTC-USD",
                bid=1.0,
                ask=1.0,
                last=1.0,
                ts=datetime.fromtimestamp(clock.time() - 5.0, tz=UTC),
            )
        )
        service = CoinbaseTickerService(symbols=["BTC-USD"], ticker_cache=cache)

        thresholds = HealthThresholds(
            market_data_staleness_seconds_warn=10.0,
            market_data_staleness_seconds_crit=30.0,
        )
        signal = check_market_data_staleness_signal(
            service,
            thresholds=thresholds,
            time_provider=clock,
        )

        assert signal.status == HealthStatus.OK
        assert signal.value == pytest.approx(5.0)
        assert signal.details.get("last_update_ts") == pytest.approx(clock.time() - 5.0)

    @pytest.mark.parametrize(
        ("age_seconds", "expected_status"),
        [
            (9.9, "OK"),
            (10.0, "WARN"),
            (30.0, "CRIT"),
        ],
    )
    def test_market_data_staleness_threshold_boundaries(
        self,
        age_seconds: float,
        expected_status: str,
    ) -> None:
        """Test staleness signal transitions around threshold boundaries."""
        from gpt_trader.features.brokerages.coinbase.market_data_service import (
            CoinbaseTickerService,
            Ticker,
            TickerCache,
        )
        from gpt_trader.monitoring.health_checks import check_market_data_staleness_signal
        from gpt_trader.monitoring.health_signals import HealthStatus, HealthThresholds

        clock = FakeClock(start_time=1000.0)
        cache = TickerCache()
        cache.update(
            Ticker(
                symbol="BTC-USD",
                bid=1.0,
                ask=1.0,
                last=1.0,
                ts=datetime.fromtimestamp(clock.time() - age_seconds, tz=UTC),
            )
        )
        service = CoinbaseTickerService(symbols=["BTC-USD"], ticker_cache=cache)

        thresholds = HealthThresholds(
            market_data_staleness_seconds_warn=10.0,
            market_data_staleness_seconds_crit=30.0,
        )
        signal = check_market_data_staleness_signal(
            service,
            thresholds=thresholds,
            time_provider=clock,
        )

        assert signal.status == HealthStatus(expected_status)
        assert signal.value == pytest.approx(age_seconds)

    @pytest.mark.parametrize(
        ("age_seconds", "expected_status"),
        [
            (0.0, "WARN"),
            (0.001, "WARN"),
            (30.0, "CRIT"),
        ],
    )
    def test_market_data_warn_zero_keeps_warn_band(
        self,
        age_seconds: float,
        expected_status: str,
    ) -> None:
        """Warn threshold of zero should still produce WARN before CRIT."""
        from gpt_trader.features.brokerages.coinbase.market_data_service import (
            CoinbaseTickerService,
            Ticker,
            TickerCache,
        )
        from gpt_trader.monitoring.health_checks import check_market_data_staleness_signal
        from gpt_trader.monitoring.health_signals import HealthStatus, HealthThresholds

        clock = FakeClock(start_time=1000.0)
        cache = TickerCache()
        cache.update(
            Ticker(
                symbol="BTC-USD",
                bid=1.0,
                ask=1.0,
                last=1.0,
                ts=datetime.fromtimestamp(clock.time() - age_seconds, tz=UTC),
            )
        )
        service = CoinbaseTickerService(symbols=["BTC-USD"], ticker_cache=cache)

        thresholds = HealthThresholds(
            market_data_staleness_seconds_warn=0.0,
            market_data_staleness_seconds_crit=30.0,
        )
        signal = check_market_data_staleness_signal(
            service,
            thresholds=thresholds,
            time_provider=clock,
        )

        assert signal.status == HealthStatus(expected_status)
