"""Tests for order submission retry behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import gpt_trader.features.live_trade.execution.order_submission as order_submission_module
from gpt_trader.features.live_trade.execution.order_submission import OrderSubmitter
from gpt_trader.utilities.time_provider import FakeClock


def test_submit_order_retries_when_classified_transient_then_success(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
    submit_order_with_result_call,
    mock_order,
) -> None:
    """Transient classification should trigger exactly one retry before success."""
    sleep_calls: list[float] = []

    submitter = OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
        enable_retries=True,
        sleep_fn=sleep_calls.append,
    )
    mock_broker.place_order.side_effect = [
        ValueError("timeout waiting for broker"),
        mock_order,
    ]

    mock_order.client_id = "retry-123"
    outcome = submit_order_with_result_call(submitter, client_order_id="retry-123")

    assert outcome.success is True
    assert outcome.order_id == "order-123"
    assert mock_broker.place_order.call_count == 2
    assert len(sleep_calls) == 1
    delay = sleep_calls[0]
    assert delay >= order_submission_module.SUBMISSION_RETRY_POLICY.base_delay
    assert delay <= order_submission_module.SUBMISSION_RETRY_POLICY.max_delay


def test_submit_order_retries_transient_then_fails_after_retry_cap(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
    submit_order_with_result_call,
) -> None:
    """Transient failures should stop after the single allowed retry."""
    sleep_calls: list[float] = []

    submitter = OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
        enable_retries=True,
        sleep_fn=sleep_calls.append,
    )
    mock_broker.place_order.side_effect = [
        ConnectionError("network down"),
        ConnectionError("network still down"),
    ]

    outcome = submit_order_with_result_call(submitter, client_order_id="retry-789")

    assert outcome.failed is True
    assert outcome.reason == "network"
    assert mock_broker.place_order.call_count == 2
    assert len(sleep_calls) == 1


def test_submit_order_no_retry_when_classified_non_transient(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
    submit_order_with_result_call,
) -> None:
    """Non-transient classification should not trigger retries."""
    sleep_calls: list[float] = []

    submitter = OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
        enable_retries=True,
        sleep_fn=sleep_calls.append,
    )
    mock_broker.place_order.side_effect = ValueError("insufficient funds")

    outcome = submit_order_with_result_call(submitter, client_order_id="retry-456")

    assert outcome.failed is True
    assert mock_broker.place_order.call_count == 1
    assert sleep_calls == []


def test_submit_order_latency_uses_time_provider(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
    submit_order_with_result_call,
    mock_order,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start_time=1000.0, start_monotonic=500.0)
    captured: dict[str, float] = {}

    def record_latency(latency_seconds: float, result: str, side: str) -> None:
        captured["latency_seconds"] = latency_seconds
        captured["result"] = result
        captured["side"] = side

    def place_order(**kwargs):
        clock.advance(1.25)
        return mock_order

    monkeypatch.setattr(order_submission_module, "_record_order_submission_latency", record_latency)
    mock_broker.place_order.side_effect = place_order

    submitter = OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
        enable_retries=False,
        time_provider=clock,
    )

    mock_order.client_id = "clock-123"
    outcome = submit_order_with_result_call(submitter, client_order_id="clock-123")

    assert outcome.success is True
    assert captured["latency_seconds"] == pytest.approx(1.25)


def test_retry_delays_use_backoff_policy_with_fake_clock(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
    submit_order_with_result_call,
    mock_order,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start_time=1000.0, start_monotonic=500.0)
    delays: list[float] = []

    def sleep_fn(seconds: float) -> None:
        delays.append(seconds)
        clock.advance(seconds)

    policy = order_submission_module.RetryPolicy(
        max_attempts=4,
        base_delay=0.1,
        max_delay=0.3,
        jitter=0.0,
    )
    monkeypatch.setattr(order_submission_module, "SUBMISSION_RETRY_POLICY", policy)

    mock_broker.place_order.side_effect = [
        ConnectionError("network down"),
        ConnectionError("network still down"),
        ConnectionError("still down"),
        mock_order,
    ]

    submitter = OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
        enable_retries=True,
        sleep_fn=sleep_fn,
        time_provider=clock,
    )

    mock_order.client_id = "timeout-retry"
    outcome = submit_order_with_result_call(submitter, client_order_id="timeout-retry")

    assert outcome.success is True
    assert delays == [0.1, 0.2, 0.3]
    assert delays[-1] == pytest.approx(policy.max_delay)
    assert clock.monotonic() == pytest.approx(500.0 + sum(delays))
