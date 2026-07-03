"""Default parameter space definitions for optimization.

These definitions are based on the actual configuration classes:
- BaseStrategyConfig: Strategy parameters
- PerpsStrategyConfig: Leverage parameters
- SimulationConfig: Backtesting simulation parameters
"""

from __future__ import annotations

from gpt_trader.features.optimize.types import ParameterDefinition, ParameterType


def strategy_parameter_space() -> list[ParameterDefinition]:
    """
    Default strategy parameter space based on BaseStrategyConfig.

    Returns:
        List of parameter definitions for strategy optimization.
    """
    return [
        # Moving Average Parameters
        ParameterDefinition(
            name="short_ma_period",
            parameter_type=ParameterType.INTEGER,
            low=3,
            high=20,
            step=1,
            default=5,
            description="Short moving average period for crossover signals",
        ),
        ParameterDefinition(
            name="long_ma_period",
            parameter_type=ParameterType.INTEGER,
            low=10,
            high=100,
            step=5,
            default=20,
            description="Long moving average period for trend direction",
        ),
        # RSI Parameters
        ParameterDefinition(
            name="rsi_period",
            parameter_type=ParameterType.INTEGER,
            low=7,
            high=28,
            step=1,
            default=14,
            description="RSI calculation period",
        ),
        ParameterDefinition(
            name="rsi_overbought",
            parameter_type=ParameterType.INTEGER,
            low=60,
            high=85,
            step=5,
            default=70,
            description="RSI overbought threshold (sell signal)",
        ),
        ParameterDefinition(
            name="rsi_oversold",
            parameter_type=ParameterType.INTEGER,
            low=15,
            high=40,
            step=5,
            default=30,
            description="RSI oversold threshold (buy signal)",
        ),
        # Risk Management Parameters
        ParameterDefinition(
            name="stop_loss_pct",
            parameter_type=ParameterType.FLOAT,
            low=0.005,
            high=0.10,
            step=0.005,
            default=0.02,
            description="Stop loss percentage (0.02 = 2%)",
        ),
        ParameterDefinition(
            name="take_profit_pct",
            parameter_type=ParameterType.FLOAT,
            low=0.01,
            high=0.20,
            step=0.01,
            default=0.05,
            description="Take profit percentage (0.05 = 5%)",
        ),
        # Confidence Thresholds
        ParameterDefinition(
            name="min_confidence",
            parameter_type=ParameterType.FLOAT,
            low=0.3,
            high=0.8,
            step=0.05,
            default=0.5,
            description="Minimum confidence threshold for entry signals",
        ),
        # Leverage cap (PerpsStrategyConfig.max_leverage; the bot-level
        # BotRiskConfig.max_leverage alias was retired in #1120 stage 3, so
        # this routes to the strategy section on apply)
        ParameterDefinition(
            name="max_leverage",
            parameter_type=ParameterType.INTEGER,
            low=1,
            high=10,
            step=1,
            default=10,
            description="Maximum allowed leverage",
        ),
        # Signal Weights (should sum to ~1.0)
        ParameterDefinition(
            name="crossover_weight",
            parameter_type=ParameterType.FLOAT,
            low=0.1,
            high=0.6,
            step=0.05,
            default=0.4,
            description="Weight for MA crossover signals in confidence calculation",
        ),
        ParameterDefinition(
            name="rsi_weight",
            parameter_type=ParameterType.FLOAT,
            low=0.1,
            high=0.5,
            step=0.05,
            default=0.3,
            description="Weight for RSI signals in confidence calculation",
        ),
        ParameterDefinition(
            name="trend_weight",
            parameter_type=ParameterType.FLOAT,
            low=0.1,
            high=0.5,
            step=0.05,
            default=0.3,
            description="Weight for trend signals in confidence calculation",
        ),
    ]


def risk_parameter_space() -> list[ParameterDefinition]:
    """
    Risk management parameter space based on PerpsStrategyConfig.

    Returns:
        List of parameter definitions for risk management optimization.
    """
    return [
        ParameterDefinition(
            name="target_leverage",
            parameter_type=ParameterType.INTEGER,
            low=1,
            high=10,
            step=1,
            default=5,
            description="Target leverage for positions",
        ),
        ParameterDefinition(
            name="position_fraction",
            parameter_type=ParameterType.FLOAT,
            low=0.05,
            high=0.5,
            step=0.05,
            default=0.2,
            description="Fraction of equity to use per position",
        ),
    ]


def simulation_parameter_space() -> list[ParameterDefinition]:
    """
    Simulation parameter space based on SimulationConfig.

    Returns:
        List of parameter definitions for simulation optimization.
    """
    return [
        ParameterDefinition(
            name="fee_tier",
            parameter_type=ParameterType.CATEGORICAL,
            choices=["TIER_0", "TIER_1", "TIER_2", "TIER_3"],
            default="TIER_2",
            description="Coinbase fee tier for simulation",
        ),
        ParameterDefinition(
            name="slippage_bps",
            parameter_type=ParameterType.INTEGER,
            low=0,
            high=20,
            step=2,
            default=5,
            description="Slippage in basis points (1 bp = 0.01%)",
        ),
        ParameterDefinition(
            name="spread_impact_pct",
            parameter_type=ParameterType.FLOAT,
            low=0.25,
            high=0.75,
            step=0.05,
            default=0.5,
            description="Fraction of spread to apply (0.5 = 50%)",
        ),
    ]


def all_parameter_space() -> tuple[
    list[ParameterDefinition],
    list[ParameterDefinition],
    list[ParameterDefinition],
]:
    """
    Get all default parameter spaces.

    Returns:
        Tuple of (strategy_params, risk_params, simulation_params)
    """
    return (
        strategy_parameter_space(),
        risk_parameter_space(),
        simulation_parameter_space(),
    )
