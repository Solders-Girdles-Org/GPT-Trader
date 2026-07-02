"""Tests for WebSocket backoff calculation with exponential backoff and jitter."""

from __future__ import annotations

import pytest

import gpt_trader.utilities.backoff_policy as backoff_policy_module
from gpt_trader.features.brokerages.coinbase.ws import calculate_backoff_with_jitter


class TestCalculateBackoffWithJitter:
    """Tests for the backoff calculation function."""

    def test_backoff_grows_exponentially(self) -> None:
        """Test that backoff delay grows with each attempt."""
        # Disable jitter for deterministic testing
        delays = [calculate_backoff_with_jitter(attempt, jitter_pct=0.0) for attempt in range(5)]

        # Each delay should be greater than the previous (exponential growth)
        for i in range(1, len(delays)):
            assert delays[i] > delays[i - 1], f"Delay {i} should be > delay {i - 1}"

    def test_backoff_respects_max(self) -> None:
        """Test that backoff delay is capped at max_seconds."""
        max_seconds = 30.0

        # High attempt count should hit the max
        delay = calculate_backoff_with_jitter(attempt=20, max_seconds=max_seconds, jitter_pct=0.0)

        assert delay == max_seconds

    def test_backoff_uses_base_at_attempt_zero(self) -> None:
        """Test that first attempt uses base delay."""
        base = 2.0
        delay = calculate_backoff_with_jitter(attempt=0, base_seconds=base, jitter_pct=0.0)
        assert delay == base

    def test_backoff_uses_multiplier(self) -> None:
        """Test that multiplier is applied correctly."""
        base = 2.0
        multiplier = 3.0

        delay0 = calculate_backoff_with_jitter(
            attempt=0, base_seconds=base, multiplier=multiplier, jitter_pct=0.0
        )
        delay1 = calculate_backoff_with_jitter(
            attempt=1, base_seconds=base, multiplier=multiplier, jitter_pct=0.0
        )
        delay2 = calculate_backoff_with_jitter(
            attempt=2, base_seconds=base, multiplier=multiplier, jitter_pct=0.0
        )

        assert delay0 == base  # 2.0
        assert delay1 == base * multiplier  # 6.0
        assert delay2 == base * multiplier * multiplier  # 18.0

    def test_jitter_within_bounds(self) -> None:
        """Test that jitter stays within ±jitter_pct bounds."""
        base = 10.0
        jitter_pct = 0.25  # ±25%

        # Run many iterations to test randomness bounds
        for _ in range(100):
            delay = calculate_backoff_with_jitter(
                attempt=0, base_seconds=base, jitter_pct=jitter_pct, max_seconds=100.0
            )

            # With 25% jitter, delay should be between 7.5 and 12.5
            min_expected = base * (1 - jitter_pct)
            max_expected = base * (1 + jitter_pct)

            assert (
                min_expected <= delay <= max_expected
            ), f"Delay {delay} out of bounds [{min_expected}, {max_expected}]"

    def test_jitter_produces_different_values(self) -> None:
        """Test that jitter produces variation across calls."""
        # Collect multiple values
        delays = [calculate_backoff_with_jitter(attempt=0, jitter_pct=0.25) for _ in range(20)]

        # Should have at least some variation (not all identical)
        unique_delays = set(delays)
        assert len(unique_delays) > 1, "Jitter should produce varying delays"

    def test_jitter_uses_random_uniform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that jitter uses random.uniform for deterministic testing."""
        monkeypatch.setattr(backoff_policy_module.random, "uniform", lambda a, b: 0.5)

        base = 10.0
        jitter_pct = 0.25
        delay = calculate_backoff_with_jitter(
            attempt=0, base_seconds=base, jitter_pct=jitter_pct, max_seconds=100.0
        )

        # Should be base + jitter (0.5)
        assert delay == base + 0.5
