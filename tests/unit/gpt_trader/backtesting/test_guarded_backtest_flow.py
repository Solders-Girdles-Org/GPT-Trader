from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.backtesting.engine.guarded_execution import (
    BacktestExecutionContext,
    BacktestGuardedExecutor,
)
from gpt_trader.backtesting.simulation.broker import SimulatedBroker
from gpt_trader.backtesting.validation.decision_logger import DecisionLogger
from gpt_trader.core import Candle, MarketType, OrderSide, OrderType, Product


@pytest.fixture
def mock_config() -> BotConfig:
    config = BotConfig()
    config.symbols = ["BTC-USD"]
    config.enable_order_preview = False
    return config


@pytest.fixture
def broker() -> SimulatedBroker:
    broker = SimulatedBroker(initial_equity_usd=Decimal("1000"))
    broker.register_product(
        Product(
            symbol="BTC-USD",
            base_asset="BTC",
            quote_asset="USD",
            market_type=MarketType.SPOT,
            min_size=Decimal("0.01"),
            step_size=Decimal("0.01"),
            min_notional=Decimal("10"),
            price_increment=Decimal("0.01"),
            leverage_max=5,
        )
    )
    broker.update_bar(
        "BTC-USD",
        Candle(
            ts=datetime(2024, 1, 1, tzinfo=UTC),
            open=Decimal("10"),
            high=Decimal("10"),
            low=Decimal("10"),
            close=Decimal("10"),
            volume=Decimal("1000"),
        ),
    )
    return broker


def test_guarded_executor_rejects_and_accepts(application_container, broker) -> None:
    decision_logger = DecisionLogger()
    application_container.risk_manager.config.slippage_guard_bps = 100000
    executor = BacktestGuardedExecutor(
        BacktestExecutionContext(
            config=application_container.config,
            broker=broker,
            risk_manager=application_container.risk_manager,
            event_store=application_container.event_store,
            decision_logger=decision_logger,
        )
    )

    blocked = executor.submit_order(
        symbol="BTC-USD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("1"),
    )
    assert blocked.blocked is True

    accepted = executor.submit_order(
        symbol="BTC-USD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=Decimal("10"),
    )

    assert accepted.success is True
    assert accepted.order_id is not None
    assert accepted.order_id in broker._filled_orders
    assert decision_logger.decision_count == 2
