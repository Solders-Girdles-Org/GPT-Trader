"""Tests for telemetry_health.update_mark_and_metrics()."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

import gpt_trader.features.live_trade.engines.telemetry_health as telemetry_health_module
from gpt_trader.features.live_trade.engines.telemetry_health import update_mark_and_metrics


class TestUpdateMarkAndMetrics:
    """Tests for update_mark_and_metrics function."""

    def _create_mock_context(self) -> Mock:
        """Create a mock CoordinatorContext."""
        ctx = Mock()
        ctx.strategy_coordinator = None
        ctx.runtime_state = None
        ctx.registry.extras = {}
        ctx.risk_manager = None
        ctx.bot_id = "test_bot"
        ctx.event_store = Mock()
        return ctx

    def test_updates_strategy_coordinator_mark_window(self) -> None:
        """Test updates mark window via strategy_coordinator."""
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        strategy_coord = Mock()
        strategy_coord.update_mark_window = Mock()
        ctx.strategy_coordinator = strategy_coord

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))

        strategy_coord.update_mark_window.assert_called_once_with("BTC-PERP", Decimal("50000"))

    def test_updates_runtime_state_mark_window(self) -> None:
        """Test updates mark window via runtime_state."""
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        runtime_state = Mock()
        runtime_state.mark_lock = threading.Lock()
        runtime_state.mark_windows = {}
        ctx.runtime_state = runtime_state
        ctx.config.strategy.short_ma_period = 10
        ctx.config.strategy.long_ma_period = 20

        update_mark_and_metrics(coordinator, ctx, "ETH-PERP", Decimal("3000"))

        assert "ETH-PERP" in runtime_state.mark_windows
        assert Decimal("3000") in runtime_state.mark_windows["ETH-PERP"]

    def test_prunes_mark_window_when_exceeds_max_size(self) -> None:
        """Test prunes mark window when it exceeds max size."""
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        runtime_state = Mock()
        runtime_state.mark_lock = threading.Lock()
        runtime_state.mark_windows = {"BTC-PERP": [Decimal(str(i)) for i in range(50)]}
        ctx.runtime_state = runtime_state
        ctx.config.strategy.short_ma_period = 10
        ctx.config.strategy.long_ma_period = 20

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("100"))

        # max_size = max(10, 20) + 5 = 25
        assert len(runtime_state.mark_windows["BTC-PERP"]) <= 25

    def test_records_market_update_via_extras_monitor(self) -> None:
        """Test records update via market_monitor from extras."""
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        monitor = Mock()
        monitor.record_update = Mock()
        ctx.registry.extras = {"market_monitor": monitor}

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))

        monitor.record_update.assert_not_called()

    def test_records_market_update_via_coordinator_monitor(self) -> None:
        """Test records update via coordinator._market_monitor."""
        coordinator = Mock()
        monitor = Mock()
        monitor.record_update = Mock()
        coordinator._market_monitor = monitor
        ctx = self._create_mock_context()

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))

        monitor.record_update.assert_called_once_with("BTC-PERP")

    def test_records_risk_manager_mark_update(self) -> None:
        """Test records mark update to risk_manager."""
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        risk_manager = Mock()
        risk_manager.record_mark_update = Mock(return_value=None)
        risk_manager.last_mark_update = {}
        ctx.risk_manager = risk_manager

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))

        risk_manager.record_mark_update.assert_called()

    def test_throttles_mark_metric_with_invalid_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator = Mock()
        coordinator._market_monitor = None
        coordinator._mark_metric_last_emit = None
        ctx = self._create_mock_context()
        ctx.config.status_interval = "bad"

        times = iter([100.0, 101.0])
        monkeypatch.setattr(telemetry_health_module.time, "time", lambda: next(times))
        emit = Mock()
        monkeypatch.setattr(telemetry_health_module, "emit_metric", emit)

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))
        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50001"))

        assert emit.call_count == 1

    def test_record_mark_update_result_is_stored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        sentinel = datetime(2024, 1, 1, tzinfo=UTC)
        risk_manager = Mock()
        risk_manager.record_mark_update.return_value = sentinel
        risk_manager.last_mark_update = {}
        ctx.risk_manager = risk_manager
        ctx.config.status_interval = 999
        monkeypatch.setattr(telemetry_health_module, "utc_now", lambda: sentinel)

        update_mark_and_metrics(coordinator, ctx, "BTC-USD", Decimal("50000"))

        assert risk_manager.last_mark_update["BTC-USD"] is sentinel

    def test_record_mark_update_exception_uses_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        sentinel = datetime(2024, 1, 2, tzinfo=UTC)
        risk_manager = Mock()
        risk_manager.record_mark_update.side_effect = RuntimeError("boom")
        risk_manager.last_mark_update = {}
        ctx.risk_manager = risk_manager
        ctx.config.status_interval = 999
        monkeypatch.setattr(telemetry_health_module, "utc_now", lambda: sentinel)

        update_mark_and_metrics(coordinator, ctx, "BTC-USD", Decimal("50000"))

        assert risk_manager.last_mark_update["BTC-USD"] is sentinel

    def test_creates_last_mark_update_dict_if_missing(self) -> None:
        """Test creates last_mark_update dict if not present."""
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        risk_manager = Mock(spec=[])
        ctx.risk_manager = risk_manager

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))

        assert hasattr(risk_manager, "last_mark_update")

    def test_handles_strategy_coordinator_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test handles error from strategy_coordinator gracefully."""
        coordinator = Mock()
        coordinator._market_monitor = None
        ctx = self._create_mock_context()
        strategy_coord = Mock()
        strategy_coord.update_mark_window.side_effect = RuntimeError("Failed")
        ctx.strategy_coordinator = strategy_coord
        mock_logger = Mock()
        monkeypatch.setattr(telemetry_health_module, "logger", mock_logger)

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))

        assert any(
            call.kwargs.get("stage") == "mark_window" for call in mock_logger.debug.call_args_list
        )

    def test_handles_market_monitor_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test handles error from market_monitor gracefully."""
        coordinator = Mock()
        monitor = Mock()
        monitor.record_update.side_effect = RuntimeError("Failed")
        coordinator._market_monitor = monitor
        ctx = self._create_mock_context()
        mock_logger = Mock()
        monkeypatch.setattr(telemetry_health_module, "logger", mock_logger)

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))

        assert any(
            call.kwargs.get("stage") == "market_monitor"
            for call in mock_logger.debug.call_args_list
        )

    def test_handles_non_dict_extras(self) -> None:
        """Test handles non-dict extras gracefully."""
        coordinator = Mock()
        monitor = Mock()
        coordinator._market_monitor = monitor
        ctx = self._create_mock_context()
        ctx.registry.extras = "not_a_dict"

        update_mark_and_metrics(coordinator, ctx, "BTC-PERP", Decimal("50000"))
        monitor.record_update.assert_called_once_with("BTC-PERP")
