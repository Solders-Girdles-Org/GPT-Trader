"""Tests for kill switch and reduce-only derivation in RiskValidationContainer."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.containers.risk_validation import RiskValidationContainer


@pytest.fixture
def mock_event_store() -> MagicMock:
    """Create a mock EventStore."""
    return MagicMock()


class TestKillSwitchDerivation:
    """Test kill_switch_enabled derivation from active strategy config."""

    def test_baseline_strategy_uses_strategy_config(self, mock_event_store: MagicMock) -> None:
        """Baseline strategy type derives kill_switch from strategy config."""
        from gpt_trader.features.live_trade.strategies.baseline import (
            PerpsStrategyConfig,
        )

        config = BotConfig(
            symbols=["BTC-USD"],
            strategy=PerpsStrategyConfig(kill_switch_enabled=True),
            strategy_type="baseline",
        )

        container = RiskValidationContainer(
            config=config,
            event_store_provider=lambda: mock_event_store,
        )

        risk_manager = container.risk_manager
        assert risk_manager.config.kill_switch_enabled is True

    def test_mean_reversion_strategy_uses_mean_reversion_config(
        self, mock_event_store: MagicMock
    ) -> None:
        """Mean reversion strategy type derives kill_switch from mean_reversion config."""
        from gpt_trader.app.config.bot_config import MeanReversionConfig

        config = BotConfig(
            symbols=["BTC-USD"],
            mean_reversion=MeanReversionConfig(kill_switch_enabled=True),
            strategy_type="mean_reversion",
        )

        container = RiskValidationContainer(
            config=config,
            event_store_provider=lambda: mock_event_store,
        )

        risk_manager = container.risk_manager
        assert risk_manager.config.kill_switch_enabled is True

    def test_ensemble_strategy_uses_strategy_config(self, mock_event_store: MagicMock) -> None:
        """Ensemble strategy type falls back to strategy config."""
        from gpt_trader.features.live_trade.strategies.baseline import (
            PerpsStrategyConfig,
        )

        config = BotConfig(
            symbols=["BTC-USD"],
            strategy=PerpsStrategyConfig(kill_switch_enabled=True),
            strategy_type="ensemble",
        )

        container = RiskValidationContainer(
            config=config,
            event_store_provider=lambda: mock_event_store,
        )

        risk_manager = container.risk_manager
        assert risk_manager.config.kill_switch_enabled is True

    def test_reduce_only_mode_from_config(self, mock_event_store: MagicMock) -> None:
        """Test reduce_only_mode is passed through to RiskConfig."""
        config = BotConfig(
            symbols=["BTC-USD"],
            reduce_only_mode=True,
        )

        container = RiskValidationContainer(
            config=config,
            event_store_provider=lambda: mock_event_store,
        )

        risk_manager = container.risk_manager
        assert risk_manager.config.reduce_only_mode is True
