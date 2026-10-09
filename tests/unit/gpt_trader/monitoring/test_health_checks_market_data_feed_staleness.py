"""Tests for market data feed staleness health check wrapper."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gpt_trader.monitoring.health_checks import check_market_data_feed_staleness
from gpt_trader.utilities.time_provider import FakeClock


class TestCheckMarketDataFeedStaleness:
    """Tests for check_market_data_feed_staleness wrapper behavior."""

    def test_unknown_signal_is_treated_as_skipped(self) -> None:
        healthy, details = check_market_data_feed_staleness(None)

        assert healthy is True
        assert details["status"] == "UNKNOWN"
        assert details["skipped"] is True
        assert details["reason"] == "market_data_timestamp_unavailable"

    @pytest.mark.parametrize(
        ("age_seconds", "expected_healthy", "expected_status", "expected_severity"),
        [
            (5.0, True, "OK", "ok"),
            (20.0, False, "WARN", "warning"),
            (40.0, False, "CRIT", "critical"),
        ],
    )
    def test_status_and_severity_follow_staleness_thresholds(
        self,
        age_seconds: float,
        expected_healthy: bool,
        expected_status: str,
        expected_severity: str,
    ) -> None:
        from gpt_trader.monitoring.health_signals import HealthThresholds

        class TimestampService:
            def __init__(self, last_update: datetime) -> None:
                self._last_update = last_update

            def get_last_ticker_timestamp(self) -> datetime:
                return self._last_update

        clock = FakeClock(start_time=1000.0)
        service = TimestampService(
            datetime.fromtimestamp(clock.time() - age_seconds, tz=UTC)
        )
        thresholds = HealthThresholds(
            market_data_staleness_seconds_warn=10.0,
            market_data_staleness_seconds_crit=30.0,
        )

        healthy, details = check_market_data_feed_staleness(
            service,
            thresholds=thresholds,
            time_provider=clock,
        )

        assert healthy is expected_healthy
        assert details["status"] == expected_status
        assert details["severity"] == expected_severity

    def test_timeout_decision_details_cover_warn_and_crit(self) -> None:
        from gpt_trader.monitoring.health_signals import HealthStatus, HealthThresholds

        class TimestampService:
            def __init__(self, last_update: datetime) -> None:
                self._last_update = last_update

            def get_last_ticker_timestamp(self) -> datetime:
                return self._last_update

        clock = FakeClock(start_time=1000.0)
        thresholds = HealthThresholds(
            market_data_staleness_seconds_warn=10.0,
            market_data_staleness_seconds_crit=30.0,
        )

        service = TimestampService(datetime.fromtimestamp(clock.time() - 15.0, tz=UTC))

        healthy, details = check_market_data_feed_staleness(
            service,
            thresholds=thresholds,
            time_provider=clock,
        )

        assert healthy is False
        assert details["status"] == HealthStatus.WARN.value
        assert details["timeout_attempt"] == 2
        assert details["timeout_delay_seconds"] == pytest.approx(10.0)
        assert details["timeout_capped"] is False

        service = TimestampService(datetime.fromtimestamp(clock.time() - 35.0, tz=UTC))

        healthy, details = check_market_data_feed_staleness(
            service,
            thresholds=thresholds,
            time_provider=clock,
        )

        assert healthy is False
        assert details["status"] == HealthStatus.CRIT.value
        assert details["timeout_attempt"] == 3
        assert details["timeout_delay_seconds"] == pytest.approx(30.0)
        assert details["timeout_capped"] is True

    def test_warn_zero_preserves_crit_timeout_metadata(self) -> None:
        from gpt_trader.monitoring.health_signals import HealthStatus, HealthThresholds

        class TimestampService:
            def __init__(self, last_update: datetime) -> None:
                self._last_update = last_update

            def get_last_ticker_timestamp(self) -> datetime:
                return self._last_update

        clock = FakeClock(start_time=1000.0)
        thresholds = HealthThresholds(
            market_data_staleness_seconds_warn=0.0,
            market_data_staleness_seconds_crit=30.0,
        )

        service = TimestampService(datetime.fromtimestamp(clock.time() - 1.0, tz=UTC))
        healthy, details = check_market_data_feed_staleness(
            service,
            thresholds=thresholds,
            time_provider=clock,
        )

        assert healthy is False
        assert details["status"] == HealthStatus.WARN.value
        assert details["timeout_attempt"] == 2
        assert details["timeout_delay_seconds"] == pytest.approx(0.0)
        assert details["timeout_capped"] is False

        service = TimestampService(datetime.fromtimestamp(clock.time() - 35.0, tz=UTC))
        healthy, details = check_market_data_feed_staleness(
            service,
            thresholds=thresholds,
            time_provider=clock,
        )

        assert healthy is False
        assert details["status"] == HealthStatus.CRIT.value
        assert details["timeout_attempt"] == 3
        assert details["timeout_delay_seconds"] == pytest.approx(30.0)
        assert details["timeout_capped"] is True
