"""Unit tests for ApplicationContainer registry and services."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import gpt_trader.features.live_trade.bot as bot_module
from gpt_trader.app.config import BotConfig
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    create_application_container,
    get_application_container,
    set_application_container,
)


class TestCreateApplicationContainer:
    """Test cases for create_application_container function."""

    def test_create_application_container(self, mock_config: BotConfig) -> None:
        """Test that application container is created correctly."""
        container = create_application_container(mock_config)

        assert isinstance(container, ApplicationContainer)
        assert container.config == mock_config


class TestContainerRegistry:
    """Test cases for container registry functions."""

    def test_get_returns_none_when_not_set(self) -> None:
        """Test that get_application_container returns None when not set."""
        clear_application_container()
        assert get_application_container() is None

    def test_set_and_get_container(self, mock_config: BotConfig) -> None:
        """Test that set and get work correctly."""
        clear_application_container()

        container = ApplicationContainer(mock_config)
        set_application_container(container)

        assert get_application_container() is container

        clear_application_container()
        assert get_application_container() is None

    def test_clear_container(self, mock_config: BotConfig) -> None:
        """Test that clear_application_container clears the container."""
        container = ApplicationContainer(mock_config)
        set_application_container(container)

        clear_application_container()

        assert get_application_container() is None

    def test_service_resolution_via_registry(self, mock_config: BotConfig) -> None:
        """Test that services can be resolved via registered container."""
        from gpt_trader.app.config.profile_loader import get_profile_loader
        from gpt_trader.features.live_trade.execution.validation import get_failure_tracker

        clear_application_container()

        with pytest.raises(RuntimeError, match="No application container set"):
            get_failure_tracker()
        with pytest.raises(RuntimeError, match="No application container set"):
            get_profile_loader()

        container = ApplicationContainer(mock_config)
        set_application_container(container)

        tracker_container = get_failure_tracker()
        loader_container = get_profile_loader()

        assert tracker_container is container.validation_failure_tracker
        assert loader_container is container.profile_loader

        clear_application_container()


class TestApplicationContainerBotCreation:
    """Test cases for ApplicationContainer.create_bot()."""

    def test_create_bot(self, mock_config: BotConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that TradingBot is created correctly from container."""
        from gpt_trader.app.containers.brokerage import BrokerageContainer

        mock_broker = MagicMock()
        mock_bot = MagicMock()
        mock_create_brokerage = MagicMock()
        mock_create_brokerage.return_value = (
            mock_broker,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        mock_bot_class = MagicMock(return_value=mock_bot)
        monkeypatch.setattr(bot_module, "TradingBot", mock_bot_class)

        container = ApplicationContainer(mock_config)
        container._brokerage = BrokerageContainer(
            config=mock_config,
            event_store_provider=lambda: container.event_store,
            broker_factory=mock_create_brokerage,
        )

        bot = container.create_bot()

        mock_bot_class.assert_called_once()
        call_args = mock_bot_class.call_args

        assert call_args.kwargs["config"] == mock_config
        assert call_args.kwargs["container"] == container
        assert call_args.kwargs["event_store"] == container.event_store
        assert call_args.kwargs["orders_store"] == container.orders_store

        assert bot == mock_bot

    def test_create_bot_includes_notification_service(
        self, mock_config: BotConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that TradingBot is created with notification service."""
        from gpt_trader.app.containers.brokerage import BrokerageContainer

        mock_broker = MagicMock()
        mock_bot = MagicMock()
        mock_create_brokerage = MagicMock()
        mock_create_brokerage.return_value = (
            mock_broker,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        mock_bot_class = MagicMock(return_value=mock_bot)
        monkeypatch.setattr(bot_module, "TradingBot", mock_bot_class)

        container = ApplicationContainer(mock_config)
        container._brokerage = BrokerageContainer(
            config=mock_config,
            event_store_provider=lambda: container.event_store,
            broker_factory=mock_create_brokerage,
        )

        _ = container.create_bot()

        mock_bot_class.assert_called_once()
        call_args = mock_bot_class.call_args

        assert call_args.kwargs["notification_service"] is not None
        assert call_args.kwargs["notification_service"] == container.notification_service


class TestApplicationContainerSecondaryServices:
    """Test lazy construction for secondary services."""

    def test_validation_failure_tracker_creation(self, mock_config: BotConfig) -> None:
        """Test that validation failure tracker is created correctly."""
        from gpt_trader.features.live_trade.execution.validation import ValidationFailureTracker

        container = ApplicationContainer(mock_config)

        tracker = container.validation_failure_tracker

        assert isinstance(tracker, ValidationFailureTracker)
        assert container._risk_validation._validation_failure_tracker == tracker
        assert tracker.escalation_threshold == 5
        assert tracker.escalation_callback is None

        tracker2 = container.validation_failure_tracker
        assert tracker is tracker2

    def test_profile_loader_creation(self, mock_config: BotConfig) -> None:
        """Test that profile loader is created correctly."""
        from gpt_trader.app.config.profile_loader import ProfileLoader

        container = ApplicationContainer(mock_config)

        loader = container.profile_loader

        assert isinstance(loader, ProfileLoader)
        assert container._config_container._profile_loader == loader

        loader2 = container.profile_loader
        assert loader is loader2

    def test_health_state_creation(self, mock_config: BotConfig) -> None:
        """Test that health state is created correctly."""
        from gpt_trader.app.health_server import HealthState

        container = ApplicationContainer(mock_config)

        health_state = container.health_state

        assert isinstance(health_state, HealthState)
        assert container._observability._health_state == health_state
        assert health_state.ready is False
        assert health_state.live is True

        health_state2 = container.health_state
        assert health_state is health_state2

    def test_secrets_manager_creation(self, mock_config: BotConfig) -> None:
        """Test that secrets manager is created correctly."""
        from gpt_trader.security.secrets_manager import SecretsManager

        container = ApplicationContainer(mock_config)

        secrets_manager = container.secrets_manager

        assert isinstance(secrets_manager, SecretsManager)
        assert container._observability._secrets_manager == secrets_manager
        assert secrets_manager._config == mock_config

        secrets_manager2 = container.secrets_manager
        assert secrets_manager is secrets_manager2


class TestRiskBudgetRuntimeSeedGate:
    """Stage 2 derivation seam (#1120): default-on startup seeding."""

    def test_gate_disabled_resolves_no_seed(self, mock_config: BotConfig) -> None:
        mock_config.risk_budget_runtime_seed_enabled = False

        container = ApplicationContainer(mock_config)

        assert container._risk_budget_seed is None
        assert container._risk_validation._risk_budget_seed is None

    def test_default_gate_seeds_and_gates_shorts(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(tmp_path))
        # No explicit gate value: seeding must engage by default.
        config = BotConfig(symbols=["BTC-USD"])
        config.strategy.enable_shorts = True

        container = ApplicationContainer(config)

        seed = container._risk_budget_seed
        assert seed is not None
        # Missing budget log falls back to the default budget (version 1),
        # whose permissions forbid naked shorts.
        assert seed.budget_version == 1
        assert seed.budget_source == "default"
        assert config.strategy.enable_shorts is False
        assert config.active_enable_shorts is False
        assert container._risk_validation._risk_budget_seed is seed
