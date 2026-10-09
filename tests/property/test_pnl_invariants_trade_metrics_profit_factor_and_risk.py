"""Property-based tests for trade profit factor and risk metric invariants."""

from __future__ import annotations

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st

from gpt_trader.monitoring.daily_report.analytics import calculate_trade_metrics
from tests.property.pnl_invariants_test_helpers import make_fill_event, positive_pnl_strategy


@seed(4005)
@settings(max_examples=200, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=1, max_size=20),
    losses=st.lists(positive_pnl_strategy, min_size=1, max_size=20),
)
def test_profit_factor_non_negative(
    wins: list[float],
    losses: list[float],
) -> None:
    """Property: Profit factor should always be non-negative."""
    events = [make_fill_event(win) for win in wins] + [make_fill_event(-loss) for loss in losses]

    metrics = calculate_trade_metrics(events)

    assert metrics["profit_factor"] >= 0, (
        f"Profit factor {metrics['profit_factor']} should not be negative"
    )


@seed(4006)
@settings(max_examples=200, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=1, max_size=20),
    losses=st.lists(positive_pnl_strategy, min_size=1, max_size=20),
)
def test_profit_factor_calculation(
    wins: list[float],
    losses: list[float],
) -> None:
    """Property: Profit factor = gross_profit / gross_loss."""
    events = [make_fill_event(win) for win in wins] + [make_fill_event(-loss) for loss in losses]

    metrics = calculate_trade_metrics(events)

    gross_profit = sum(wins)
    gross_loss = sum(losses)

    if gross_loss > 0:
        expected_pf = gross_profit / gross_loss
        tolerance = 0.01

        assert abs(metrics["profit_factor"] - expected_pf) < tolerance, (
            f"Profit factor {metrics['profit_factor']} != expected {expected_pf}"
        )


@seed(4007)
@settings(max_examples=200, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=0, max_size=20),
    losses=st.lists(positive_pnl_strategy, min_size=0, max_size=20),
)
def test_max_drawdown_non_negative(
    wins: list[float],
    losses: list[float],
) -> None:
    """Property: Max drawdown should always be non-negative."""
    events = [make_fill_event(win) for win in wins] + [make_fill_event(-loss) for loss in losses]

    if not events:
        return

    metrics = calculate_trade_metrics(events)

    assert metrics["max_drawdown"] >= 0, (
        f"Max drawdown {metrics['max_drawdown']} should not be negative"
    )


@seed(4011)
@settings(max_examples=150, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=1, max_size=10),
    losses=st.lists(positive_pnl_strategy, min_size=1, max_size=10),
)
def test_average_win_loss_calculation(
    wins: list[float],
    losses: list[float],
) -> None:
    """Property: Average win/loss is computed correctly."""
    events = [make_fill_event(win) for win in wins] + [make_fill_event(-loss) for loss in losses]

    metrics = calculate_trade_metrics(events)

    expected_avg_win = sum(wins) / len(wins)
    expected_avg_loss = sum(losses) / len(losses)
    tolerance = 0.01

    assert abs(metrics["avg_win"] - expected_avg_win) < tolerance, (
        f"Avg win {metrics['avg_win']} != expected {expected_avg_win}"
    )
    assert abs(metrics["avg_loss"] - expected_avg_loss) < tolerance, (
        f"Avg loss {metrics['avg_loss']} != expected {expected_avg_loss}"
    )


@seed(4012)
@settings(max_examples=150, deadline=None)
@given(
    wins=st.lists(positive_pnl_strategy, min_size=1, max_size=10),
    losses=st.lists(positive_pnl_strategy, min_size=1, max_size=10),
)
def test_largest_win_loss_calculation(
    wins: list[float],
    losses: list[float],
) -> None:
    """Property: Largest win/loss is the maximum values."""
    events = [make_fill_event(win) for win in wins] + [make_fill_event(-loss) for loss in losses]

    metrics = calculate_trade_metrics(events)

    expected_largest_win = max(wins)
    expected_largest_loss = max(losses)
    tolerance = 0.01

    assert abs(metrics["largest_win"] - expected_largest_win) < tolerance, (
        f"Largest win {metrics['largest_win']} != expected {expected_largest_win}"
    )
    assert abs(metrics["largest_loss"] - expected_largest_loss) < tolerance, (
        f"Largest loss {metrics['largest_loss']} != expected {expected_largest_loss}"
    )


@pytest.mark.property
class TestPnLPropertyBased:
    """Grouped property-based tests for trade metrics."""

    def test_empty_events_trade_metrics(self) -> None:
        """Verify empty events trade metrics return sensible defaults."""
        metrics = calculate_trade_metrics([])

        assert metrics["total_trades"] == 0
        assert metrics["win_rate"] == 0
        assert metrics["profit_factor"] == 0

    def test_sharpe_ratio_requires_variance(self) -> None:
        """Verify Sharpe ratio calculation handles edge cases."""
        events = [make_fill_event(100) for _ in range(5)]
        metrics = calculate_trade_metrics(events)

        assert metrics["sharpe_ratio"] == 0

    def test_max_drawdown_sequence(self) -> None:
        """Verify max drawdown calculation with known sequence."""
        events = [
            make_fill_event(100),
            make_fill_event(100),
            make_fill_event(-150),
            make_fill_event(50),
        ]

        metrics = calculate_trade_metrics(events)

        assert metrics["max_drawdown"] == 150
