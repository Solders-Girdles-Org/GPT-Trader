from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
from gpt_trader.features.live_trade.engines.strategy import TradingEngine
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision


@pytest.fixture
def mock_context():
    context = MagicMock()
    context.config.symbols = ["BTC-USD"]
    context.config.interval = 0.1
    context.config.strategy_type = "baseline"  # Required for factory
    context.config.max_concurrent_rest_calls = 5  # Required for semaphore init
    context.broker.get_ticker.return_value = {"price": "50000.00"}
    context.risk_manager._start_of_day_equity = Decimal("1000.0")
    return context


@pytest.fixture
def application_container():
    """Set up application container for TradingEngine tests."""
    config = BotConfig(symbols=["BTC-USD"], interval=1)
    container = ApplicationContainer(config)
    set_application_container(container)
    yield container
    clear_application_container()


@pytest.mark.asyncio
async def test_trading_engine_initialization(mock_context, application_container):
    engine = TradingEngine(mock_context)
    assert engine.running is False
    assert engine.strategy is not None
    assert engine.price_history is not None


@pytest.mark.asyncio
async def test_trading_engine_cycle(mock_context, application_container):
    engine = TradingEngine(mock_context)

    # Mock the strategy to return a specific decision
    engine.strategy.decide = MagicMock(return_value=Decision(Action.BUY, "Test Buy"))

    await engine._cycle()

    # Verify broker call
    mock_context.broker.get_ticker.assert_called_with("BTC-USD")

    # Verify strategy call
    engine.strategy.decide.assert_called_once()
    call_args = engine.strategy.decide.call_args
    assert call_args.kwargs["symbol"] == "BTC-USD"
    assert call_args.kwargs["current_mark"] == Decimal("50000.00")

    # Verify price history update
    assert len(engine.price_history["BTC-USD"]) == 1
    assert engine.price_history["BTC-USD"][0] == Decimal("50000.00")


@pytest.mark.asyncio
async def test_trading_engine_cycle_history_limit(mock_context, application_container):
    engine = TradingEngine(mock_context)
    engine.strategy.decide = MagicMock(return_value=Decision(Action.HOLD, "Hold"))

    # Fill history with 25 items
    for i in range(25):
        mock_context.broker.get_ticker.return_value = {"price": f"{50000 + i}"}
        await engine._cycle()

    assert len(engine.price_history["BTC-USD"]) == 20
    assert engine.price_history["BTC-USD"][-1] == Decimal("50024")
