"""Property-based tests for quantization utilities.

Uses Hypothesis to verify mathematical invariants:
- Idempotence: quantize(quantize(x)) == quantize(x)
- Alignment: result is always on the increment grid
- Side consistency: BUY floors, SELL ceils
"""

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from gpt_trader.utilities.quantization import quantize_price_side_aware

# Custom strategy for reasonable trading prices and increments
reasonable_prices = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("1000000"),
    places=8,
    allow_nan=False,
    allow_infinity=False,
)

reasonable_increments = st.decimals(
    min_value=Decimal("0.000001"),
    max_value=Decimal("1000"),
    places=8,
    allow_nan=False,
    allow_infinity=False,
)

sides = st.sampled_from(["BUY", "SELL", "buy", "sell"])


class TestQuantizeIdempotence:
    """Test that quantization is idempotent."""

    @given(price=reasonable_prices, increment=reasonable_increments, side=sides)
    @settings(max_examples=200)
    def test_idempotence(self, price: Decimal, increment: Decimal, side: str) -> None:
        """Quantizing twice yields the same result as quantizing once.

        This ensures:
        quantize(quantize(x)) == quantize(x)
        """
        assume(increment > 0)

        first_pass = quantize_price_side_aware(price, increment, side)
        second_pass = quantize_price_side_aware(first_pass, increment, side)

        assert first_pass == second_pass, (
            f"Idempotence violated: "
            f"quantize({price}, {increment}, {side}) = {first_pass}, "
            f"quantize({first_pass}, {increment}, {side}) = {second_pass}"
        )


class TestQuantizeAlignment:
    """Test that quantization aligns to increment grid."""

    @given(price=reasonable_prices, increment=reasonable_increments, side=sides)
    @settings(max_examples=200)
    def test_result_on_grid(self, price: Decimal, increment: Decimal, side: str) -> None:
        """Result must be an exact multiple of the increment.

        This ensures the result is properly aligned to the price grid.
        """
        assume(increment > 0)

        result = quantize_price_side_aware(price, increment, side)

        # Check result is a multiple of increment
        remainder = result % increment
        assert remainder == 0, (
            f"Result not on grid: "
            f"quantize({price}, {increment}, {side}) = {result}, "
            f"remainder = {remainder}"
        )


class TestQuantizeBounds:
    """Test that quantization respects side-based bounds."""

    @given(price=reasonable_prices, increment=reasonable_increments)
    @settings(max_examples=200)
    def test_buy_floors_price(self, price: Decimal, increment: Decimal) -> None:
        """BUY side floors the price (result <= input)."""
        assume(increment > 0)

        result = quantize_price_side_aware(price, increment, "BUY")

        assert result <= price, (
            f"BUY should floor: quantize({price}, {increment}, BUY) = {result} > {price}"
        )

    @given(price=reasonable_prices, increment=reasonable_increments)
    @settings(max_examples=200)
    def test_sell_ceils_price(self, price: Decimal, increment: Decimal) -> None:
        """SELL side ceils the price (result >= input)."""
        assume(increment > 0)

        result = quantize_price_side_aware(price, increment, "SELL")

        assert result >= price, (
            f"SELL should ceil: quantize({price}, {increment}, SELL) = {result} < {price}"
        )


class TestQuantizeEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_increment_returns_original(self) -> None:
        """Zero increment should return original price."""
        price = Decimal("123.456")
        result = quantize_price_side_aware(price, Decimal("0"), "BUY")
        assert result == price

    def test_negative_increment_returns_original(self) -> None:
        """Negative increment should return original price."""
        price = Decimal("123.456")
        result = quantize_price_side_aware(price, Decimal("-0.01"), "BUY")
        assert result == price

    def test_price_already_on_grid(self) -> None:
        """Price already on grid should remain unchanged."""
        price = Decimal("100.00")
        increment = Decimal("0.01")

        buy_result = quantize_price_side_aware(price, increment, "BUY")
        sell_result = quantize_price_side_aware(price, increment, "SELL")

        assert buy_result == price
        assert sell_result == price

    def test_invalid_side_defaults_to_buy(self) -> None:
        """Invalid side should default to BUY behavior (floor)."""
        price = Decimal("100.005")
        increment = Decimal("0.01")

        result = quantize_price_side_aware(price, increment, "INVALID")

        # Should floor like BUY
        assert result == Decimal("100.00")

    @given(price=reasonable_prices, increment=reasonable_increments)
    @settings(max_examples=100)
    def test_spread_consistency(self, price: Decimal, increment: Decimal) -> None:
        """BUY price should be <= SELL price for the same input.

        This ensures we don't create negative spreads.
        """
        assume(increment > 0)

        buy_price = quantize_price_side_aware(price, increment, "BUY")
        sell_price = quantize_price_side_aware(price, increment, "SELL")

        assert buy_price <= sell_price, (
            f"Spread consistency violated: BUY={buy_price} > SELL={sell_price} for input={price}"
        )


class TestQuantizeRealWorldScenarios:
    """Test with real-world trading scenarios."""

    @pytest.mark.parametrize(
        "price,increment,side,expected",
        [
            # Bitcoin typical increments
            (Decimal("50000.125"), Decimal("0.01"), "BUY", Decimal("50000.12")),
            (Decimal("50000.125"), Decimal("0.01"), "SELL", Decimal("50000.13")),
            # Ethereum smaller increments
            (Decimal("3000.0015"), Decimal("0.001"), "BUY", Decimal("3000.001")),
            (Decimal("3000.0015"), Decimal("0.001"), "SELL", Decimal("3000.002")),
            # Micro-cap with large increments
            (Decimal("0.00015678"), Decimal("0.0001"), "BUY", Decimal("0.0001")),
            (Decimal("0.00015678"), Decimal("0.0001"), "SELL", Decimal("0.0002")),
            # Whole number increments
            (Decimal("99.50"), Decimal("1"), "BUY", Decimal("99")),
            (Decimal("99.50"), Decimal("1"), "SELL", Decimal("100")),
        ],
    )
    def test_real_world_prices(
        self, price: Decimal, increment: Decimal, side: str, expected: Decimal
    ) -> None:
        """Test quantization with realistic trading prices."""
        result = quantize_price_side_aware(price, increment, side)
        assert result == expected
