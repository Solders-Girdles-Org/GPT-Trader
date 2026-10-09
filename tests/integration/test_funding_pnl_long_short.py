"""Integration tests for long/short funding PnL behavior."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from gpt_trader.backtesting.engine.bar_runner import FundingProcessor
from gpt_trader.backtesting.simulation.broker import SimulatedBroker
from gpt_trader.core import OrderSide, OrderType
from tests.integration.funding_pnl_test_base import SYMBOL, FundingPnLTestBase

pytestmark = pytest.mark.integration


class TestFundingPnLLongShort(FundingPnLTestBase):
    """Integration tests for funding PnL calculation."""

    def test_long_position_pays_positive_funding(
        self,
        broker: SimulatedBroker,
        funding_processor: FundingProcessor,
    ) -> None:
        """Long position pays funding when rate is positive."""
        start_time = datetime(2024, 1, 1, 0, 0, 0)
        broker._simulation_time = start_time
        self.seed_market_data(broker, start_time)

        order = broker.place_order(
            symbol=SYMBOL,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
        )
        assert order.status.value in ["FILLED", "filled", "PENDING", "pending"]

        positions = broker.list_positions()
        assert len(positions) == 1
        assert positions[0].symbol == SYMBOL
        assert positions[0].quantity > 0  # Long

        self.run_funding_hours(broker, funding_processor, start_time, hours=12)

        funding_pnl = broker.get_statistics()["funding_pnl"]
        assert funding_pnl > Decimal("0"), (
            f"Expected positive funding PnL (paid), got {funding_pnl}"
        )

    def test_short_position_receives_positive_funding(
        self,
        broker: SimulatedBroker,
        funding_processor: FundingProcessor,
    ) -> None:
        """Short position receives funding when rate is positive."""
        start_time = datetime(2024, 1, 1, 0, 0, 0)
        broker._simulation_time = start_time
        self.seed_market_data(broker, start_time)

        order = broker.place_order(
            symbol=SYMBOL,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
        )
        assert order.status.value in ["FILLED", "filled", "PENDING", "pending"]

        positions = broker.list_positions()
        assert len(positions) == 1
        assert positions[0].quantity < 0  # Short

        self.run_funding_hours(broker, funding_processor, start_time, hours=12)

        funding_pnl = broker.get_statistics()["funding_pnl"]
        assert funding_pnl < Decimal("0"), (
            f"Expected negative funding PnL (received), got {funding_pnl}"
        )
