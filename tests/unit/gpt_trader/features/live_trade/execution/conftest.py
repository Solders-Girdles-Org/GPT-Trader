from __future__ import annotations

import time
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

import gpt_trader.features.live_trade.execution.order_event_recorder as recorder_module
from gpt_trader.core import (
    MarketType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Product,
    TimeInForce,
)
from gpt_trader.features.live_trade.execution.broker_executor import BrokerExecutor
from gpt_trader.features.live_trade.execution.guard_manager import GuardManager
from gpt_trader.features.live_trade.execution.guards import RuntimeGuardState
from gpt_trader.features.live_trade.execution.order_event_recorder import OrderEventRecorder
from gpt_trader.features.live_trade.execution.order_submission import OrderSubmitter
from gpt_trader.features.live_trade.execution.state_collection import StateCollector
from gpt_trader.features.live_trade.execution.validation import OrderValidator
from gpt_trader.utilities.datetime_helpers import utc_now


@pytest.fixture
def mock_broker() -> MagicMock:
    """Create a mock broker with execution defaults."""
    broker = MagicMock()
    broker.place_order = MagicMock()
    broker.list_balances.return_value = []
    broker.list_positions.return_value = []
    broker.cancel_order.return_value = True
    broker.list_orders = None
    broker.get_market_snapshot.return_value = None
    broker.get_product.return_value = None
    return broker


@pytest.fixture
def mock_config(bot_config_factory):
    """Create mock BotConfig for state collection tests."""
    return bot_config_factory()


@pytest.fixture
def collector(mock_broker: MagicMock, mock_config, monkeypatch) -> StateCollector:
    """Create a StateCollector instance."""
    monkeypatch.setenv("PERPS_COLLATERAL_ASSETS", "USD,USDC")
    return StateCollector(mock_broker, mock_config)


@pytest.fixture
def executor(mock_broker: MagicMock) -> BrokerExecutor:
    """Create a BrokerExecutor instance."""
    return BrokerExecutor(broker=mock_broker)


@pytest.fixture
def sample_order() -> Order:
    """Create a sample order response."""
    return Order(
        id="order-123",
        client_id="client-123",
        symbol="BTC-USD",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal("1.0"),
        price=Decimal("50000"),
        stop_price=None,
        tif=TimeInForce.GTC,
        status=OrderStatus.PENDING,
        submitted_at=utc_now(),
        updated_at=utc_now(),
    )


@pytest.fixture
def order_event_recorder(mock_event_store: MagicMock) -> OrderEventRecorder:
    """Create an OrderEventRecorder instance."""
    return OrderEventRecorder(event_store=mock_event_store, bot_id="test-bot-123")


@pytest.fixture
def order_event_mock_order() -> MagicMock:
    """Create a mock order object for OrderEventRecorder tests."""
    order = MagicMock()
    order.id = "order-123"
    order.client_order_id = "client-123"
    order.quantity = Decimal("1.0")
    order.price = Decimal("50000")
    order.status = "SUBMITTED"
    return order


@pytest.fixture
def mock_event_store() -> MagicMock:
    """Create a mock event store."""
    store = MagicMock()
    store.store_event = MagicMock()
    store.append_trade = MagicMock()
    store.append_error = MagicMock()
    return store


@pytest.fixture
def monitoring_logger(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_logger = MagicMock()
    monkeypatch.setattr(recorder_module, "get_monitoring_logger", lambda: mock_logger)
    return mock_logger


@pytest.fixture
def emit_metric_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_emit = MagicMock()
    monkeypatch.setattr(recorder_module, "emit_metric", mock_emit)
    return mock_emit


@pytest.fixture
def mock_risk_manager() -> MagicMock:
    """Create a mock risk manager with default guard settings."""
    rm = MagicMock()
    rm.track_daily_pnl.return_value = False
    rm.last_mark_update = {}
    rm.config = MagicMock()
    rm.config.volatility_window_periods = 20
    rm.config.slippage_guard_bps = 100
    rm.check_mark_staleness.return_value = False
    rm.is_reduce_only_mode.return_value = False
    rm.pre_trade_validate = MagicMock()
    rm.append_risk_metrics = MagicMock()
    return rm


@pytest.fixture
def mock_equity_calculator() -> MagicMock:
    """Create a mock equity calculator returning a stable equity tuple."""
    return MagicMock(return_value=(Decimal("1000"), [], Decimal("1000")))


@pytest.fixture
def guard_manager(
    mock_broker: MagicMock, mock_risk_manager: MagicMock, mock_equity_calculator: MagicMock
) -> GuardManager:
    open_orders = ["order1", "order2"]
    invalidate_cache = MagicMock()
    return GuardManager(
        broker=mock_broker,
        risk_manager=mock_risk_manager,
        equity_calculator=mock_equity_calculator,
        open_orders=open_orders,
        invalidate_cache_callback=invalidate_cache,
    )


@pytest.fixture
def disable_api_health_guard(guard_manager: GuardManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ApiHealthGuard a no-op so full manager runs work against the bare broker mock.

    The bare MagicMock broker exposes a ``client`` whose resilience status is itself a
    mock, which the guard would read as a breached error rate.
    """
    api_guard = next(guard for guard in guard_manager._guards if guard.name == "api_health")
    monkeypatch.setattr(api_guard, "check", lambda state, incremental=False: None)


@pytest.fixture
def mock_position() -> MagicMock:
    pos = MagicMock()
    pos.symbol = "BTC-PERP"
    pos.entry_price = "50000"
    pos.mark_price = "51000"
    pos.quantity = "0.1"
    pos.side = "long"
    return pos


@pytest.fixture
def sample_guard_state(mock_position: MagicMock) -> RuntimeGuardState:
    return RuntimeGuardState(
        timestamp=time.time(),
        balances=[],
        equity=Decimal("10000"),
        positions=[mock_position],
        positions_pnl={
            "BTC-PERP": {"realized_pnl": Decimal("0"), "unrealized_pnl": Decimal("100")}
        },
        positions_dict={
            "BTC-PERP": {
                "quantity": Decimal("0.1"),
                "mark": Decimal("51000"),
                "entry": Decimal("50000"),
            }
        },
        guard_events=[],
    )


@pytest.fixture
def mock_product() -> Product:
    """Create a mock product."""
    return Product(
        symbol="BTC-PERP",
        base_asset="BTC",
        quote_asset="USD",
        market_type=MarketType.PERPETUAL,
        min_size=Decimal("0.001"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("10"),
        price_increment=Decimal("0.01"),
        leverage_max=20,
    )


@pytest.fixture
def mock_failure_tracker() -> MagicMock:
    """Create a mock failure tracker."""
    return MagicMock()


@pytest.fixture
def validator(
    mock_broker: MagicMock,
    mock_risk_manager: MagicMock,
    mock_failure_tracker: MagicMock,
) -> OrderValidator:
    record_preview = MagicMock()
    record_rejection = MagicMock()
    return OrderValidator(
        broker=mock_broker,
        risk_manager=mock_risk_manager,
        enable_order_preview=True,
        record_preview_callback=record_preview,
        record_rejection_callback=record_rejection,
        failure_tracker=mock_failure_tracker,
    )


@pytest.fixture
def open_orders() -> list[str]:
    """Create an open orders list."""
    return []


@pytest.fixture
def submitter(
    mock_broker: MagicMock,
    mock_event_store: MagicMock,
    open_orders: list[str],
) -> OrderSubmitter:
    """Create an OrderSubmitter instance."""
    return OrderSubmitter(
        broker=mock_broker,
        event_store=mock_event_store,
        bot_id="test-bot-123",
        open_orders=open_orders,
        integration_mode=False,
    )


@pytest.fixture
def submit_order_kwargs() -> dict[str, Any]:
    return {
        "symbol": "BTC-PERP",
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT,
        "order_quantity": Decimal("1.0"),
        "price": Decimal("50000"),
        "effective_price": Decimal("50000"),
        "stop_price": None,
        "tif": TimeInForce.GTC,
        "reduce_only": False,
        "leverage": 10,
        "client_order_id": "decision-123",
    }


@pytest.fixture
def submit_order_call(submit_order_kwargs):
    def _call(submitter: OrderSubmitter, **overrides):
        payload = {**submit_order_kwargs, **overrides}
        return submitter.submit_order(**payload)

    return _call


@pytest.fixture
def submit_order_with_result_call(submit_order_kwargs):
    def _call(submitter: OrderSubmitter, **overrides):
        payload = {**submit_order_kwargs, **overrides}
        return submitter.submit_order_with_result(**payload)

    return _call


@pytest.fixture
def mock_order() -> Order:
    """Create a mock successful order."""
    return Order(
        id="order-123",
        client_id="client-123",
        symbol="BTC-PERP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal("1.0"),
        price=Decimal("50000"),
        stop_price=None,
        tif=TimeInForce.GTC,
        status=OrderStatus.PENDING,
        submitted_at=utc_now(),
        updated_at=utc_now(),
    )


@pytest.fixture
def rejected_order() -> MagicMock:
    """Create a mock rejected order object."""
    order = MagicMock()
    order.id = "rejected-order"
    order.quantity = Decimal("1.0")
    order.filled_quantity = Decimal("0")
    order.price = Decimal("50000")
    order.side = OrderSide.BUY
    order.type = OrderType.LIMIT
    order.tif = TimeInForce.GTC
    return order
