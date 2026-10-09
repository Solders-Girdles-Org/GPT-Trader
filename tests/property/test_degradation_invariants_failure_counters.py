"""Property-based tests for degradation failure-counter invariants."""

from __future__ import annotations

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.features.live_trade.degradation import DegradationState
from gpt_trader.features.live_trade.risk.config import RiskConfig
from tests.property.degradation_invariants_test_helpers import (
    failure_count_strategy,
    symbol_strategy,
)


@seed(3005)
@settings(max_examples=50, deadline=None)
@given(
    symbol=symbol_strategy,
    failure_count=failure_count_strategy,
    threshold=st.integers(min_value=1, max_value=10),
)
def test_slippage_failure_escalation(
    symbol: str,
    failure_count: int,
    threshold: int,
) -> None:
    """
    Property: Slippage failures should trigger pause exactly at threshold.

    The pause should be triggered when count >= threshold, and counter resets.
    """
    state = DegradationState()

    # Create config with specific threshold
    config = RiskConfig(
        slippage_failure_pause_after=threshold,
        slippage_pause_seconds=60,
    )

    pauses_triggered = 0
    for _ in range(failure_count):
        triggered = state.record_slippage_failure(symbol, config)
        if triggered:
            pauses_triggered += 1

    # Number of pause triggers should match how many times we hit the threshold
    expected_triggers = failure_count // threshold
    assert pauses_triggered == expected_triggers, (
        f"Expected {expected_triggers} triggers for {failure_count} failures with threshold {threshold}, "
        f"got {pauses_triggered}"
    )

    # If triggered at least once, symbol should be paused
    if pauses_triggered > 0:
        assert state.is_paused(symbol=symbol) is True, "Symbol should be paused after threshold"


@seed(3006)
@settings(max_examples=50, deadline=None)
@given(
    failure_count=failure_count_strategy,
    threshold=st.integers(min_value=1, max_value=10),
)
def test_broker_failure_escalation(
    failure_count: int,
    threshold: int,
) -> None:
    """
    Property: Broker failures should trigger global pause exactly at threshold.

    Counter resets after pause is triggered.
    """
    state = DegradationState()

    config = RiskConfig(
        broker_outage_max_failures=threshold,
        broker_outage_cooldown_seconds=60,
    )

    pauses_triggered = 0
    for _ in range(failure_count):
        triggered = state.record_broker_failure(config)
        if triggered:
            pauses_triggered += 1

    expected_triggers = failure_count // threshold
    assert pauses_triggered == expected_triggers, (
        f"Expected {expected_triggers} triggers for {failure_count} failures with threshold {threshold}"
    )


@seed(3007)
@settings(max_examples=100, deadline=None)
@given(
    symbol=symbol_strategy,
    success_after=st.integers(min_value=1, max_value=5),
)
def test_slippage_counter_resets_on_success(
    symbol: str,
    success_after: int,
) -> None:
    """
    Property: reset_slippage_failures should reset the counter.

    Subsequent failures start from zero.
    """
    state = DegradationState()
    config = RiskConfig(slippage_failure_pause_after=10, slippage_pause_seconds=60)

    # Record some failures
    for _ in range(success_after):
        state.record_slippage_failure(symbol, config)

    # Reset
    state.reset_slippage_failures(symbol)

    # Internal counter should be zero
    assert state._slippage_failures.get(symbol, 0) == 0


@seed(3008)
@settings(max_examples=100, deadline=None)
@given(
    success_after=st.integers(min_value=1, max_value=5),
)
def test_broker_counter_resets_on_success(
    success_after: int,
) -> None:
    """
    Property: reset_broker_failures should reset the counter.
    """
    state = DegradationState()
    config = RiskConfig(broker_outage_max_failures=10, broker_outage_cooldown_seconds=60)

    # Record some failures
    for _ in range(success_after):
        state.record_broker_failure(config)

    # Reset
    state.reset_broker_failures()

    # Internal counter should be zero
    assert state._broker_failures == 0
