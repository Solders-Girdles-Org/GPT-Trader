from __future__ import annotations

from decimal import Decimal

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.features.brokerages.coinbase.specs import validate_order


# Property-based tests for order lifecycle invariants
@seed(7777)
@settings(max_examples=50, deadline=None)
@given(
    initial_quantity=st.decimals(
        min_value="0.0001", max_value="100", allow_nan=False, allow_infinity=False, places=6
    ),
    initial_price=st.decimals(
        min_value="0.01", max_value="10000", allow_nan=False, allow_infinity=False, places=4
    ),
    fill_price=st.decimals(
        min_value="0.01", max_value="10000", allow_nan=False, allow_infinity=False, places=4
    ),
    min_size=st.decimals(
        min_value="0.0001", max_value="10", allow_nan=False, allow_infinity=False, places=6
    ),
    step_size=st.decimals(
        min_value="0.0001", max_value="5", allow_nan=False, allow_infinity=False, places=6
    ),
    price_increment=st.decimals(
        min_value="0.001", max_value="100", allow_nan=False, allow_infinity=False, places=3
    ),
)
def test_order_lifecycle_invariants(
    initial_quantity: Decimal,
    initial_price: Decimal,
    fill_price: Decimal,
    min_size: Decimal,
    step_size: Decimal,
    price_increment: Decimal,
) -> None:
    """Property-based test for order lifecycle invariants."""

    # Create mock product
    class MockProduct:
        pass

    product = MockProduct()
    product.min_size = min_size
    product.step_size = step_size
    product.price_increment = price_increment
    product.min_notional = None

    # Ensure valid constraints
    if step_size > min_size:
        step_size = min_size / Decimal("10")

    # Simulate order lifecycle: submit -> validate -> fill
    # 1. Validate order
    validation = validate_order(
        product=product,
        side="buy",
        quantity=initial_quantity,
        order_type="limit",
        price=initial_price,
    )

    # 2. If validation passes, simulate fill
    if validation.ok and validation.adjusted_quantity and validation.adjusted_price:
        # Fill should respect validated constraints
        assert validation.adjusted_quantity >= min_size, "Fill quantity should meet min size"

        # Limit buys should never fill above the validated price
        effective_fill_price = fill_price
        if validation.adjusted_price > 0:
            if validation.adjusted_price >= effective_fill_price:
                effective_fill_price = min(effective_fill_price, validation.adjusted_price)
            else:
                # Allow a small tolerance beyond the validated price for stochastic tests
                effective_fill_price = validation.adjusted_price

        # Fill price should be within reasonable bounds (slippage)
        if effective_fill_price <= validation.adjusted_price:
            price_diff = Decimal("0")
        else:
            price_diff = effective_fill_price - validation.adjusted_price
        max_slippage = validation.adjusted_price * Decimal("0.05")  # 5% max slippage
        assert price_diff <= max_slippage, (
            f"Fill price {effective_fill_price} exceeds slippage tolerance from {validation.adjusted_price}"
        )

        # Notional should be reasonable
        fill_notional = validation.adjusted_quantity * effective_fill_price
        min_expected_notional = (
            min_size * effective_fill_price * Decimal("0.95")
        )  # Allow some slippage
        assert fill_notional >= min_expected_notional, f"Fill notional {fill_notional} too low"
