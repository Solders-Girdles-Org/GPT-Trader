"""Configured storage failure must not turn a real engine into storeless mode."""

from decimal import Decimal
from unittest.mock import Mock

import pytest

from gpt_trader.features.live_trade.engines.strategy import TradingEngine
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision


@pytest.mark.asyncio
async def test_engine_preserves_configured_failed_store_and_refuses_order(
    real_flow_context,
    mock_strategy,
    monkeypatch,
):
    import gpt_trader.features.live_trade.engines.strategy as strategy_module

    store = real_flow_context.container.orders_store
    monkeypatch.setattr(store, "initialize", Mock(side_effect=OSError("unavailable SQLite")))
    monkeypatch.setattr(strategy_module, "create_strategy", Mock(return_value=mock_strategy))
    engine = TradingEngine(real_flow_context)
    assert engine._orders_store is store
    assert engine._order_submitter.orders_store is store
    await engine._handle_decision(
        symbol="BTC-USD",
        decision=Decision(Action.BUY, "storage failure regression", 0.82),
        price=Decimal("50000"),
        equity=Decimal("1000"),
        position_state=None,
    )
    real_flow_context.broker.place_order.assert_not_called()
