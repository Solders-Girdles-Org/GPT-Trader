"""Property-based tests for ValidationFailureTracker counter invariants."""

from __future__ import annotations

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.features.live_trade.execution.validation import ValidationFailureTracker
from tests.property.validation_invariants_test_helpers import (
    check_type_strategy,
    operation_count_strategy,
    threshold_strategy,
)


@seed(4001)
@settings(max_examples=100, deadline=None)
@given(
    check_type=check_type_strategy,
    failure_count=operation_count_strategy,
)
def test_failure_counter_increments(
    check_type: str,
    failure_count: int,
) -> None:
    """
    Property: Each record_failure call should increment the counter by 1.
    """
    tracker = ValidationFailureTracker(escalation_threshold=1000)  # High threshold

    for i in range(failure_count):
        tracker.record_failure(check_type)
        expected_count = i + 1
        actual_count = tracker.get_failure_count(check_type)
        assert actual_count == expected_count, (
            f"After {i + 1} failures, expected count {expected_count}, got {actual_count}"
        )


@seed(4002)
@settings(max_examples=100, deadline=None)
@given(
    check_type=check_type_strategy,
    failure_count=operation_count_strategy,
)
def test_success_resets_counter(
    check_type: str,
    failure_count: int,
) -> None:
    """
    Property: record_success should always reset the counter to zero.
    """
    tracker = ValidationFailureTracker(escalation_threshold=1000)

    for _ in range(failure_count):
        tracker.record_failure(check_type)

    assert tracker.get_failure_count(check_type) == failure_count

    tracker.record_success(check_type)

    assert tracker.get_failure_count(check_type) == 0, "Counter should be zero after success"


@seed(4005)
@settings(max_examples=100, deadline=None)
@given(
    check_types=st.lists(check_type_strategy, min_size=2, max_size=5, unique=True),
    threshold=threshold_strategy,
)
def test_check_types_are_independent(
    check_types: list[str],
    threshold: int,
) -> None:
    """
    Property: Different check types should have independent counters.

    Failures in one type should not affect other types.
    """
    tracker = ValidationFailureTracker(escalation_threshold=threshold)

    first_type = check_types[0]
    other_types = check_types[1:]

    for _ in range(threshold + 5):
        tracker.record_failure(first_type)

    assert tracker.get_failure_count(first_type) > 0

    for other_type in other_types:
        assert tracker.get_failure_count(other_type) == 0, (
            f"Check type {other_type} should not be affected by {first_type} failures"
        )


@seed(4006)
@settings(max_examples=100, deadline=None)
@given(
    check_type=check_type_strategy,
    failure_count=operation_count_strategy,
)
def test_get_failure_count_is_readonly(
    check_type: str,
    failure_count: int,
) -> None:
    """
    Property: get_failure_count should not modify state.
    """
    tracker = ValidationFailureTracker(escalation_threshold=1000)

    for _ in range(failure_count):
        tracker.record_failure(check_type)

    count1 = tracker.get_failure_count(check_type)
    count2 = tracker.get_failure_count(check_type)
    count3 = tracker.get_failure_count(check_type)

    assert count1 == count2 == count3 == failure_count, (
        "get_failure_count should return consistent values"
    )


@seed(4008)
@settings(max_examples=100, deadline=None)
@given(
    check_type=check_type_strategy,
)
def test_initial_failure_count_is_zero(
    check_type: str,
) -> None:
    """
    Property: Initial failure count for any check type should be zero.
    """
    tracker = ValidationFailureTracker()

    assert tracker.get_failure_count(check_type) == 0


__all__ = ["test_failure_counter_increments"]
