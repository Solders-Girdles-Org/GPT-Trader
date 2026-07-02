"""Tests for TradingEngine state: equity, positions, daily tracking, reduce-only."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import gpt_trader.security.validate as security_validate_module
from gpt_trader.core import Balance, OrderSide, OrderType, Position
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision


def test_reset_daily_tracking_recomputes_equity(engine):
    """reset_daily_tracking recomputes equity and invalidates guard cache."""
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("1000"), available=Decimal("1000"))
    ]
    engine._state_collector.calculate_equity_from_balances.return_value = (
        Decimal("1000"),
        [],
        Decimal("1000"),
    )
    engine.context.risk_manager.reset_daily_tracking = MagicMock()
    engine._guard_manager = MagicMock()

    engine.reset_daily_tracking()

    engine.context.broker.list_balances.assert_called_once()
    engine._state_collector.calculate_equity_from_balances.assert_called_once()
    engine.context.risk_manager.reset_daily_tracking.assert_called_once()
    engine._guard_manager.invalidate_cache.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_total_equity_success(engine):
    """Test successful equity fetch including non-USD assets valued in USD."""
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("1000"), available=Decimal("800")),
        Balance(asset="USDC", total=Decimal("500"), available=Decimal("200")),
        Balance(asset="BTC", total=Decimal("1"), available=Decimal("1")),
    ]
    positions = {}

    equity = await engine._fetch_total_equity(positions)
    assert equity == Decimal("51000")


@pytest.mark.asyncio
async def test_fetch_total_equity_includes_unrealized_pnl(engine):
    """Test equity fetch includes unrealized PnL from positions."""
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("1000"), available=Decimal("1000")),
    ]
    positions = {
        "BTC-USD": Position(
            symbol="BTC-USD",
            quantity=Decimal("1"),
            entry_price=Decimal("0"),
            mark_price=Decimal("0"),
            unrealized_pnl=Decimal("500"),
            realized_pnl=Decimal("0"),
            side="long",
        ),
        "ETH-USD": Position(
            symbol="ETH-USD",
            quantity=Decimal("10"),
            entry_price=Decimal("0"),
            mark_price=Decimal("0"),
            unrealized_pnl=Decimal("-200"),
            realized_pnl=Decimal("0"),
            side="short",
        ),
    }

    equity = await engine._fetch_total_equity(positions)
    assert equity == Decimal("1300")


@pytest.mark.asyncio
async def test_fetch_total_equity_failure(engine):
    """Test equity fetch handles broker errors gracefully."""
    engine.context.broker.list_balances.side_effect = Exception("API Error")
    equity = await engine._fetch_total_equity({})
    assert equity is None


@pytest.mark.asyncio
async def test_fetch_positions_success(engine):
    """Test successful position fetch converting to dict."""
    positions = await engine._fetch_positions()
    assert "BTC-USD" in positions
    assert positions["BTC-USD"].quantity == Decimal("0.5")


@pytest.mark.asyncio
async def test_fetch_positions_failure(engine):
    """Test position fetch handles broker errors gracefully."""
    engine.context.broker.list_positions.side_effect = Exception("API Error")
    positions = await engine._fetch_positions()
    assert positions == {}


@pytest.mark.asyncio
async def test_cycle_skips_on_equity_failure(engine):
    """Test cycle aborts if equity cannot be fetched."""
    engine.context.broker.list_balances.side_effect = Exception("API Error")
    await engine._cycle()
    engine.strategy.decide.assert_not_called()


def test_build_position_state(engine):
    """Test position state formatting."""
    positions = {
        "BTC-USD": Position(
            symbol="BTC-USD",
            quantity=Decimal("0.5"),
            entry_price=Decimal("40000"),
            mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("5000"),
            realized_pnl=Decimal("0"),
            side="long",
        )
    }

    state = engine._build_position_state("BTC-USD", positions)
    assert state["quantity"] == Decimal("0.5")
    assert state["entry_price"] == Decimal("40000")
    assert state["side"] == "long"
    assert state["product_type"] == "SPOT"

    state_none = engine._build_position_state("ETH-USD", positions)
    assert state_none is None


@pytest.mark.asyncio
async def test_position_state_passed_to_strategy(engine):
    """Verify strategy receives correct position state."""
    engine.context.broker.list_positions.return_value = [
        Position(
            symbol="BTC-USD",
            quantity=Decimal("1.0"),
            entry_price=Decimal("45000"),
            mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("5000"),
            realized_pnl=Decimal("0"),
            side="long",
        )
    ]
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("10000"), available=Decimal("10000"))
    ]

    await engine._cycle()

    call_args = engine.strategy.decide.call_args
    position_state = call_args.kwargs["position_state"]

    assert position_state is not None
    assert position_state["quantity"] == Decimal("1.0")
    assert position_state["entry_price"] == Decimal("45000")
    assert position_state["side"] == "long"
    assert position_state["product_type"] == "SPOT"


@pytest.mark.asyncio
async def test_reduce_only_clamps_quantity_to_prevent_position_flip(
    engine, monkeypatch: pytest.MonkeyPatch
):
    """Test that reduce-only mode clamps order quantity to prevent position flip."""
    engine.strategy.decide.return_value = Decision(Action.SELL, "test")
    engine.strategy.config.position_fraction = Decimal("0.2")
    engine.context.broker.get_ticker.return_value = {"price": "50000"}
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("500000"), available=Decimal("500000"))
    ]
    engine.context.broker.list_positions.return_value = [
        Position(
            symbol="BTC-USD",
            quantity=Decimal("1.0"),
            entry_price=Decimal("40000"),
            mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("10000"),
            realized_pnl=Decimal("0"),
            side="long",
        )
    ]

    engine.context.risk_manager._reduce_only_mode = True
    engine.context.risk_manager._daily_pnl_triggered = False
    engine.context.risk_manager.check_order.return_value = True

    engine._order_validator.validate_exchange_rules.side_effect = lambda **kw: (
        kw.get("order_quantity"),
        None,
    )

    mock_validator = MagicMock()
    mock_validator.validate_order_request.return_value.is_valid = True
    monkeypatch.setattr(security_validate_module, "get_validator", lambda: mock_validator)

    await engine._cycle()

    engine._order_submitter.submit_order_with_result.assert_called_once()
    call_kwargs = engine._order_submitter.submit_order_with_result.call_args[1]
    assert call_kwargs["symbol"] == "BTC-USD"
    assert call_kwargs["side"] == OrderSide.SELL
    assert call_kwargs["order_type"] == OrderType.MARKET
    assert call_kwargs["order_quantity"] == Decimal("1.0")


@pytest.mark.asyncio
async def test_reduce_only_blocks_new_position_on_empty_symbol(
    engine, monkeypatch: pytest.MonkeyPatch
):
    """Test that reduce-only mode blocks orders for symbols with no position."""
    engine.strategy.decide.return_value = Decision(Action.BUY, "test")
    engine.strategy.config.position_fraction = Decimal("0.1")
    engine.context.broker.get_ticker.return_value = {"price": "50000"}
    engine.context.broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("10000"), available=Decimal("10000"))
    ]
    engine.context.broker.list_positions.return_value = []

    engine.context.risk_manager._reduce_only_mode = True
    engine.context.risk_manager._daily_pnl_triggered = False
    engine.context.risk_manager.check_order.return_value = False

    mock_validator = MagicMock()
    mock_validator.validate_order_request.return_value.is_valid = True
    monkeypatch.setattr(security_validate_module, "get_validator", lambda: mock_validator)

    await engine._cycle()

    engine.context.broker.place_order.assert_not_called()
