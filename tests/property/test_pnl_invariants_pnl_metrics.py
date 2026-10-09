"""Property-based tests for PnL metrics invariants."""

from __future__ import annotations

import pytest
from hypothesis import assume, given, seed, settings
from hypothesis import strategies as st

from gpt_trader.monitoring.daily_report.analytics import calculate_pnl_metrics
from tests.property.pnl_invariants_test_helpers import (
    equity_strategy,
    make_fill_event,
    make_funding_event,
    make_pnl_event,
    pnl_value_strategy,
)


@seed(4001)
@settings(max_examples=200, deadline=None)
@given(
    realized=pnl_value_strategy,
    unrealized=pnl_value_strategy,
    equity=equity_strategy,
)
def test_total_pnl_is_sum_of_components(
    realized: float,
    unrealized: float,
    equity: float,
) -> None:
    """Property: Total PnL = realized + unrealized."""
    events = [make_pnl_event(realized, unrealized)]
    current_metrics = {"account": {"equity": equity}}

    metrics = calculate_pnl_metrics(events, current_metrics)

    expected_total = realized + unrealized
    tolerance = 0.01

    assert abs(metrics["total_pnl"] - expected_total) < tolerance, (
        f"Total PnL {metrics['total_pnl']} != realized {realized} + unrealized {unrealized}"
    )


@seed(4002)
@settings(max_examples=200, deadline=None)
@given(
    realized=pnl_value_strategy,
    unrealized=pnl_value_strategy,
    funding=pnl_value_strategy,
    equity=equity_strategy,
)
def test_pnl_components_correctly_extracted(
    realized: float,
    unrealized: float,
    funding: float,
    equity: float,
) -> None:
    """Property: PnL components should be extracted from events."""
    events = [
        make_pnl_event(realized, unrealized),
        make_funding_event(funding),
    ]
    current_metrics = {"account": {"equity": equity}}

    metrics = calculate_pnl_metrics(events, current_metrics)

    tolerance = 0.01

    assert abs(metrics["realized_pnl"] - realized) < tolerance
    assert abs(metrics["unrealized_pnl"] - unrealized) < tolerance
    assert abs(metrics["funding_pnl"] - funding) < tolerance


@seed(4013)
@settings(max_examples=100, deadline=None)
@given(
    total_pnl=pnl_value_strategy,
    equity=equity_strategy,
)
def test_equity_change_percentage(
    total_pnl: float,
    equity: float,
) -> None:
    """Property: equity_change_pct matches (total_pnl / prev_equity) * 100."""
    assume(abs(total_pnl) < equity)

    events = [make_pnl_event(total_pnl, 0)]
    current_metrics = {"account": {"equity": equity}}

    metrics = calculate_pnl_metrics(events, current_metrics)

    prev_equity = equity - total_pnl
    if prev_equity > 0:
        expected_pct = (total_pnl / prev_equity) * 100
        tolerance = 0.1

        assert abs(metrics["equity_change_pct"] - expected_pct) < tolerance, (
            f"Equity change % {metrics['equity_change_pct']} != expected {expected_pct}"
        )


@seed(4014)
@settings(max_examples=100, deadline=None)
@given(
    fees=st.lists(
        st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False),
        min_size=0,
        max_size=20,
    ),
    equity=equity_strategy,
)
def test_fees_accumulation(
    fees: list[float],
    equity: float,
) -> None:
    """Property: Fees are accumulated from fill events."""
    events = [make_fill_event(0, fee) for fee in fees]
    current_metrics = {"account": {"equity": equity}}

    metrics = calculate_pnl_metrics(events, current_metrics)

    expected_fees = sum(fees)
    tolerance = 0.01

    assert abs(metrics["fees_paid"] - expected_fees) < tolerance, (
        f"Fees paid {metrics['fees_paid']} != expected {expected_fees}"
    )


@pytest.mark.property
class TestPnLPropertyBased:
    """Grouped property-based tests for PnL calculations."""

    def test_empty_events_returns_defaults(self) -> None:
        """Verify empty events return sensible defaults."""
        metrics = calculate_pnl_metrics([], {"account": {"equity": 10000}})

        assert metrics["realized_pnl"] == 0
        assert metrics["unrealized_pnl"] == 0
        assert metrics["funding_pnl"] == 0
        assert metrics["total_pnl"] == 0
        assert metrics["fees_paid"] == 0
