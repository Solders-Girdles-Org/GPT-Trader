"""Order submission metrics and latency tests."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import gpt_trader.features.live_trade.execution.order_event_recorder as recorder_module
import gpt_trader.features.live_trade.execution.order_submission as order_submission_module
from gpt_trader.core import OrderSide, OrderType


@pytest.fixture(autouse=True)
def _setup_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recorder_module, "get_monitoring_logger", lambda: MagicMock())
    monkeypatch.setattr(recorder_module, "emit_metric", MagicMock())


@pytest.fixture
def reset_metrics() -> None:
    from gpt_trader.monitoring.metrics_collector import reset_all

    reset_all()
    yield
    reset_all()


@pytest.fixture
def record_metric_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_record = MagicMock()
    monkeypatch.setattr(order_submission_module, "_record_order_submission_metric", mock_record)
    return mock_record


@pytest.fixture
def record_latency_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_latency = MagicMock()
    monkeypatch.setattr(order_submission_module, "_record_order_submission_latency", mock_latency)
    return mock_latency


@pytest.mark.usefixtures("reset_metrics")
def test_successful_order_records_metric(
    submitter,
    submit_order_call,
    mock_broker: MagicMock,
    mock_order,
) -> None:
    from gpt_trader.monitoring.metrics_collector import get_metrics_collector

    mock_order.client_id = "decision-123"
    mock_broker.place_order.return_value = mock_order

    submit_order_call(submitter)

    collector = get_metrics_collector()
    success_key = "gpt_trader_order_submission_total{reason=none,result=success,side=buy}"
    assert success_key in collector.counters
    assert collector.counters[success_key] == 1


@pytest.mark.usefixtures("reset_metrics")
def test_successful_order_increments_trades_executed_counter(
    submitter,
    submit_order_call,
    mock_broker: MagicMock,
    mock_order,
) -> None:
    from gpt_trader.monitoring.metrics_collector import get_metrics_collector

    mock_order.client_id = "decision-123"
    mock_broker.place_order.return_value = mock_order

    submit_order_call(submitter)

    collector = get_metrics_collector()
    assert collector.counters["gpt_trader_trades_executed_total"] == 1


@pytest.mark.usefixtures("reset_metrics")
def test_failed_order_records_metric_with_reason(
    submitter,
    submit_order_call,
    mock_broker: MagicMock,
) -> None:
    from gpt_trader.monitoring.metrics_collector import get_metrics_collector

    mock_broker.place_order.side_effect = RuntimeError("Insufficient balance")

    submit_order_call(
        submitter,
        symbol="ETH-USD",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        price=None,
        effective_price=Decimal("3000"),
        tif=None,
        leverage=None,
    )

    collector = get_metrics_collector()
    failed_key = (
        "gpt_trader_order_submission_total{reason=insufficient_funds,result=failed,side=sell}"
    )
    assert failed_key in collector.counters
    assert collector.counters[failed_key] == 1


def test_classification_label_used_in_metrics(
    submitter,
    submit_order_call,
    mock_broker: MagicMock,
    record_metric_mock: MagicMock,
) -> None:
    mock_broker.place_order.side_effect = RuntimeError("Rate limit exceeded")

    submit_order_call(
        submitter,
        order_type=OrderType.MARKET,
        price=None,
        tif=None,
        leverage=None,
    )

    call_kwargs = record_metric_mock.call_args[1]
    assert call_kwargs["reason"] == "rate_limit"
    assert call_kwargs["result"] == "failed"


def test_successful_submission_records_latency_histogram(
    submitter,
    submit_order_call,
    mock_broker: MagicMock,
    mock_order,
    record_latency_mock: MagicMock,
) -> None:
    mock_order.client_id = "decision-123"
    mock_order.type = OrderType.MARKET
    mock_broker.place_order.return_value = mock_order

    submit_order_call(
        submitter,
        order_type=OrderType.MARKET,
        price=None,
        tif=None,
        leverage=None,
    )

    record_latency_mock.assert_called_once()
    call_kwargs = record_latency_mock.call_args[1]
    assert call_kwargs["result"] == "success"
    assert call_kwargs["side"].lower() == "buy"
    assert call_kwargs["latency_seconds"] >= 0


def test_failed_submission_records_latency_with_failure_result(
    submitter,
    submit_order_call,
    mock_broker: MagicMock,
    record_latency_mock: MagicMock,
) -> None:
    mock_broker.place_order.side_effect = RuntimeError("Connection error")

    submit_order_call(
        submitter,
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        leverage=None,
    )

    record_latency_mock.assert_called_once()
    call_kwargs = record_latency_mock.call_args[1]
    assert call_kwargs["result"] == "failed"
    assert call_kwargs["side"].lower() == "sell"
