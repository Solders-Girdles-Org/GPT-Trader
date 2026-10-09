"""
Rolling window statistics.

Provides O(1) updates for mean, variance, and standard deviation over a fixed-size sliding window.
"""

from __future__ import annotations

import math
from collections import deque
from decimal import Decimal

Number = float | Decimal


class RollingStats:
    """
    Calculates statistics over a fixed-size sliding window.

    Maintains running sums to allow O(1) updates when adding new values
    and removing old ones.
    """

    def __init__(self, window_size: int) -> None:
        if window_size < 1:
            raise ValueError("Window size must be at least 1")

        self._window_size = window_size
        self._values: deque[float] = deque(maxlen=window_size)
        self._sum = 0.0
        self._sum_sq = 0.0

    def update(self, value: Number) -> None:
        """
        Add a new value to the window.

        If the window is full, the oldest value is removed and statistics are updated.
        """
        val_float = float(value)

        # If window is full, remove oldest value from sums
        if len(self._values) == self._window_size:
            old_val = self._values[0]  # deque[0] is the oldest
            self._sum -= old_val
            self._sum_sq -= old_val * old_val

        # Add new value
        self._values.append(val_float)
        self._sum += val_float
        self._sum_sq += val_float * val_float

    @property
    def count(self) -> int:
        """Current number of items in the window."""
        return len(self._values)

    @property
    def is_full(self) -> bool:
        """True if the window has reached its maximum size."""
        return len(self._values) == self._window_size

    @property
    def mean(self) -> float:
        """Current mean of the window."""
        if not self._values:
            return 0.0
        return self._sum / len(self._values)

    @property
    def variance(self) -> float:
        """Current sample variance of the window."""
        n = len(self._values)
        if n < 2:
            return 0.0

        # Var = (SumSq - (Sum^2 / n)) / (n - 1)
        # Use max(0, ...) to handle potential floating point errors resulting in negative variance
        numerator = self._sum_sq - (self._sum * self._sum) / n
        return max(0.0, numerator / (n - 1))

    @property
    def population_variance(self) -> float:
        """Current population variance of the window."""
        n = len(self._values)
        if n == 0:
            return 0.0

        numerator = self._sum_sq - (self._sum * self._sum) / n
        return max(0.0, numerator / n)

    @property
    def std_dev(self) -> float:
        """Current sample standard deviation."""
        return math.sqrt(self.variance)

    @property
    def population_std_dev(self) -> float:
        """Current population standard deviation."""
        return math.sqrt(self.population_variance)

    def clear(self) -> None:
        """Clear the window and reset statistics."""
        self._values.clear()
        self._sum = 0.0
        self._sum_sq = 0.0
