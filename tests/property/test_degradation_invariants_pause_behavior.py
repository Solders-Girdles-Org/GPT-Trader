"""Property-based tests for degradation pause behavior invariants."""

from __future__ import annotations

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.features.live_trade.degradation import DegradationState
from tests.property.degradation_invariants_test_helpers import (
    pause_seconds_strategy,
    reason_strategy,
    symbol_strategy,
)


@seed(3001)
@settings(max_examples=100, deadline=None)
@given(
    seconds=pause_seconds_strategy,
    reason=reason_strategy,
    allow_reduce_only=st.booleans(),
)
def test_global_pause_is_active_during_duration(
    seconds: int,
    reason: str,
    allow_reduce_only: bool,
) -> None:
    """
    Property: Global pause should be active immediately after calling pause_all.

    is_paused() should return True when not reduce-only (or reduce-only not allowed).
    """
    state = DegradationState()

    # Pause all trading
    state.pause_all(seconds=seconds, reason=reason, allow_reduce_only=allow_reduce_only)

    # Check immediately - should be paused
    assert state.is_paused() is True, "Global pause should be active immediately"
    assert state.is_paused(symbol="BTC-USD") is True, "Symbol should be paused under global pause"

    # Reduce-only should bypass if allowed
    if allow_reduce_only:
        assert state.is_paused(is_reduce_only=True) is False, (
            "Reduce-only should bypass when allowed"
        )
    else:
        assert state.is_paused(is_reduce_only=True) is True, (
            "Reduce-only should be blocked when not allowed"
        )


@seed(3002)
@settings(max_examples=100, deadline=None)
@given(
    symbol=symbol_strategy,
    seconds=pause_seconds_strategy,
    reason=reason_strategy,
    allow_reduce_only=st.booleans(),
)
def test_symbol_pause_is_isolated(
    symbol: str,
    seconds: int,
    reason: str,
    allow_reduce_only: bool,
) -> None:
    """
    Property: Symbol pause should only affect the paused symbol.

    Other symbols should remain unpaused.
    """
    state = DegradationState()
    other_symbol = "XRP-USD"  # Always different from the paused symbol

    # Pause specific symbol
    state.pause_symbol(
        symbol=symbol, seconds=seconds, reason=reason, allow_reduce_only=allow_reduce_only
    )

    # Paused symbol should be paused
    assert state.is_paused(symbol=symbol) is True, f"{symbol} should be paused"

    # Other symbol should not be paused
    assert state.is_paused(symbol=other_symbol) is False, f"{other_symbol} should not be paused"

    # Global check (no symbol) should not be paused
    assert state.is_paused() is False, "Global pause should not be active"


@seed(3003)
@settings(max_examples=100, deadline=None)
@given(
    symbol=symbol_strategy,
    global_seconds=pause_seconds_strategy,
    symbol_seconds=pause_seconds_strategy,
)
def test_global_pause_takes_precedence(
    symbol: str,
    global_seconds: int,
    symbol_seconds: int,
) -> None:
    """
    Property: Global pause should take precedence over symbol pause.

    When both are active, global pause properties should apply.
    """
    state = DegradationState()

    # Set both pauses with different reduce-only settings
    state.pause_all(seconds=global_seconds, reason="global", allow_reduce_only=False)
    state.pause_symbol(
        symbol=symbol, seconds=symbol_seconds, reason="symbol", allow_reduce_only=True
    )

    # Global should take precedence - reduce-only should be blocked
    assert state.is_paused(symbol=symbol, is_reduce_only=True) is True, (
        "Global pause (allow_reduce_only=False) should block reduce-only"
    )


@seed(3004)
@settings(max_examples=100, deadline=None)
@given(
    reason=reason_strategy,
)
def test_pause_reason_returned_correctly(
    reason: str,
) -> None:
    """
    Property: get_pause_reason should return the correct reason.
    """
    state = DegradationState()

    # Initially no reason
    assert state.get_pause_reason() is None

    # After pause, reason should be returned
    state.pause_all(seconds=60, reason=reason)
    returned_reason = state.get_pause_reason()

    assert returned_reason == reason, f"Expected '{reason}', got '{returned_reason}'"


@seed(3012)
@settings(max_examples=50, deadline=None)
@given(
    allow_reduce_only=st.booleans(),
)
def test_reduce_only_consistency_global(
    allow_reduce_only: bool,
) -> None:
    """
    Property: Reduce-only behavior should be consistent with the pause setting.
    """
    state = DegradationState()

    state.pause_all(seconds=60, reason="test", allow_reduce_only=allow_reduce_only)

    # Non-reduce-only orders should always be blocked
    assert state.is_paused(is_reduce_only=False) is True

    # Reduce-only orders should be blocked only if not allowed
    if allow_reduce_only:
        assert state.is_paused(is_reduce_only=True) is False
    else:
        assert state.is_paused(is_reduce_only=True) is True


@seed(3013)
@settings(max_examples=50, deadline=None)
@given(
    symbol=symbol_strategy,
    allow_reduce_only=st.booleans(),
)
def test_reduce_only_consistency_symbol(
    symbol: str,
    allow_reduce_only: bool,
) -> None:
    """
    Property: Reduce-only behavior for symbol pause should be consistent.
    """
    state = DegradationState()

    state.pause_symbol(
        symbol=symbol, seconds=60, reason="test", allow_reduce_only=allow_reduce_only
    )

    # Non-reduce-only orders should always be blocked for this symbol
    assert state.is_paused(symbol=symbol, is_reduce_only=False) is True

    # Reduce-only orders follow the setting
    if allow_reduce_only:
        assert state.is_paused(symbol=symbol, is_reduce_only=True) is False
    else:
        assert state.is_paused(symbol=symbol, is_reduce_only=True) is True


@seed(3011)
@settings(max_examples=50, deadline=None)
@given(
    symbols=st.lists(symbol_strategy, min_size=2, max_size=4, unique=True),
)
def test_multiple_symbol_pauses_are_independent(
    symbols: list[str],
) -> None:
    """
    Property: Multiple symbol pauses should be independent.

    Each symbol can be paused/unpaused independently.
    """
    state = DegradationState()

    # Pause first symbol only
    first_symbol = symbols[0]
    other_symbols = symbols[1:]

    state.pause_symbol(symbol=first_symbol, seconds=60, reason="test")

    # First symbol should be paused
    assert state.is_paused(symbol=first_symbol) is True

    # Other symbols should not be paused
    for other in other_symbols:
        assert state.is_paused(symbol=other) is False, f"{other} should not be paused"


__all__ = ["test_global_pause_is_active_during_duration"]
