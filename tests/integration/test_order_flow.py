"""Integration tests for order flow validation.

Tests the full path from order submission through event recording,
with DeterministicBroker for deterministic behavior without network calls.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, cast

import pytest

from gpt_trader.core import OrderSide

pytestmark = pytest.mark.integration


@pytest.fixture
def trading_bot(integration_container):
    """Create a TradingBot via container for integration testing."""
    bot = integration_container.create_bot()

    if bot.risk_manager:
        bot.risk_manager.set_reduce_only_mode(False, reason="fixture_setup")
        bot.risk_manager.last_mark_update["BTC-USD"] = time.time()

    yield bot

    if bot.risk_manager:
        bot.risk_manager.set_reduce_only_mode(False, reason="fixture_cleanup")


# =============================================================================
# TradingEngine submit_order tests (Order Submission Path)
# =============================================================================


class TestTradingEngineOrderFlow:
    """Tests for TradingEngine submit_order path."""

    @pytest.mark.asyncio
    async def test_happy_path_creates_trade_event(self, trading_bot) -> None:
        """Test that successful order placement creates a trade event."""
        engine = trading_bot.engine

        result = await engine.submit_order(
            symbol="BTC-USD",
            side=OrderSide.BUY,
            price=Decimal("50000"),
            equity=Decimal("100000"),
            quantity_override=Decimal("0.01"),
        )
        assert result.success is True

        events = engine.context.event_store.list_events()
        trade_events = [e for e in events if e["type"] == "trade"]
        assert len(trade_events) >= 1, "Expected at least one trade event"

        latest_trade = trade_events[-1]
        trade_data = latest_trade["data"]
        assert trade_data.get("symbol") == "BTC-USD"
        assert trade_data.get("side") == "BUY"
        assert "order_id" in trade_data

        trace_events = [e for e in events if e["type"] == "order_decision_trace"]
        assert len(trace_events) >= 1, "Expected at least one decision trace event"
        trace_data = trace_events[-1]["data"]
        assert trace_data.get("symbol") == "BTC-USD"
        assert trace_data.get("side") == "BUY"
        assert trace_data.get("client_order_id")
        assert trace_data.get("order_id") == trade_data.get("order_id")

    @pytest.mark.asyncio
    async def test_multiple_orders_record_multiple_events(self, trading_bot) -> None:
        """Test that multiple successful orders each create trade events."""
        engine = trading_bot.engine

        first = await engine.submit_order(
            symbol="BTC-USD",
            side=OrderSide.BUY,
            price=Decimal("50000"),
            equity=Decimal("100000"),
            quantity_override=Decimal("0.01"),
        )
        assert first.success is True

        second = await engine.submit_order(
            symbol="BTC-USD",
            side=OrderSide.SELL,
            price=Decimal("50000"),
            equity=Decimal("100000"),
            quantity_override=Decimal("0.01"),
        )
        assert second.success is True

        events = engine.context.event_store.list_events()
        trade_events = [e for e in events if e["type"] == "trade"]
        assert len(trade_events) >= 2, "Expected at least two trade events"

        sides = [e["data"].get("side") for e in trade_events[-2:]]
        assert "BUY" in sides and "SELL" in sides


# =============================================================================
# TradingEngine guard stack + degradation path
# =============================================================================


class TestTradingEngineGuardStack:
    """Tests for TradingEngine guard stack and degradation handling."""

    @pytest.mark.asyncio
    async def test_reduce_only_blocks_new_position(self, trading_bot) -> None:
        """Test that reduce-only mode blocks new position entries."""
        engine = trading_bot.engine

        engine.context.risk_manager.set_reduce_only_mode(True, reason="integration_test")

        initial_events = len(engine.context.event_store.list_events())

        from gpt_trader.features.live_trade.strategies.baseline import Action, Decision

        buy_decision = Decision(action=Action.BUY, reason="Test signal", confidence=0.8)

        result = await engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=buy_decision,
            price=Decimal("50000"),
            equity=Decimal("100000"),
        )
        assert result.blocked is True

        events = engine.context.event_store.list_events()
        new_events = events[initial_events:]
        trade_events = [e for e in new_events if e["type"] == "trade"]
        assert len(trade_events) == 0, "Should not have recorded any trades"

    @pytest.mark.asyncio
    async def test_mark_staleness_blocks_order(self, trading_bot) -> None:
        """Test that stale mark price blocks order and triggers symbol pause."""
        engine = trading_bot.engine

        risk_manager = engine.context.risk_manager
        stale_time = time.time() - 300
        risk_manager.last_mark_update["BTC-USD"] = stale_time

        from gpt_trader.features.live_trade.strategies.baseline import Action, Decision

        buy_decision = Decision(action=Action.BUY, reason="Test signal", confidence=0.8)

        initial_events = len(engine.context.event_store.list_events())

        result = await engine._validate_and_place_order(
            symbol="BTC-USD",
            decision=buy_decision,
            price=Decimal("50000"),
            equity=Decimal("100000"),
        )
        assert result.blocked is True
        assert result.reason is not None
        assert "stale" in result.reason.lower() or "mark" in result.reason.lower()

        events = engine.context.event_store.list_events()
        new_events = events[initial_events:]
        rejection_events = [
            e
            for e in new_events
            if e["type"] == "error"
            or (
                e["type"] == "metric"
                and e["data"].get("metrics", {}).get("event_type") == "order_rejected"
            )
        ]

        assert len(rejection_events) > 0, "Expected rejection event"

        risk_manager.last_mark_update["BTC-USD"] = time.time()


# =============================================================================
# Metrics Verification (Optional)
# =============================================================================


class TestOrderFlowMetrics:
    """Optional tests for order flow metrics integration."""

    @pytest.mark.asyncio
    async def test_order_submission_metric_incremented_on_success(self, trading_bot) -> None:
        """Test that gpt_trader_order_submission_total increments on success."""
        from gpt_trader.monitoring.metrics_collector import get_metrics_collector

        collector = get_metrics_collector()

        initial_summary = cast(dict[str, Any], collector.get_metrics_summary())
        initial_counters = cast(dict[str, int], initial_summary.get("counters", {}))

        result = await trading_bot.engine.submit_order(
            symbol="BTC-USD",
            side=OrderSide.BUY,
            price=Decimal("50000"),
            equity=Decimal("100000"),
            quantity_override=Decimal("0.01"),
        )
        assert result.success is True

        final_summary = cast(dict[str, Any], collector.get_metrics_summary())
        final_counters = cast(dict[str, int], final_summary.get("counters", {}))

        success_key = None
        for key in final_counters:
            if "order_submission_total" in key and "result=success" in key:
                success_key = key
                break

        if success_key:
            initial_value = initial_counters.get(success_key, 0)
            final_value = final_counters.get(success_key, 0)
            assert (
                final_value > initial_value
            ), f"Expected counter to increment, was {initial_value} -> {final_value}"
