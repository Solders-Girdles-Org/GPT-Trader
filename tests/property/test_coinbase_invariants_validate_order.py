from __future__ import annotations

from decimal import Decimal

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.features.brokerages.coinbase.specs import validate_order


# Property-based tests for order validation
@seed(8888)
@settings(max_examples=100, deadline=None)
@given(
    quantity=st.decimals(
        min_value="0.0001", max_value="10000", allow_nan=False, allow_infinity=False, places=6
    ),
    price=st.decimals(
        min_value="0.01", max_value="100000", allow_nan=False, allow_infinity=False, places=4
    )
    | st.none(),
    min_size=st.decimals(
        min_value="0.0001", max_value="10", allow_nan=False, allow_infinity=False, places=6
    ),
    step_size=st.decimals(
        min_value="0.0001", max_value="5", allow_nan=False, allow_infinity=False, places=6
    ),
    price_increment=st.decimals(
        min_value="0.001", max_value="100", allow_nan=False, allow_infinity=False, places=3
    ),
    min_notional=st.decimals(
        min_value="1", max_value="10000", allow_nan=False, allow_infinity=False, places=2
    )
    | st.none(),
    order_type=st.sampled_from(["market", "limit", "stop_limit"]),
    side=st.sampled_from(["buy", "sell"]),
)
def test_validate_order_invariants(
    quantity: Decimal,
    price: Decimal | None,
    min_size: Decimal,
    step_size: Decimal,
    price_increment: Decimal,
    min_notional: Decimal | None,
    order_type: str,
    side: str,
) -> None:
    """Property-based test for order validation invariants."""

    # Create mock product
    class MockProduct:
        pass

    product = MockProduct()
    product.min_size = min_size
    product.step_size = step_size
    product.price_increment = price_increment
    product.min_notional = min_notional

    # Ensure valid constraints
    if step_size > min_size:
        step_size = min_size / Decimal("10")

    # Validate order
    result = validate_order(
        product=product,
        side=side,
        quantity=quantity,
        order_type=order_type,
        price=price,
    )

    # Invariants that must hold
    assert isinstance(result.ok, bool), "Result should have boolean ok field"
    assert isinstance(result.adjusted_quantity, Decimal) or result.adjusted_quantity is None, (
        "Adjusted quantity should be Decimal or None"
    )
    assert isinstance(result.adjusted_price, Decimal) or result.adjusted_price is None, (
        "Adjusted price should be Decimal or None"
    )
    assert isinstance(result.reason, str) or result.reason is None, (
        "Reason should be string or None"
    )

    # If validation passes, adjusted_quantity should be set and meet constraints
    if result.ok:
        assert result.adjusted_quantity is not None, "Valid orders should have adjusted quantity"
        assert result.adjusted_quantity >= min_size, (
            f"Adjusted quantity {result.adjusted_quantity} below min_size {min_size}"
        )

        # Should be quantized to step size
        if step_size > 0:
            steps = result.adjusted_quantity / step_size
            assert steps == steps.to_integral_value(), (
                f"Quantity {result.adjusted_quantity} not quantized to step {step_size}"
            )

        # For limit orders, price should be set and quantized
        if order_type.lower() in ("limit", "stop_limit"):
            assert result.adjusted_price is not None, "Limit orders should have adjusted price"
            assert price_increment > 0, "Price increment should be positive for quantization check"

            # Price should be quantized
            normalized = result.adjusted_price / price_increment
            assert normalized == normalized.to_integral_value(), (
                f"Price {result.adjusted_price} not quantized to increment {price_increment}"
            )

            # Direction should be correct for side
            if price is not None:
                if side.lower() == "buy":
                    assert result.adjusted_price <= price, (
                        "Buy orders should not have price increased"
                    )
                else:
                    assert result.adjusted_price >= price, (
                        "Sell orders should not have price decreased"
                    )

    # If validation fails, there should be a reason
    if not result.ok:
        assert result.reason is not None, "Failed validation should have a reason"
