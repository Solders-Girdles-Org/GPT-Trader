"""Shared helpers for optimization feature integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from gpt_trader.backtesting.engine.bar_runner import IHistoricalDataProvider
from gpt_trader.core import Candle
from gpt_trader.features.live_trade.strategies.baseline import (
    BaselinePerpsStrategy,
    PerpsStrategyConfig,
)
from gpt_trader.features.optimize.objectives.single import TotalReturnObjective
from gpt_trader.features.optimize.runner.batch_runner import BatchBacktestRunner

DEFAULT_SYMBOLS = ("BTC-USD",)
DEFAULT_GRANULARITY = "FIVE_MINUTE"
DEFAULT_START_DATE = datetime(2024, 1, 1)
DEFAULT_END_DATE = datetime(2024, 1, 2)


class SyntheticDataProvider(IHistoricalDataProvider):
    """
    Generates synthetic candle data for testing.

    Creates a predictable price series with trends to enable
    strategy parameter optimization testing.
    """

    def __init__(self, base_price: Decimal = Decimal("50000")):
        self.base_price = base_price

    async def get_candles(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Generate synthetic candles with a trending pattern."""
        candles = []
        current_time = start
        price = self.base_price

        granularity_map = {
            "ONE_MINUTE": timedelta(minutes=1),
            "FIVE_MINUTE": timedelta(minutes=5),
            "ONE_HOUR": timedelta(hours=1),
        }
        delta = granularity_map.get(granularity, timedelta(minutes=5))

        bar_index = 0
        while current_time < end:
            # Trend: up for 20 bars, down for 10, repeat
            cycle_position = bar_index % 30
            if cycle_position < 20:
                price = price + Decimal("50")
            else:
                price = price - Decimal("100")

            high = price + Decimal("20")
            low = price - Decimal("20")
            open_price = price - Decimal("10")

            candles.append(
                Candle(
                    ts=current_time,
                    open=open_price,
                    high=high,
                    low=low,
                    close=price,
                    volume=Decimal("100"),
                )
            )

            current_time += delta
            bar_index += 1

        return candles


def create_strategy_factory(params: dict) -> BaselinePerpsStrategy:
    """Factory to create strategy instances from parameter dict."""
    config = PerpsStrategyConfig(
        short_ma_period=params.get("short_ma_period", 5),
        long_ma_period=params.get("long_ma_period", 20),
        position_fraction=params.get("position_fraction", 0.1),
    )
    return BaselinePerpsStrategy(config=config)


def make_batch_runner(
    *,
    data_provider: IHistoricalDataProvider | None = None,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    granularity: str = DEFAULT_GRANULARITY,
    start_date: datetime = DEFAULT_START_DATE,
    end_date: datetime = DEFAULT_END_DATE,
    strategy_factory=create_strategy_factory,
    objective: TotalReturnObjective | None = None,
) -> BatchBacktestRunner:
    """Create a BatchBacktestRunner with sensible defaults."""
    if data_provider is None:
        data_provider = SyntheticDataProvider()
    if objective is None:
        objective = TotalReturnObjective(min_trades=0)

    return BatchBacktestRunner(
        data_provider=data_provider,
        symbols=list(symbols),
        granularity=granularity,
        start_date=start_date,
        end_date=end_date,
        strategy_factory=strategy_factory,
        objective=objective,
    )
