"""Property-based tests for ValidationFailureTracker escalation invariants."""

from __future__ import annotations

from unittest.mock import MagicMock

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.features.live_trade.execution.validation import ValidationFailureTracker
from tests.property.validation_invariants_test_helpers import (
    check_type_strategy,
    threshold_strategy,
)


@seed(4003)
@settings(max_examples=100, deadline=None)
@given(
    check_type=check_type_strategy,
    threshold=threshold_strategy,
    total_failures=st.integers(min_value=1, max_value=100),
)
def test_escalation_triggers_at_threshold(
    check_type: str,
    threshold: int,
    total_failures: int,
) -> None:
    """
    Property: Escalation should trigger when counter reaches threshold.

    record_failure returns True exactly when threshold is reached.
    """
    tracker = ValidationFailureTracker(escalation_threshold=threshold)
    escalation_count = 0

    for _ in range(total_failures):
        if tracker.record_failure(check_type):
            escalation_count += 1

    # Implementation triggers on count >= threshold, so every failure after threshold escalates.
    expected_escalations = max(0, total_failures - threshold + 1)
    assert escalation_count == expected_escalations, (
        f"Expected {expected_escalations} escalations for {total_failures} failures "
        f"with threshold {threshold}, got {escalation_count}"
    )


@seed(4004)
@settings(max_examples=100, deadline=None)
@given(
    check_type=check_type_strategy,
    threshold=threshold_strategy,
)
def test_escalation_callback_called(
    check_type: str,
    threshold: int,
) -> None:
    """
    Property: Escalation callback should be called when threshold is reached.
    """
    callback = MagicMock()
    tracker = ValidationFailureTracker(
        escalation_threshold=threshold,
        escalation_callback=callback,
    )

    for _ in range(threshold - 1):
        tracker.record_failure(check_type)

    callback.assert_not_called()

    tracker.record_failure(check_type)
    callback.assert_called_once()


@seed(4007)
@settings(max_examples=50, deadline=None)
@given(
    check_type=check_type_strategy,
    threshold=threshold_strategy,
)
def test_reset_allows_new_escalation(
    check_type: str,
    threshold: int,
) -> None:
    """
    Property: After reset, reaching threshold again should trigger escalation.
    """
    callback = MagicMock()
    tracker = ValidationFailureTracker(
        escalation_threshold=threshold,
        escalation_callback=callback,
    )

    for _ in range(threshold):
        tracker.record_failure(check_type)
    assert callback.call_count == 1

    tracker.record_success(check_type)
    callback.reset_mock()

    for _ in range(threshold):
        tracker.record_failure(check_type)
    assert callback.call_count == 1, "Callback should trigger again after reset"


@seed(4009)
@settings(max_examples=50, deadline=None)
@given(
    threshold=threshold_strategy,
)
def test_escalation_does_not_trigger_below_threshold(
    threshold: int,
) -> None:
    """
    Property: record_failure should return False for all failures below threshold.
    """
    tracker = ValidationFailureTracker(escalation_threshold=threshold)

    for i in range(threshold - 1):
        escalated = tracker.record_failure("test_check")
        assert escalated is False, (
            f"Escalation should not trigger at failure {i + 1} (threshold {threshold})"
        )


@seed(4012)
@settings(max_examples=100, deadline=None)
@given(
    check_type=check_type_strategy,
    threshold=threshold_strategy,
)
def test_no_callback_still_returns_true(
    check_type: str,
    threshold: int,
) -> None:
    """
    Property: record_failure returns True at threshold even without callback.
    """
    tracker = ValidationFailureTracker(
        escalation_threshold=threshold,
        escalation_callback=None,  # No callback
    )

    for _ in range(threshold - 1):
        result = tracker.record_failure(check_type)
        assert result is False

    result = tracker.record_failure(check_type)
    assert result is True, "Should return True at threshold even without callback"
