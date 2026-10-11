"""Tests for kill switch and reduce-only derivation in RiskValidationContainer."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.containers.risk_validation import RiskValidationContainer

KILL_SWITCH_ENV_VAR = "RISK_KILL_SWITCH_ENABLED"


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

    def test_regime_switcher_strategy_uses_strategy_config(
        self, mock_event_store: MagicMock
    ) -> None:
        """Regime-switcher strategy type falls back to strategy config."""
        from gpt_trader.features.live_trade.strategies.baseline import (
            PerpsStrategyConfig,
        )

        config = BotConfig(
            symbols=["BTC-USD"],
            strategy=PerpsStrategyConfig(kill_switch_enabled=True),
            strategy_type="regime_switcher",
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


class TestOperatorKillSwitch:
    """RISK_KILL_SWITCH_ENABLED and BotConfig.kill_switch_enabled reach the live manager (#1300)."""

    def _build(self, config: BotConfig, event_store: MagicMock):
        container = RiskValidationContainer(
            config=config,
            event_store_provider=lambda: event_store,
        )
        return container.risk_manager

    def test_disengaged_by_default(self, mock_event_store: MagicMock) -> None:
        risk_manager = self._build(BotConfig(symbols=["BTC-USD"]), mock_event_store)
        assert risk_manager.config.kill_switch_enabled is False

    @pytest.mark.parametrize("strategy_type", ["baseline", "mean_reversion", "regime_switcher"])
    def test_env_var_engages_kill_switch_for_every_strategy_type(
        self,
        mock_event_store: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        strategy_type: str,
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV_VAR, "1")
        # Built directly, not via from_env: the env must still be honoured.
        config = BotConfig(symbols=["BTC-USD"], strategy_type=strategy_type)  # type: ignore[arg-type]

        risk_manager = self._build(config, mock_event_store)

        assert risk_manager.config.kill_switch_enabled is True

    def test_env_var_zero_leaves_kill_switch_disengaged(
        self, mock_event_store: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV_VAR, "0")
        risk_manager = self._build(BotConfig(symbols=["BTC-USD"]), mock_event_store)
        assert risk_manager.config.kill_switch_enabled is False

    def test_unrecognised_env_value_fails_closed(
        self, mock_event_store: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV_VAR, "enabled")
        risk_manager = self._build(BotConfig(symbols=["BTC-USD"]), mock_event_store)
        assert risk_manager.config.kill_switch_enabled is True

    def test_bot_config_flag_engages_kill_switch(self, mock_event_store: MagicMock) -> None:
        config = BotConfig(symbols=["BTC-USD"], kill_switch_enabled=True)
        risk_manager = self._build(config, mock_event_store)
        assert risk_manager.config.kill_switch_enabled is True

    def test_strategy_flag_off_does_not_clear_env_kill_switch(
        self, mock_event_store: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gpt_trader.features.live_trade.strategies.baseline import (
            PerpsStrategyConfig,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV_VAR, "true")
        config = BotConfig(
            symbols=["BTC-USD"],
            strategy=PerpsStrategyConfig(kill_switch_enabled=False),
            kill_switch_enabled=False,
        )

        risk_manager = self._build(config, mock_event_store)

        assert risk_manager.config.kill_switch_enabled is True
