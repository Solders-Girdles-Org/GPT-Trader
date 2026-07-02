from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_end_to_end_buy_execution(monkeypatch: pytest.MonkeyPatch):
    """Test that the bot correctly executes a BUY when strategy signals.

    This tests the wiring between TradingBot -> TradingEngine -> Broker,
    not the strategy logic itself. We mock the strategy decision to isolate
    the execution path.
    """
    # 1. Setup Config with mock broker
    config = BotConfig(
        symbols=["BTC-USD"],
        interval=0.01,  # Fast interval for testing
        mock_broker=True,  # Use deterministic broker
        perps_position_fraction=0.04,  # 4% position sizing (must be < 5% security limit)
    )

    # 2. Create container, register globally, and create bot
    container = ApplicationContainer(config)
    set_application_container(container)
    try:
        bot = container.create_bot()

        # Ensure clean risk manager state for isolated testing
        if bot.risk_manager:
            bot.risk_manager.set_reduce_only_mode(False, reason="test_setup")

        # 3. Mock strategy to return BUY decision on first cycle
        # This isolates testing of the execution wiring from the strategy logic
        buy_decision = Decision(action=Action.BUY, reason="Test signal", confidence=0.8)

        # Mock the broker's place_order to verify it gets called
        # Create a wrapper to track calls while preserving original behavior
        original_place_order = bot.broker.place_order
        mock_place = MagicMock(wraps=original_place_order)
        monkeypatch.setattr(bot.broker, "place_order", mock_place)
        monkeypatch.setattr(bot.engine.strategy, "decide", lambda *args, **kwargs: buy_decision)

        await bot.engine._cycle()

        # 4. Verify Order Placement
        mock_place.assert_called_once()
        call_kwargs = mock_place.call_args.kwargs
        assert call_kwargs["symbol"] == "BTC-USD"
        from gpt_trader.core import OrderSide

        assert call_kwargs["side"] == OrderSide.BUY
    finally:
        clear_application_container()
