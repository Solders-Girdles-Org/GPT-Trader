"""Property-based tests for fee tier and rate invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import assume, given, seed, settings
from hypothesis import strategies as st

from gpt_trader.backtesting.simulation.fee_calculator import FeeCalculator
from gpt_trader.backtesting.types import FEE_TIER_RATES, FeeTier
from tests.property.fee_invariants_test_helpers import (
    TIERS_ORDERED,
    positive_notional_strategy,
    tier_strategy,
)


@seed(3003)
@settings(max_examples=100, deadline=None)
@given(
    notional=positive_notional_strategy,
    tier=tier_strategy,
)
def test_maker_fee_leq_taker_fee(
    notional: Decimal,
    tier: FeeTier,
) -> None:
    """
    Property: Maker fees should always be less than or equal to taker fees.

    This is a fundamental market structure property - makers add liquidity
    and should be rewarded with lower fees.
    """
    assume(notional > Decimal("0"))

    calculator = FeeCalculator(tier=tier, volume_tracking=False)

    maker_fee = calculator.calculate(notional, is_maker=True)
    taker_fee = calculator.calculate(notional, is_maker=False)

    assert maker_fee <= taker_fee, f"Maker fee {maker_fee} should not exceed taker fee {taker_fee}"


@seed(3004)
@settings(max_examples=100, deadline=None)
@given(
    notional=positive_notional_strategy,
)
def test_fee_tiers_monotonically_decrease(
    notional: Decimal,
) -> None:
    """
    Property: Higher volume tiers should have lower or equal fees.

    As volume increases, traders get better rates.
    """
    assume(notional > Decimal("0"))

    # Calculate fees for each tier
    fees = []
    for tier in TIERS_ORDERED:
        calculator = FeeCalculator(tier=tier, volume_tracking=False)
        fee = calculator.calculate(notional, is_maker=False)  # Use taker for comparison
        fees.append((tier, fee))

    # Each subsequent tier should have lower or equal fees
    for i in range(len(fees) - 1):
        current_tier, current_fee = fees[i]
        next_tier, next_fee = fees[i + 1]

        assert next_fee <= current_fee, (
            f"Higher tier {next_tier.value} fee {next_fee} should not exceed "
            f"lower tier {current_tier.value} fee {current_fee}"
        )


@seed(3007)
@settings(max_examples=100, deadline=None)
@given(
    tier=tier_strategy,
    is_maker=st.booleans(),
)
def test_rate_percentage_conversion(
    tier: FeeTier,
    is_maker: bool,
) -> None:
    """
    Property: Rate percentage should be correctly converted from basis points.

    rate_pct = rate_bps / 100
    e.g., 25 bps = 0.25%
    """
    calculator = FeeCalculator(tier=tier, volume_tracking=False)
    rate_pct = calculator.get_rate_pct(is_maker)

    rates = FEE_TIER_RATES[tier]
    rate_bps = rates.maker_bps if is_maker else rates.taker_bps

    expected_pct = rate_bps / Decimal("100")

    assert rate_pct == expected_pct, (
        f"Rate PCT {rate_pct} doesn't match expected {expected_pct} (rate_bps={rate_bps})"
    )


@pytest.mark.property
class TestFeeRatesConsistency:
    """Grouped invariant tests for fee tiers/rates."""

    def test_fee_tier_rates_consistency(self) -> None:
        """Verify fee tier rates are internally consistent."""
        for tier in FeeTier:
            rates = FEE_TIER_RATES[tier]

            # Maker should be <= taker
            assert rates.maker_bps <= rates.taker_bps, (
                f"Tier {tier.value}: maker {rates.maker_bps} > taker {rates.taker_bps}"
            )

            # Both should be non-negative
            assert rates.maker_bps >= Decimal("0")
            assert rates.taker_bps >= Decimal("0")

    def test_highest_tier_lowest_rates(self) -> None:
        """Verify TIER_7 has the lowest rates."""
        tier_7_rates = FEE_TIER_RATES[FeeTier.TIER_7]

        for tier in FeeTier:
            if tier != FeeTier.TIER_7:
                other_rates = FEE_TIER_RATES[tier]
                assert tier_7_rates.maker_bps <= other_rates.maker_bps
                assert tier_7_rates.taker_bps <= other_rates.taker_bps

    def test_fee_calculation_example(self) -> None:
        """Verify fee calculation matches documented example."""
        # Example from docstring: TIER_2, $10000 notional
        # Maker: 25 bps = 0.25% = $25
        # Taker: 40 bps = 0.40% = $40

        calculator = FeeCalculator(tier=FeeTier.TIER_2, volume_tracking=False)

        maker_fee = calculator.calculate(Decimal("10000"), is_maker=True)
        taker_fee = calculator.calculate(Decimal("10000"), is_maker=False)

        assert maker_fee == Decimal("25")
        assert taker_fee == Decimal("40")
