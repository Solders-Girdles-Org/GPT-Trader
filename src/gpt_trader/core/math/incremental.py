"""
Incremental statistics using Welford's online algorithm.

This module provides O(1) updates for mean, variance, and standard deviation,
avoiding the need to store the entire history of values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

Number = float | Decimal


@dataclass
class IncrementalStats:
    """
    Calculates running statistics (mean, variance, std_dev) using Welford's algorithm.

    This class is numerically stable and efficient (O(1) memory and time).
    """

    count: int = 0
    _mean: float = 0.0
    _m2: float = 0.0  # Sum of squares of differences from the current mean
    min_val: float = float("inf")
    max_val: float = float("-inf")

    def update(self, value: Number) -> None:
        """
        Update statistics with a new value.

        Args:
            value: The new observation (float or Decimal).
        """
        val_float = float(value)
        self.count += 1

        delta = val_float - self._mean
        self._mean += delta / self.count
        delta2 = val_float - self._mean
        self._m2 += delta * delta2

        if val_float < self.min_val:
            self.min_val = val_float
        if val_float > self.max_val:
            self.max_val = val_float

    @property
    def mean(self) -> float:
        """Current mean."""
        return self._mean

    @property
    def variance(self) -> float:
        """Current sample variance."""
        if self.count < 2:
            return 0.0
        return self._m2 / (self.count - 1)

    @property
    def population_variance(self) -> float:
        """Current population variance."""
        if self.count == 0:
            return 0.0
        return self._m2 / self.count

    @property
    def std_dev(self) -> float:
        """Current sample standard deviation."""
        return math.sqrt(self.variance)

    @property
    def population_std_dev(self) -> float:
        """Current population standard deviation."""
        return math.sqrt(self.population_variance)

    def reset(self) -> None:
        """Reset all statistics."""
        self.count = 0
        self._mean = 0.0
        self._m2 = 0.0
        self.min_val = float("inf")
        self.max_val = float("-inf")
