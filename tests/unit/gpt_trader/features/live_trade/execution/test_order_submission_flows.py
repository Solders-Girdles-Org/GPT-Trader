from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.core import Order, OrderSide, OrderStatus, OrderType, TimeInForce
from gpt_trader.features.live_trade.execution.order_submission import OrderSubmitter
from gpt_trader.persistence.orders_store import OrderRecord, OrdersStore
from gpt_trader.persistence.orders_store import OrderStatus as StoreOrderStatus
from gpt_trader.utilities.datetime_helpers import utc_now


@pytest.fixture
def reset_metrics() -> None:
    from gpt_trader.monitoring.metrics_collector import reset_all

    reset_all()
    yield
    reset_all()


@pytest.fixture
def flow_submitter(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
) -> OrderSubmitter:
    return OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
    )


def _status_value(name: str):
    if name == "CANCELLED":
        return OrderStatus.CANCELLED
    status = MagicMock()
    status.value = name
    return status


def _rejected_order(status) -> MagicMock:
    order = MagicMock(spec=["id", "status", "quantity", "filled_quantity"])
    order.id = "rejected-order"
    order.status = status
    order.quantity = Decimal("1.0")
    order.filled_quantity = Decimal("0")
    return order


MARKET_KWARGS = {"order_type": OrderType.MARKET, "price": None, "tif": None, "leverage": None}


def test_submit_order_success_tracks_trade(
    flow_submitter: OrderSubmitter,
    submit_order_call,
    mock_broker: MagicMock,
    mock_order: Order,
    mock_event_store: MagicMock,
    open_orders: list[str],
) -> None:
    mock_order.client_id = "custom-id"
    mock_broker.place_order.return_value = mock_order

    result = submit_order_call(flow_submitter, client_order_id="custom-id")

    assert result == "order-123"
    assert "order-123" in open_orders
    mock_event_store.append_trade.assert_called_once()


def test_submit_order_with_result_success(
    flow_submitter: OrderSubmitter,
    submit_order_with_result_call,
    mock_broker: MagicMock,
    mock_order: Order,
) -> None:
    mock_order.client_id = "custom-id"
    mock_broker.place_order.return_value = mock_order

    outcome = submit_order_with_result_call(flow_submitter, client_order_id="custom-id")

    assert outcome.success is True
    assert outcome.order_id == "order-123"
    assert outcome.reason is None


def test_submit_order_missing_decision_id_rejected(
    flow_submitter: OrderSubmitter,
    submit_order_with_result_call,
    mock_broker: MagicMock,
    emit_metric_mock: MagicMock,
    reset_metrics: None,
) -> None:
    from gpt_trader.monitoring.metrics_collector import get_metrics_collector

    outcome = submit_order_with_result_call(flow_submitter, client_order_id=None)

    assert outcome.rejected is True
    assert outcome.reason == "missing_decision_id"
    mock_broker.place_order.assert_not_called()
    emit_metric_mock.assert_called()
    collector = get_metrics_collector()
    assert collector.counters["gpt_trader_trades_blocked_total"] == 1


@pytest.mark.parametrize("status_name", ["CANCELLED", "FAILED"])
def test_submit_order_rejection_returns_none(
    status_name: str,
    flow_submitter: OrderSubmitter,
    submit_order_call,
    mock_broker: MagicMock,
    emit_metric_mock: MagicMock,
    monitoring_logger: MagicMock,
    open_orders: list[str],
) -> None:
    mock_broker.place_order.return_value = _rejected_order(_status_value(status_name))

    result = submit_order_call(flow_submitter, **MARKET_KWARGS, reduce_only=True)

    assert result is None
    assert "rejected-order" not in open_orders
    emit_metric_mock.assert_called()


def test_submit_order_with_result_rejection_reason(
    flow_submitter: OrderSubmitter,
    submit_order_with_result_call,
    mock_broker: MagicMock,
    emit_metric_mock: MagicMock,
) -> None:
    mock_broker.place_order.return_value = _rejected_order(_status_value("CANCELLED"))

    outcome = submit_order_with_result_call(flow_submitter, **MARKET_KWARGS, reduce_only=True)

    assert outcome.rejected is True
    assert outcome.reason == "broker_status"
    assert outcome.reason_detail == "CANCELLED"
    emit_metric_mock.assert_called()


def test_submit_order_with_result_failure_reason(
    flow_submitter: OrderSubmitter,
    submit_order_with_result_call,
    mock_broker: MagicMock,
    monitoring_logger: MagicMock,
) -> None:
    mock_broker.place_order.side_effect = RuntimeError("Request timeout")

    outcome = submit_order_with_result_call(flow_submitter)

    assert outcome.failed is True
    assert outcome.reason == "timeout"


def test_submit_order_exception_returns_none(
    flow_submitter: OrderSubmitter,
    submit_order_call,
    mock_broker: MagicMock,
    monitoring_logger: MagicMock,
) -> None:
    mock_broker.place_order.side_effect = RuntimeError("API error")

    result = submit_order_call(flow_submitter)

    assert result is None


def test_submit_order_none_order_returns_none(
    flow_submitter: OrderSubmitter,
    submit_order_call,
    mock_broker: MagicMock,
) -> None:
    mock_broker.place_order.return_value = None

    result = submit_order_call(
        flow_submitter,
        **MARKET_KWARGS,
        reduce_only=True,
        client_order_id="test-id",
    )

    assert result is None


def test_submit_order_none_order_persists_terminal_status(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
    submit_order_call,
    tmp_path,
) -> None:
    mock_broker.place_order.return_value = None

    orders_store = OrdersStore(tmp_path)
    orders_store.initialize()
    submitter = OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
        orders_store=orders_store,
    )

    result = submit_order_call(
        submitter,
        **MARKET_KWARGS,
        reduce_only=True,
        client_order_id="test-id",
    )

    assert result is None
    assert orders_store.get_pending_orders() == []
    record = orders_store.get_order("test-id")
    assert record is not None
    assert record.status in {StoreOrderStatus.REJECTED, StoreOrderStatus.FAILED}


def test_submit_order_idempotent_open_record_skips_broker(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
    submit_order_call,
    tmp_path,
) -> None:
    orders_store = OrdersStore(tmp_path)
    orders_store.initialize()
    record = OrderRecord(
        order_id="order-open-1",
        client_order_id="idempotent-open-1",
        symbol="BTC-USD",
        side="buy",
        order_type="market",
        quantity=Decimal("1.0"),
        price=None,
        status=StoreOrderStatus.OPEN,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        created_at=utc_now(),
        updated_at=utc_now(),
        bot_id="test-bot",
        time_in_force="GTC",
        metadata=None,
    )
    orders_store.upsert_by_client_id(record)

    submitter = OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
        orders_store=orders_store,
    )

    result = submit_order_call(
        submitter,
        symbol="BTC-USD",
        **MARKET_KWARGS,
        client_order_id="idempotent-open-1",
    )

    assert result == "order-open-1"
    assert open_orders == ["order-open-1"]
    mock_broker.place_order.assert_not_called()


def test_submit_order_idempotent_terminal_record_skips_broker(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
    submit_order_call,
    emit_metric_mock: MagicMock,
    tmp_path,
) -> None:
    orders_store = OrdersStore(tmp_path)
    orders_store.initialize()
    record = OrderRecord(
        order_id="order-cancelled-1",
        client_order_id="idempotent-cancelled-1",
        symbol="BTC-USD",
        side="buy",
        order_type="market",
        quantity=Decimal("1.0"),
        price=None,
        status=StoreOrderStatus.CANCELLED,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        created_at=utc_now(),
        updated_at=utc_now(),
        bot_id="test-bot",
        time_in_force="GTC",
        metadata=None,
    )
    orders_store.upsert_by_client_id(record)

    submitter = OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot",
        open_orders=open_orders,
        orders_store=orders_store,
    )

    result = submit_order_call(
        submitter,
        symbol="BTC-USD",
        **MARKET_KWARGS,
        client_order_id="idempotent-cancelled-1",
    )

    assert result is None
    assert open_orders == []
    mock_broker.place_order.assert_not_called()


def test_transient_failure_then_success_reuses_client_order_id(
    flow_submitter: OrderSubmitter,
    submit_order_call,
    mock_broker: MagicMock,
    open_orders: list[str],
) -> None:
    captured_client_ids: list[str] = []
    call_count = [0]

    def capture_and_respond(**kwargs) -> Order:
        captured_client_ids.append(kwargs.get("client_id", ""))
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("transient network error")
        return Order(
            id="order-success-123",
            client_id=kwargs.get("client_id", ""),
            symbol=kwargs.get("symbol", "BTC-USD"),
            side=kwargs.get("side", OrderSide.BUY),
            type=kwargs.get("order_type", OrderType.MARKET),
            quantity=kwargs.get("quantity", Decimal("1.0")),
            price=None,
            stop_price=None,
            tif=TimeInForce.GTC,
            status=OrderStatus.PENDING,
            submitted_at=utc_now(),
            updated_at=utc_now(),
        )

    mock_broker.place_order = capture_and_respond
    fixed_client_id = "idempotent-order-abc123"

    result1 = submit_order_call(
        flow_submitter,
        symbol="BTC-USD",
        **MARKET_KWARGS,
        client_order_id=fixed_client_id,
    )
    assert result1 is None

    result2 = submit_order_call(
        flow_submitter,
        symbol="BTC-USD",
        **MARKET_KWARGS,
        client_order_id=fixed_client_id,
    )

    assert result2 == "order-success-123"
    assert captured_client_ids == [fixed_client_id, fixed_client_id]
    assert open_orders == ["order-success-123"]


def test_retry_client_order_id_behavior(
    flow_submitter: OrderSubmitter,
    submit_order_call,
    mock_broker: MagicMock,
    monitoring_logger: MagicMock,
) -> None:
    captured_client_ids: list[str] = []

    def capture_client_id(**kwargs):
        captured_client_ids.append(kwargs.get("client_id", ""))
        raise RuntimeError("Simulated transient error")

    mock_broker.place_order.side_effect = capture_client_id

    submit_order_call(
        flow_submitter,
        symbol="BTC-USD",
        **MARKET_KWARGS,
        client_order_id="retry-test-123",
    )
    submit_order_call(
        flow_submitter,
        symbol="BTC-USD",
        **MARKET_KWARGS,
        client_order_id="retry-test-123",
    )

    assert captured_client_ids == ["retry-test-123", "retry-test-123"]
