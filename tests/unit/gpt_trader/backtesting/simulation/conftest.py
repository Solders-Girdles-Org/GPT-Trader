from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.backtesting.simulation.fill_model import OrderFillModel
from gpt_trader.core import Candle


@pytest.fixture
def fill_model() -> OrderFillModel:
    """Default fill model with standard configuration."""
    return OrderFillModel(
        slippage_bps={"BTC-USD": Decimal("2"), "ETH-USD": Decimal("2")},
        spread_impact_pct=Decimal("0.5"),
        limit_volume_threshold=Decimal("2.0"),
        enable_queue_priority=False,
    )


@pytest.fixture
def current_bar() -> Candle:
    """Standard candle for testing."""
    return Candle(
        ts=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
        open=Decimal("50000"),
        high=Decimal("50500"),
        low=Decimal("49500"),
        close=Decimal("50200"),
        volume=Decimal("100"),
    )


@pytest.fixture
def next_bar() -> Candle:
    """Next bar for market order fills."""
    return Candle(
        ts=datetime(2024, 1, 1, 10, 1, 0, tzinfo=UTC),
        open=Decimal("50200"),
        high=Decimal("50700"),
        low=Decimal("50000"),
        close=Decimal("50400"),
        volume=Decimal("150"),
    )
