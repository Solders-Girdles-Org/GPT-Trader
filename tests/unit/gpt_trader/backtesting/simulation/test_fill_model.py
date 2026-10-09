"""Tests for FillResult, volume threshold, slippage, spread impact, and market fills."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.unit.gpt_trader.backtesting.simulation.fill_model_test_utils import (  # naming: allow
    make_order,  # naming: allow
)

from gpt_trader.backtesting.simulation.fill_model import FillResult, OrderFillModel
from gpt_trader.core import Candle, OrderSide, OrderType


class TestFillResult:
    """Test FillResult dataclass."""

    def test_unfilled_result(self) -> None:
        """Test creating an unfilled result."""
        result = FillResult(filled=False, reason="Price not touched")
        assert result.filled is False
        assert result.fill_price is None
        assert result.reason == "Price not touched"

    def test_filled_result(self) -> None:
        """Test creating a filled result."""
        result = FillResult(
            filled=True,
            fill_price=Decimal("50100"),
            fill_quantity=Decimal("1"),
            fill_time=datetime(2024, 1, 1, tzinfo=UTC),
            is_maker=False,
            slippage_bps=Decimal("2"),
        )
        assert result.filled is True
        assert result.fill_price == Decimal("50100")
        assert result.is_maker is False


class TestVolumeThreshold:
    """Test volume threshold logic."""

    @pytest.mark.parametrize(
        ("bar_volume", "expected"),
        [
            (Decimal("25"), True),  # 25 >= 10 * 2
            (Decimal("20"), True),  # 20 == 10 * 2
            (Decimal("15"), False),  # 15 < 10 * 2
        ],
    )
    def test_volume_threshold(self, bar_volume: Decimal, expected: bool) -> None:
        """Test volume is sufficient when bar_volume >= order_size * threshold."""
        model = OrderFillModel(limit_volume_threshold=Decimal("2.0"))
        sufficient = model._has_sufficient_volume(
            order_size=Decimal("10"),
            bar_volume=bar_volume,
        )
        assert sufficient is expected


class TestSlippageCalculation:
    """Test slippage calculation for different symbols."""

    @pytest.mark.parametrize(
        ("symbol", "expected", "slippage_bps"),
        [
            ("BTC-USD", Decimal("2"), None),
            ("ETH-USD", Decimal("2"), None),
            ("DOGE-USD", Decimal("5"), None),
            ("BTC-PERP-USDC", Decimal("2"), None),
            ("CUSTOM-USD", Decimal("10"), {"CUSTOM-USD": Decimal("10")}),
        ],
    )
    def test_slippage_defaults_and_overrides(
        self,
        symbol: str,
        expected: Decimal,
        slippage_bps: dict[str, Decimal] | None,
    ) -> None:
        """Test slippage defaults and custom overrides."""
        model = OrderFillModel(slippage_bps=slippage_bps or {})
        slippage = model._get_slippage(symbol)
        assert slippage == expected


class TestSpreadImpact:
    """Test spread impact on fill prices."""

    def test_zero_spread_impact(self) -> None:
        """Test no spread impact when spread_impact_pct is 0."""
        model = OrderFillModel(spread_impact_pct=Decimal("0"))
        bar = Candle(
            ts=datetime.now(UTC),
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("102"),
            volume=Decimal("1000"),
        )
        order = make_order(side=OrderSide.BUY)

        result = model.fill_market_order(
            order=order,
            current_bar=bar,
            best_bid=Decimal("100"),
            best_ask=Decimal("102"),  # 2 spread
            next_bar=None,
        )

        # With 0 spread impact, only slippage applies
        assert result.filled is True

    def test_full_spread_impact(self) -> None:
        """Test full spread impact when spread_impact_pct is 1."""
        model = OrderFillModel(spread_impact_pct=Decimal("1.0"), slippage_bps={})
        bar = Candle(
            ts=datetime.now(UTC),
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )
        order = make_order(side=OrderSide.BUY, symbol="UNKNOWN")

        # With full spread impact, buy should pay half the spread more
        result = model.fill_market_order(
            order=order,
            current_bar=bar,
            best_bid=Decimal("99"),
            best_ask=Decimal("101"),  # 2 spread
            next_bar=None,
        )

        assert result.filled is True
        # Fill price should include spread impact + slippage
        assert result.fill_price > Decimal("100")


class TestMarketOrderFill:
    """Test market order fill simulation."""

    def test_buy_market_order_fill(
        self, fill_model: OrderFillModel, current_bar: Candle, next_bar: Candle
    ) -> None:
        """Test buy market order fills at next bar open with slippage."""
        order = make_order(side=OrderSide.BUY, order_type=OrderType.MARKET)
        best_bid = Decimal("50100")
        best_ask = Decimal("50150")

        result = fill_model.fill_market_order(
            order=order,
            current_bar=current_bar,
            best_bid=best_bid,
            best_ask=best_ask,
            next_bar=next_bar,
        )

        assert result.filled is True
        assert result.fill_quantity == Decimal("1")
        assert result.is_maker is False  # Market orders are always taker
        assert result.fill_time == next_bar.ts
        assert result.fill_price > next_bar.open

    def test_sell_market_order_fill(
        self, fill_model: OrderFillModel, current_bar: Candle, next_bar: Candle
    ) -> None:
        """Test sell market order fills with slippage working against seller."""
        order = make_order(side=OrderSide.SELL, order_type=OrderType.MARKET)
        best_bid = Decimal("50100")
        best_ask = Decimal("50150")

        result = fill_model.fill_market_order(
            order=order,
            current_bar=current_bar,
            best_bid=best_bid,
            best_ask=best_ask,
            next_bar=next_bar,
        )

        assert result.filled is True
        assert result.is_maker is False
        assert result.fill_price < next_bar.open

    def test_market_order_without_next_bar(
        self, fill_model: OrderFillModel, current_bar: Candle
    ) -> None:
        """Test market order uses current bar close when no next bar."""
        order = make_order(side=OrderSide.BUY, order_type=OrderType.MARKET)
        best_bid = Decimal("50100")
        best_ask = Decimal("50150")

        result = fill_model.fill_market_order(
            order=order,
            current_bar=current_bar,
            best_bid=best_bid,
            best_ask=best_ask,
            next_bar=None,
        )

        assert result.filled is True
        assert result.fill_time == current_bar.ts
        assert result.fill_price > current_bar.close

    def test_market_order_slippage_recorded(
        self, fill_model: OrderFillModel, current_bar: Candle, next_bar: Candle
    ) -> None:
        """Test that slippage is recorded in basis points."""
        order = make_order(side=OrderSide.BUY, order_type=OrderType.MARKET)
        best_bid = Decimal("50100")
        best_ask = Decimal("50150")

        result = fill_model.fill_market_order(
            order=order,
            current_bar=current_bar,
            best_bid=best_bid,
            best_ask=best_ask,
            next_bar=next_bar,
        )

        assert result.slippage_bps is not None
        assert result.slippage_bps >= Decimal("0")
