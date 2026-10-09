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
            open=Decimal("1"),
            high=Decimal("1.2"),
            low=Decimal("0.8"),
            close=Decimal("1"),
            volume=Decimal("1000"),
        ),
    )
    return broker


def test_guard_rejection_records_decision(application_container, broker) -> None:
    decision_logger = DecisionLogger()
    executor = BacktestGuardedExecutor(
        BacktestExecutionContext(
            config=application_container.config,
            broker=broker,
            risk_manager=application_container.risk_manager,
            event_store=application_container.event_store,
            decision_logger=decision_logger,
        )
    )

    result = executor.submit_order(
        symbol="BTC-USD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("1"),
    )

    assert result.blocked is True
    assert result.reason is not None and "min_notional" in result.reason

    decisions = decision_logger.get_decisions()
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.risk_checks_passed is False
    assert "exchange_rules" in decision.risk_check_failures
