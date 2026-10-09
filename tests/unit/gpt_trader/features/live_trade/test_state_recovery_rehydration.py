"""Tests for TradingEngine rehydration state recovery."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.engines.strategy import (
    EVENT_PRICE_TICK,
    TradingEngine,
)
from gpt_trader.persistence.orders_store import OrderRecord, OrdersStore, OrderStatus

pytest_plugins = ["tests.unit.gpt_trader.features.live_trade.state_recovery_test_helpers"]


class TestTradingEngineRehydration:
    """Tests for TradingEngine state recovery."""

    def test_rehydrate_from_empty_events(
        self,
        context_with_store: CoordinatorContext,
        mock_event_store: MagicMock,
        application_container,
    ) -> None:
        """Test rehydration with no events returns 0."""
        mock_event_store.get_recent.return_value = []
        engine = TradingEngine(context_with_store)

        restored = engine._rehydrate_from_events()

        assert restored == 0
        assert len(engine.price_history) == 0

    def test_rehydrate_from_price_ticks(
        self,
        context_with_store: CoordinatorContext,
        mock_event_store: MagicMock,
        application_container,
    ) -> None:
        """Test rehydration restores price history from price_tick events."""
        mock_event_store.get_recent.return_value = [
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "50000.00"}},
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "50100.00"}},
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "ETH-PERP", "price": "3000.00"}},
        ]
        engine = TradingEngine(context_with_store)

        restored = engine._rehydrate_from_events()

        assert restored == 3
        assert len(engine.price_history["BTC-PERP"]) == 2
        assert len(engine.price_history["ETH-PERP"]) == 1
        assert engine.price_history["BTC-PERP"][0] == Decimal("50000.00")
        assert engine.price_history["BTC-PERP"][1] == Decimal("50100.00")

    def test_rehydrate_ignores_unknown_symbols(
        self,
        context_with_store: CoordinatorContext,
        mock_event_store: MagicMock,
        application_container,
    ) -> None:
        """Test rehydration ignores events for symbols not in config."""
        mock_event_store.get_recent.return_value = [
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "50000.00"}},
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "UNKNOWN-PERP", "price": "100.00"}},
        ]
        engine = TradingEngine(context_with_store)

        restored = engine._rehydrate_from_events()

        assert restored == 1
        assert "UNKNOWN-PERP" not in engine.price_history
        assert len(engine.price_history["BTC-PERP"]) == 1

    def test_rehydrate_ignores_other_event_types(
        self,
        context_with_store: CoordinatorContext,
        mock_event_store: MagicMock,
        application_container,
    ) -> None:
        """Test rehydration ignores non-price_tick events."""
        mock_event_store.get_recent.return_value = [
            {"type": "trade", "data": {"symbol": "BTC-PERP", "price": "50000.00"}},
            {"type": "error", "data": {"message": "test error"}},
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "50100.00"}},
        ]
        engine = TradingEngine(context_with_store)

        restored = engine._rehydrate_from_events()

        assert restored == 1
        assert len(engine.price_history["BTC-PERP"]) == 1

    def test_rehydrate_bounds_history_to_20(
        self,
        context_with_store: CoordinatorContext,
        mock_event_store: MagicMock,
        application_container,
    ) -> None:
        """Test rehydration keeps at most 20 prices per symbol."""
        events = [
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": str(50000 + i)}}
            for i in range(30)
        ]
        mock_event_store.get_recent.return_value = events
        engine = TradingEngine(context_with_store)

        restored = engine._rehydrate_from_events()

        assert restored == 30
        # Should only keep last 20
        assert len(engine.price_history["BTC-PERP"]) == 20
        # First price should be 50010 (30 - 20 = 10, so 50000 + 10)
        assert engine.price_history["BTC-PERP"][0] == Decimal("50010")

    def test_rehydrate_without_event_store(
        self, context_without_store: CoordinatorContext, application_container
    ) -> None:
        """Test rehydration with no event store returns 0 gracefully."""
        engine = TradingEngine(context_without_store)

        restored = engine._rehydrate_from_events()

        assert restored == 0

    def test_rehydrate_handles_invalid_price(
        self,
        context_with_store: CoordinatorContext,
        mock_event_store: MagicMock,
        application_container,
    ) -> None:
        """Test rehydration handles invalid price values gracefully."""
        mock_event_store.get_recent.return_value = [
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "50000.00"}},
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "invalid"}},
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "50200.00"}},
        ]
        engine = TradingEngine(context_with_store)

        restored = engine._rehydrate_from_events()

        # Should restore 2 valid prices, skip invalid
        assert restored == 2
        assert len(engine.price_history["BTC-PERP"]) == 2

    def test_rehydrate_open_orders_from_store(
        self, bot_config, application_container, tmp_path
    ) -> None:
        orders_store = OrdersStore(tmp_path)
        orders_store.initialize()
        now = datetime.now(UTC)
        orders_store.save_order(
            OrderRecord(
                order_id="order-123",
                client_order_id="client-123",
                symbol="BTC-PERP",
                side="buy",
                order_type="market",
                quantity=Decimal("1"),
                price=None,
                status=OrderStatus.OPEN,
                filled_quantity=Decimal("0"),
                average_fill_price=None,
                created_at=now,
                updated_at=now,
                bot_id="test-bot",
                time_in_force="GTC",
                metadata=None,
            )
        )

        context = CoordinatorContext(
            config=bot_config,
            container=application_container,
            broker=MagicMock(),
            event_store=MagicMock(),
            orders_store=orders_store,
            bot_id="test-bot",
        )
        engine = TradingEngine(context)

        assert "order-123" in engine._open_orders

    def test_rehydrate_handles_missing_fields(
        self,
        context_with_store: CoordinatorContext,
        mock_event_store: MagicMock,
        application_container,
    ) -> None:
        """Test rehydration handles events with missing fields."""
        mock_event_store.get_recent.return_value = [
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP"}},  # missing price
            {"type": EVENT_PRICE_TICK, "data": {"price": "50000.00"}},  # missing symbol
            {"type": EVENT_PRICE_TICK, "data": {}},  # missing both
            {"type": EVENT_PRICE_TICK, "data": {"symbol": "BTC-PERP", "price": "50100.00"}},
        ]
        engine = TradingEngine(context_with_store)

        restored = engine._rehydrate_from_events()

        assert restored == 1
        assert len(engine.price_history["BTC-PERP"]) == 1
