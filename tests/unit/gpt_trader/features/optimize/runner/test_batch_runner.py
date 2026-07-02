"""Unit tests for BatchBacktestRunner."""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

import gpt_trader.features.optimize.runner.batch_runner as batch_runner_module
from gpt_trader.backtesting.engine.bar_runner import IHistoricalDataProvider
from gpt_trader.features.live_trade.strategies.base import StrategyProtocol
from gpt_trader.features.live_trade.strategies.baseline.strategy import Action, Decision
from gpt_trader.features.optimize.objectives.base import ObjectiveFunction
from gpt_trader.features.optimize.runner.batch_runner import BatchBacktestRunner


@pytest.fixture
def mock_runner_deps():
    """Create mock dependencies for runner."""
    data_provider = Mock(spec=IHistoricalDataProvider)

    strategy = Mock(spec=StrategyProtocol)
    strategy.decide.return_value = Decision(Action.HOLD, "test")

    objective = Mock(spec=ObjectiveFunction)
    objective.calculate.return_value = 1.0
    objective.is_feasible.return_value = True
    objective.direction = "maximize"

    return data_provider, strategy, objective


class TestBatchBacktestRunner:
    @pytest.mark.asyncio
    async def test_run_trial(self, mock_runner_deps, monkeypatch: pytest.MonkeyPatch):
        """Test running a single trial."""
        data_provider, strategy, objective = mock_runner_deps

        runner = BatchBacktestRunner(
            data_provider=data_provider,
            symbols=["BTC-USD"],
            granularity="ONE_MINUTE",
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 2),
            strategy_factory=lambda _: strategy,
            objective=objective,
        )

        # Mock ClockedBarRunner to yield nothing (empty loop)
        MockRunner = Mock()
        mock_bar_runner = MockRunner.return_value
        mock_bar_runner.run.return_value = AsyncMock()
        # Make the async iterator yield nothing
        mock_bar_runner.run.return_value.__aiter__.return_value = []
        monkeypatch.setattr(batch_runner_module, "ClockedBarRunner", MockRunner)

        result = await runner.run_trial(1, {"p1": 1})

        assert result.trial_number == 1
        assert result.objective_value == 1.0
        assert result.is_feasible
        assert result.backtest_result is not None
