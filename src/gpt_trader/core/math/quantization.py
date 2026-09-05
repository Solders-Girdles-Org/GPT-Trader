"""Venue-independent tick arithmetic for planned price levels."""

from decimal import ROUND_HALF_EVEN, Decimal


def quantize_price_nearest(price: Decimal, increment: Decimal) -> Decimal:
    """Round a proposal level to an actual tick multiple, including e.g. 0.05."""
    if not increment.is_finite() or increment <= 0:
        raise ValueError("Price increment must be finite and positive")
    return ((price / increment).to_integral_value(rounding=ROUND_HALF_EVEN) * increment).quantize(
        increment
    )
