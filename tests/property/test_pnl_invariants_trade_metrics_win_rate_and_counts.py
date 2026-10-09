"""Property-based tests for trade win rate and counts invariants."""

from __future__ import annotations

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.monitoring.daily_report.analytics import calculate_trade_metrics
from tests.property.pnl_invariants_test_helpers import make_fill_event, positive_pnl_strategy


@seed(4003)
@settings(max_examples=200, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=0, max_size=20),
    losses=st.lists(positive_pnl_strategy, min_size=0, max_size=20),
)
def test_win_rate_bounded(
    wins: list[float],
    losses: list[float],
) -> None:
    """Property: Win rate is always in [0, 1]."""
    events = [make_fill_event(win) for win in wins] + [make_fill_event(-loss) for loss in losses]

    if not events:
        return

    metrics = calculate_trade_metrics(events)

    assert 0 <= metrics["win_rate"] <= 1, f"Win rate {metrics['win_rate']} should be in [0, 1]"


@seed(4004)
@settings(max_examples=200, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=0, max_size=20),
    losses=st.lists(positive_pnl_strategy, min_size=0, max_size=20),
)
def test_win_rate_calculation(
    wins: list[float],
    losses: list[float],
) -> None:
    """Property: Win rate = winning_trades / total_trades."""
    events = [make_fill_event(win) for win in wins] + [make_fill_event(-loss) for loss in losses]
    total = len(wins) + len(losses)

    if total == 0:
        return

    metrics = calculate_trade_metrics(events)

    expected_win_rate = len(wins) / total
    tolerance = 0.01

    assert abs(metrics["win_rate"] - expected_win_rate) < tolerance, (
        f"Win rate {metrics['win_rate']} != expected {expected_win_rate}"
    )


@seed(4008)
@settings(max_examples=200, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=1, max_size=10),
    losses=st.lists(positive_pnl_strategy, min_size=1, max_size=10),
)
def test_trade_counts_correct(
    wins: list[float],
    losses: list[float],
) -> None:
    """Property: Trade counts should match input events."""
    events = [make_fill_event(win) for win in wins] + [make_fill_event(-loss) for loss in losses]

    metrics = calculate_trade_metrics(events)

    assert metrics["total_trades"] == len(wins) + len(losses)
    assert metrics["winning_trades"] == len(wins)
    assert metrics["losing_trades"] == len(losses)


@seed(4009)
@settings(max_examples=200, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=1, max_size=10),
)
def test_all_wins_profit_factor_handling(
    wins: list[float],
) -> None:
    """Property: All wins (no losses) handles profit factor edge case."""
    events = [make_fill_event(win) for win in wins]

    metrics = calculate_trade_metrics(events)

    assert metrics["win_rate"] == 1.0
    assert metrics["profit_factor"] == 0


@seed(4010)
@settings(max_examples=200, deadline=None)
@given(
    losses=st.lists(positive_pnl_strategy, min_size=1, max_size=10),
)
def test_all_losses_win_rate_zero(
    losses: list[float],
) -> None:
    """Property: All losses yield 0% win rate."""
    events = [make_fill_event(-loss) for loss in losses]

    metrics = calculate_trade_metrics(events)

    assert metrics["win_rate"] == 0.0
    assert metrics["winning_trades"] == 0
    assert metrics["losing_trades"] == len(losses)
