"""Tests for validation metrics recording and failure tracker integration."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import gpt_trader.features.live_trade.execution.validation as validation_module
from gpt_trader.core import OrderSide, OrderType, TimeInForce
from gpt_trader.features.live_trade.execution.validation import OrderValidator


@pytest.fixture
def record_counter_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_record = MagicMock()
    monkeypatch.setattr(validation_module, "record_counter", mock_record)
    return mock_record


class TestMetricsRecording:
    def test_mark_staleness_failure_records_metric(
        self,
        record_counter_mock: MagicMock,
        mock_broker: MagicMock,
        mock_risk_manager: MagicMock,
    ) -> None:
        from gpt_trader.features.live_trade.execution.validation import (
            METRIC_MARK_STALENESS_CHECK_FAILED,
            ValidationFailureTracker,
        )

        mock_risk_manager.check_mark_staleness.side_effect = RuntimeError("API error")

        tracker = ValidationFailureTracker()
        validator = OrderValidator(
            broker=mock_broker,
            risk_manager=mock_risk_manager,
            enable_order_preview=False,
            record_preview_callback=MagicMock(),
            record_rejection_callback=MagicMock(),
            failure_tracker=tracker,
        )

        validator.ensure_mark_is_fresh("BTC-PERP")

        record_counter_mock.assert_called_with(METRIC_MARK_STALENESS_CHECK_FAILED)

    def test_slippage_guard_failure_records_metric(
        self,
        record_counter_mock: MagicMock,
        mock_broker: MagicMock,
        mock_risk_manager: MagicMock,
    ) -> None:
        from gpt_trader.features.live_trade.execution.validation import (
            METRIC_SLIPPAGE_GUARD_CHECK_FAILED,
            ValidationFailureTracker,
        )

        mock_broker.get_market_snapshot.side_effect = RuntimeError("API error")

        tracker = ValidationFailureTracker()
        validator = OrderValidator(
            broker=mock_broker,
            risk_manager=mock_risk_manager,
            enable_order_preview=False,
            record_preview_callback=MagicMock(),
            record_rejection_callback=MagicMock(),
            failure_tracker=tracker,
        )

        validator.enforce_slippage_guard(
            symbol="BTC-PERP",
            side=OrderSide.BUY,
            order_quantity=Decimal("1.0"),
            effective_price=Decimal("50000"),
        )

        record_counter_mock.assert_called_with(METRIC_SLIPPAGE_GUARD_CHECK_FAILED)

    def test_preview_failure_records_metric(
        self,
        record_counter_mock: MagicMock,
        mock_risk_manager: MagicMock,
    ) -> None:
        from gpt_trader.features.live_trade.execution.validation import (
            METRIC_ORDER_PREVIEW_FAILED,
            ValidationFailureTracker,
        )

        broker = MagicMock()
        broker.preview_order = MagicMock(side_effect=RuntimeError("API error"))
        broker.edit_order_preview = MagicMock()

        tracker = ValidationFailureTracker()
        validator = OrderValidator(
            broker=broker,
            risk_manager=mock_risk_manager,
            enable_order_preview=True,
            record_preview_callback=MagicMock(),
            record_rejection_callback=MagicMock(),
            failure_tracker=tracker,
        )

        validator.maybe_preview_order(
            symbol="BTC-PERP",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            order_quantity=Decimal("1.0"),
            effective_price=Decimal("50000"),
            stop_price=None,
            tif=TimeInForce.GTC,
            reduce_only=False,
            leverage=10,
        )

        record_counter_mock.assert_called_with(METRIC_ORDER_PREVIEW_FAILED)


class TestFailureTrackerIntegration:
    def test_success_resets_mark_staleness_counter(
        self,
        mock_broker: MagicMock,
        mock_risk_manager: MagicMock,
    ) -> None:
        from gpt_trader.features.live_trade.execution.validation import ValidationFailureTracker

        mock_risk_manager.check_mark_staleness.return_value = False

        tracker = ValidationFailureTracker()
        tracker.record_failure("mark_staleness")
        tracker.record_failure("mark_staleness")
        assert tracker.get_failure_count("mark_staleness") == 2

        validator = OrderValidator(
            broker=mock_broker,
            risk_manager=mock_risk_manager,
            enable_order_preview=False,
            record_preview_callback=MagicMock(),
            record_rejection_callback=MagicMock(),
            failure_tracker=tracker,
        )

        validator.ensure_mark_is_fresh("BTC-PERP")

        assert tracker.get_failure_count("mark_staleness") == 0

    def test_failure_increments_slippage_counter(
        self,
        mock_broker: MagicMock,
        mock_risk_manager: MagicMock,
    ) -> None:
        from gpt_trader.features.live_trade.execution.validation import ValidationFailureTracker

        mock_broker.get_market_snapshot.side_effect = RuntimeError("API error")

        tracker = ValidationFailureTracker()
        validator = OrderValidator(
            broker=mock_broker,
            risk_manager=mock_risk_manager,
            enable_order_preview=False,
            record_preview_callback=MagicMock(),
            record_rejection_callback=MagicMock(),
            failure_tracker=tracker,
        )

        validator.enforce_slippage_guard(
            symbol="BTC-PERP",
            side=OrderSide.BUY,
            order_quantity=Decimal("1.0"),
            effective_price=Decimal("50000"),
        )

        assert tracker.get_failure_count("slippage_guard") == 1

        validator.enforce_slippage_guard(
            symbol="BTC-PERP",
            side=OrderSide.BUY,
            order_quantity=Decimal("1.0"),
            effective_price=Decimal("50000"),
        )

        assert tracker.get_failure_count("slippage_guard") == 2

    def test_escalation_triggered_after_repeated_failures(
        self,
        record_counter_mock: MagicMock,
        mock_broker: MagicMock,
        mock_risk_manager: MagicMock,
    ) -> None:
        from gpt_trader.features.live_trade.execution.validation import (
            METRIC_CONSECUTIVE_FAILURES_ESCALATION,
            ValidationFailureTracker,
        )

        mock_risk_manager.check_mark_staleness.side_effect = RuntimeError("API error")

        escalation_called = []
        tracker = ValidationFailureTracker(
            escalation_threshold=3,
            escalation_callback=lambda: escalation_called.append(True),
        )

        validator = OrderValidator(
            broker=mock_broker,
            risk_manager=mock_risk_manager,
            enable_order_preview=False,
            record_preview_callback=MagicMock(),
            record_rejection_callback=MagicMock(),
            failure_tracker=tracker,
        )

        validator.ensure_mark_is_fresh("BTC-PERP")
        validator.ensure_mark_is_fresh("BTC-PERP")
        assert len(escalation_called) == 0

        validator.ensure_mark_is_fresh("BTC-PERP")
        assert len(escalation_called) == 1

        record_counter_mock.assert_any_call(METRIC_CONSECUTIVE_FAILURES_ESCALATION)
