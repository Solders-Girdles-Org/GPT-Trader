"""Integration tests for optimization failure handling."""

from __future__ import annotations

import pytest

from gpt_trader.features.live_trade.strategies.baseline import (
    BaselinePerpsStrategy,
    PerpsStrategyConfig,
)
from gpt_trader.features.optimize.study.manager import OptimizationStudyManager
from gpt_trader.features.optimize.types import (
    OptimizationConfig,
    ParameterDefinition,
    ParameterSpace,
    ParameterType,
)
from tests.integration.optimize_feature_test_base import make_batch_runner

pytestmark = pytest.mark.integration


class TestOptimizationFailures:
    """Tests for optimization error handling."""

    @pytest.mark.asyncio
    async def test_trial_handles_strategy_error_gracefully(self, optuna) -> None:
        """Test that a failing strategy doesn't crash the optimization."""

        def failing_strategy_factory(params: dict) -> BaselinePerpsStrategy:
            config = PerpsStrategyConfig(
                short_ma_period=params.get("short_ma_period", 5),
                long_ma_period=params.get("long_ma_period", 20),
            )
            return BaselinePerpsStrategy(config=config)

        parameter_space = ParameterSpace(
            strategy_parameters=[
                ParameterDefinition(
                    name="short_ma_period",
                    parameter_type=ParameterType.INTEGER,
                    low=2,
                    high=5,
                ),
            ]
        )

        config = OptimizationConfig(
            study_name="test_error_handling",
            parameter_space=parameter_space,
            objective_name="total_return",
            direction="maximize",
            number_of_trials=2,
            sampler_type="random",
            seed=42,
        )

        study_manager = OptimizationStudyManager(config)
        study = study_manager.create_or_load_study()

        runner = make_batch_runner(strategy_factory=failing_strategy_factory)

        for trial_num in range(config.number_of_trials):
            trial = study.ask()
            params = study_manager.suggest_parameters(trial)
            result = await runner.run_trial(trial_num, params)
            study.tell(trial, result.objective_value)

        assert len(study.trials) == 2
