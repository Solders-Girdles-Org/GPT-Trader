from __future__ import annotations

from decimal import Decimal

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.features.brokerages.coinbase.specs import calculate_safe_position_size


# Property-based tests for safe position sizing
@seed(9999)
@settings(max_examples=100, deadline=None)
@given(
    target_notional=st.decimals(
        min_value="1", max_value="1000000", allow_nan=False, allow_infinity=False, places=2
    ),
    mark_price=st.decimals(
        min_value="0.01", max_value="100000", allow_nan=False, allow_infinity=False, places=4
    ),
    min_size=st.decimals(
        min_value="0.0001", max_value="10", allow_nan=False, allow_infinity=False, places=6
    ),
    step_size=st.decimals(
        min_value="0.0001", max_value="5", allow_nan=False, allow_infinity=False, places=6
    ),
    max_size=st.decimals(
        min_value="1", max_value="1000000", allow_nan=False, allow_infinity=False, places=2
    ),
    min_notional=st.decimals(
        min_value="1", max_value="10000", allow_nan=False, allow_infinity=False, places=2
    ),
    safe_buffer=st.decimals(
        min_value="0.01", max_value="0.5", allow_nan=False, allow_infinity=False, places=3
    ),
)
def test_calculate_safe_position_size_invariants(
    target_notional: Decimal,
    mark_price: Decimal,
    min_size: Decimal,
    step_size: Decimal,
    max_size: Decimal,
    min_notional: Decimal,
    safe_buffer: Decimal,
) -> None:
    """Property-based test for safe position sizing invariants."""

    # Create mock product
    class MockProduct:
        pass

    product = MockProduct()
    product.min_size = min_size
    product.step_size = step_size
    product.max_size = max_size
    product.min_notional = min_notional

    # Ensure valid constraints
    if min_size > max_size:
        max_size = min_size * Decimal("2")
    if step_size > min_size:
        step_size = min_size / Decimal("10")

    # Calculate safe size
    safe_size = calculate_safe_position_size(
        product=product,
        side="buy",
        intended_quantity=target_notional / mark_price,
        ref_price=mark_price,
    )

    # Invariants that must hold
    assert safe_size >= Decimal("0"), "Safe size should never be negative"

    # Size should be quantized to step size (unless zero)
    if safe_size > 0 and step_size > 0:
        steps = safe_size / step_size
        assert steps == steps.to_integral_value(), (
            f"Size {safe_size} not quantized to step {step_size}"
        )

    # Should not exceed max_size
    assert safe_size <= max_size, f"Safe size {safe_size} exceeds max_size {max_size}"

    # Should meet minimum size requirements (with buffer consideration)
    if safe_size > 0:
        min_required = min_size * Decimal("1.1")  # 10% buffer
        assert safe_size >= min_required, (
            f"Safe size {safe_size} below buffered min_size {min_required}"
        )

    # Notional should meet minimum requirements (with buffer consideration)
    if safe_size > 0:
        notional = safe_size * mark_price
        min_notional_required = min_notional * Decimal("1.1")  # 10% buffer
        assert notional >= min_notional or safe_size == 0, (
            f"Notional {notional} below required {min_notional_required}"
        )


__all__ = ["test_calculate_safe_position_size_invariants"]
