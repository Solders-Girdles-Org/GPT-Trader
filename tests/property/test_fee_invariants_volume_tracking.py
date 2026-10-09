"""Property-based tests for fee volume tracking invariants."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.backtesting.simulation.fee_calculator import FeeCalculator
from gpt_trader.backtesting.types import FeeTier
from tests.property.fee_invariants_test_helpers import expected_tier_for_volume


@seed(3006)
@settings(max_examples=50, deadline=None)
@given(
    volumes=st.lists(
        st.decimals(
            min_value="100",
            max_value="1000000",
            allow_nan=False,
            allow_infinity=False,
            places=2,
        ),
        min_size=1,
        max_size=20,
    ),
)
def test_volume_tracking_tier_upgrades(
    volumes: list[Decimal],
) -> None:
    """
    Property: Volume tracking should correctly upgrade tiers as volume accumulates.

    Tier thresholds:
    - TIER_0: < $10K
    - TIER_1: $10K-$50K
    - TIER_2: $50K-$100K
    - TIER_3: $100K-$1M
    - etc.
    """
    calculator = FeeCalculator(tier=FeeTier.TIER_0, volume_tracking=True)

    cumulative_volume = Decimal("0")

    for volume in volumes:
        calculator.calculate(volume, is_maker=False)
        cumulative_volume += volume

        expected_tier = expected_tier_for_volume(cumulative_volume)

        assert calculator.tier == expected_tier, (
            f"After volume {cumulative_volume}, tier should be {expected_tier.value}, "
            f"got {calculator.tier.value}"
        )


@seed(3008)
@settings(max_examples=50, deadline=None)
@given(
    initial_volume=st.decimals(
        min_value="0",
        max_value="10000000",
        allow_nan=False,
        allow_infinity=False,
        places=2,
    ),
)
def test_volume_reset_clears_tracking(
    initial_volume: Decimal,
) -> None:
    """
    Property: Resetting volume should clear the tracker and potentially change tier.
    """
    calculator = FeeCalculator(tier=FeeTier.TIER_0, volume_tracking=True)

    calculator.calculate(initial_volume, is_maker=False)
    calculator.reset_volume()

    assert calculator.current_volume == Decimal("0"), (
        f"After reset, volume should be 0, got {calculator.current_volume}"
    )


def test_volume_tracking_disabled_no_tier_change() -> None:
    """Verify volume tracking disabled keeps tier constant."""
    calculator = FeeCalculator(tier=FeeTier.TIER_2, volume_tracking=False)
    initial_tier = calculator.tier

    for _ in range(100):
        calculator.calculate(Decimal("1000000"), is_maker=False)

    assert calculator.tier == initial_tier
    assert calculator.current_volume == Decimal("0")
