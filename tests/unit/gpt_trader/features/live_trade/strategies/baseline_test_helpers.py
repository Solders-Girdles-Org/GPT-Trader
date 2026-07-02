"""Test helpers for perps baseline strategy tests."""

from __future__ import annotations

from decimal import Decimal


def make_price_series(
    base: float,
    changes: list[float],
) -> list[Decimal]:
    """Create a price series from base price and percentage changes."""
    prices = [Decimal(str(base))]
    for change in changes:
        next_price = prices[-1] * Decimal(str(1 + change / 100))
        prices.append(next_price)
    return prices


def make_uptrend(periods: int = 25, volatility: float = 0.5) -> list[Decimal]:
    """Generate an uptrending price series."""
    changes = [volatility + (i * 0.1) for i in range(periods)]
    return make_price_series(100, changes)


def make_downtrend(periods: int = 25, volatility: float = 0.5) -> list[Decimal]:
    """Generate a downtrending price series."""
    changes = [-(volatility + (i * 0.1)) for i in range(periods)]
    return make_price_series(100, changes)


def make_sideways(periods: int = 25) -> list[Decimal]:
    """Generate a sideways price series."""
    changes = [0.2 if i % 2 == 0 else -0.2 for i in range(periods)]
    return make_price_series(100, changes)
