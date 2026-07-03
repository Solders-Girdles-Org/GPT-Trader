"""
Strategy factory for creating trading strategy instances.

This module provides a factory function that creates the appropriate
strategy based on the configuration, enabling runtime strategy selection.
"""

from typing import TYPE_CHECKING

from gpt_trader.features.live_trade.interfaces import TradingStrategy
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.app.config import BotConfig

logger = get_logger(__name__, component="strategy_factory")


def create_strategy(config: "BotConfig") -> TradingStrategy:
    """Create a trading strategy based on configuration.

    Args:
        config: Bot configuration containing strategy_type and strategy-specific params

    Returns:
        A TradingStrategy instance ready for use by TradingEngine

    Raises:
        ValueError: If strategy_type is not recognized
    """
    strategy_type = config.strategy_type

    if strategy_type == "baseline":
        from gpt_trader.features.live_trade.strategies.baseline import (
            BaselinePerpsStrategy,
        )

        logger.info("Creating BaselinePerpsStrategy (RSI + MA crossover)")
        return BaselinePerpsStrategy(config=config.strategy)

    elif strategy_type == "mean_reversion":
        from gpt_trader.features.live_trade.strategies.mean_reversion import (
            MeanReversionStrategy,
        )

        logger.info("Creating MeanReversionStrategy (Z-Score based)")
        return MeanReversionStrategy(config=config.mean_reversion)

    elif strategy_type == "ensemble":
        from gpt_trader.features.live_trade.strategies.ensemble import (
            EnsembleStrategy,
            EnsembleStrategyConfig,
        )

        logger.info("Creating EnsembleStrategy (Signal Ensemble Architecture)")

        # Use provided ensemble_config or default
        ensemble_config = config.ensemble_config
        if ensemble_config is None or not isinstance(ensemble_config, EnsembleStrategyConfig):
            # Try to parse from dict if it's a dict
            if isinstance(ensemble_config, dict):
                # This requires EnsembleStrategyConfig to be constructible from dict
                # For now, we'll just use default if parsing fails or implement a helper
                # But since we haven't implemented from_dict for EnsembleStrategyConfig,
                # let's assume it's passed correctly or use default.
                # Actually, let's try to unpack if it matches fields
                try:
                    ensemble_config = EnsembleStrategyConfig(**ensemble_config)
                except (TypeError, ValueError) as e:
                    logger.warning("Failed to parse ensemble_config dict: %s, using default", e)
                    ensemble_config = EnsembleStrategyConfig()
            else:
                ensemble_config = EnsembleStrategyConfig()

        return EnsembleStrategy(config=ensemble_config)

    elif strategy_type == "regime_switcher":
        from gpt_trader.features.intelligence.regime import MarketRegimeDetector, RegimeConfig
        from gpt_trader.features.live_trade.strategies.baseline import (
            BaselinePerpsStrategy,
            SpotStrategy,
        )
        from gpt_trader.features.live_trade.strategies.mean_reversion import (
            MeanReversionStrategy,
        )
        from gpt_trader.features.live_trade.strategies.regime_switcher import (
            RegimeSwitchingStrategy,
        )

        logger.info("Creating RegimeSwitchingStrategy (MarketRegimeDetector hard switch)")

        regime_config = config.regime_config
        if isinstance(regime_config, dict):
            detector_config = RegimeConfig.from_dict(regime_config)
        elif isinstance(regime_config, RegimeConfig):
            detector_config = regime_config
        else:
            detector_config = RegimeConfig()

        detector = MarketRegimeDetector(detector_config)

        trend_lookback = max(
            64,
            max(
                int(
                    getattr(
                        config.strategy, "long_ma_period", getattr(config.strategy, "long_ma", 20)
                    )
                ),
                int(getattr(config.strategy, "rsi_period", 14)) + 1,
            )
            + 10,
        )
        mean_reversion_lookback = max(
            64, int(getattr(config.mean_reversion, "lookback_window", 20))
        )
        if getattr(config.mean_reversion, "trend_filter_enabled", False):
            mean_reversion_lookback = max(
                mean_reversion_lookback,
                int(getattr(config.mean_reversion, "trend_window", mean_reversion_lookback)),
            )
        required_lookback = max(trend_lookback, mean_reversion_lookback)

        def trend_factory() -> TradingStrategy:
            if not config.active_enable_shorts:
                return SpotStrategy(config=config.strategy)
            return BaselinePerpsStrategy(config=config.strategy)

        def mean_reversion_factory() -> TradingStrategy:
            return MeanReversionStrategy(config=config.mean_reversion)

        return RegimeSwitchingStrategy(
            trend_strategy_factory=trend_factory,
            mean_reversion_strategy_factory=mean_reversion_factory,
            regime_detector=detector,
            required_lookback_bars=required_lookback,
            trend_mode=config.regime_switcher_trend_mode,
            enable_shorts=config.active_enable_shorts,
        )

    else:
        raise ValueError(
            f"Unknown strategy_type: {strategy_type!r}. "
            f"Valid options: 'baseline', 'mean_reversion', 'ensemble', 'regime_switcher'"
        )
