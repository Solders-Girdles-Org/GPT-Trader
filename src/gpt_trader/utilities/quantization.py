"""Canonical home for order size/price quantization math.

Callers in the coinbase brokerage (specs, utilities, rest) re-export these to
preserve their historical import surfaces.
"""

from decimal import ROUND_DOWN, ROUND_UP, Decimal


def quantize_price_side_aware(price: Decimal, increment: Decimal, side: str) -> Decimal:
    """
    Quantize price based on side for better fills.
    BUY orders: floor to price increment (more aggressive)
    SELL orders: ceil to price increment (more aggressive)
    Invalid sides default to BUY behavior (floor).
    """
    if increment <= 0:
        return price

    normalized = price / increment
    if side.upper() == "SELL":
        steps = normalized.to_integral_value(rounding=ROUND_UP)
    else:
        steps = normalized.to_integral_value(rounding=ROUND_DOWN)
    return (steps * increment).quantize(increment)


def quantize_size(size: Decimal, step_size: Decimal) -> Decimal:
    """Floor size to the exchange step size."""
    if step_size is None or step_size == 0:
        return size
    q = (size / step_size).to_integral_value(rounding=ROUND_DOWN)
    return (q * step_size).quantize(step_size)


def quantize_size_up(size: Decimal, step_size: Decimal) -> Decimal:
    """Ceil size to the next exchange step size."""
    if step_size is None or step_size == 0:
        return size
    q = (size / step_size).to_integral_value(rounding=ROUND_UP)
    return (q * step_size).quantize(step_size)


def quantize_to_increment(value: Decimal, increment: Decimal | None) -> Decimal:
    """Floor a positive value to an increment; passthrough when no increment."""
    if not increment or increment == 0:
        return value
    return quantize_size(value, increment)
