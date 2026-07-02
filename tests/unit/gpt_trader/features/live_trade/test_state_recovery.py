"""Tests for TradingEngine state recovery behaviors."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.engines.strategy import (
    EVENT_PRICE_TICK,
    TradingEngine,
)
from gpt_trader.features.live_trade.strategies.baseline import BaselinePerpsStrategy

pytest_plugins = ["tests.unit.gpt_trader.features.live_trade.state_recovery_test_helpers"]


class TestTradingEngineRecordPriceTick:
    """Tests for TradingEngine price tick recording."""

    def test_record_price_tick_stores_event(
        self,
        context_with_store: CoordinatorContext,
        mock_event_store: MagicMock,
        application_container,
    ) -> None:
        """Test that price ticks are recorded to event store."""
        engine = TradingEngine(context_with_store)

        engine._record_price_tick("BTC-PERP", Decimal("50000.00"))

        mock_event_store.store.assert_called_once()
        call_args = mock_event_store.store.call_args[0][0]
        assert call_args["type"] == EVENT_PRICE_TICK
        assert call_args["data"]["symbol"] == "BTC-PERP"
        assert call_args["data"]["price"] == "50000.00"
        assert call_args["data"]["bot_id"] == "test-bot"
        assert "timestamp" in call_args["data"]

    def test_record_price_tick_without_store(
        self, context_without_store: CoordinatorContext, application_container
    ) -> None:
        """Test that price tick recording is skipped without event store."""
        engine = TradingEngine(context_without_store)

        # Should not raise
        engine._record_price_tick("BTC-PERP", Decimal("50000.00"))
        assert context_without_store.event_store is None


class TestStrategyRehydrate:
    """Tests for strategy rehydrate method."""

    def test_baseline_strategy_rehydrate_returns_zero(self) -> None:
        """Test that BaselinePerpsStrategy.rehydrate returns 0 (stateless)."""
        strategy = BaselinePerpsStrategy()

        events = [
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "50000.00"}},
        ]
        result = strategy.rehydrate(events)

        assert result == 0
