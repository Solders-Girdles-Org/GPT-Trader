"""Tests for TradingBot initialization."""

from __future__ import annotations

from unittest.mock import ANY, Mock

import pytest

import gpt_trader.features.live_trade.bot as bot_module
from gpt_trader.features.live_trade.account_snapshot import AccountSnapshotService
from gpt_trader.features.live_trade.bot import TradingBot


class TestTradingBotInitialization:
    """Test TradingBot initialization."""

    @pytest.fixture
    def engine_mock(self, monkeypatch: pytest.MonkeyPatch) -> Mock:
        mock_engine = Mock()
        monkeypatch.setattr(bot_module, "TradingEngine", mock_engine)
        return mock_engine

    @pytest.fixture
    def context_mock(self, monkeypatch: pytest.MonkeyPatch) -> Mock:
        mock_context = Mock()
        monkeypatch.setattr(bot_module, "CoordinatorContext", mock_context)
        return mock_context

    @pytest.fixture
    def mock_config(self) -> Mock:
        """Create a mock BotConfig."""
        config = Mock()
        config.symbols = ["BTC-PERP-USDC", "ETH-PERP-USDC"]
        config.interval = 60
        return config

    def test_init_with_config_only(self, mock_config: Mock, engine_mock: Mock) -> None:
        """Test initialization with only config."""
        mock_container = Mock()
        # Set container attributes to None for this test
        mock_container.broker = None
        mock_container.risk_manager = None
        mock_container.event_store = Mock()
        mock_container.orders_store = Mock()
        mock_container.notification_service = Mock()

        mock_container.runtime_state = Mock()

        engine_mock.return_value = Mock()
        bot = TradingBot(config=mock_config, container=mock_container)

        assert bot.broker is mock_container.broker
        assert bot.risk_manager is mock_container.risk_manager
        assert bot.runtime_state is mock_container.runtime_state
        # No broker means no snapshot provider (account-snapshot ADR Option A).
        assert bot.account_telemetry is None

    def test_init_with_container(self, mock_config: Mock, engine_mock: Mock) -> None:
        """Test initialization with container."""
        mock_container = Mock()

        engine_mock.return_value = Mock()
        bot = TradingBot(config=mock_config, container=mock_container)

        assert bot.container is mock_container
        # A wired broker yields a working read-only snapshot provider
        # (account-snapshot ADR Option A, #1121).
        assert isinstance(bot.account_telemetry, AccountSnapshotService)
        assert bot.account_telemetry.supports_snapshots() is True

    def test_init_creates_context(
        self,
        mock_config: Mock,
        engine_mock: Mock,
        context_mock: Mock,
    ) -> None:
        """Test that initialization creates CoordinatorContext."""
        mock_container = Mock()
        mock_container.broker = Mock()
        mock_container.risk_manager = Mock()
        mock_container.event_store = Mock()
        mock_container.orders_store = Mock()
        mock_container.notification_service = Mock()

        engine_mock.return_value = Mock()
        context_mock.return_value = Mock()

        bot = TradingBot(config=mock_config, container=mock_container)

        context_mock.assert_called_once_with(
            config=mock_config,
            container=mock_container,
            broker=mock_container.broker,
            broker_calls=ANY,
            symbols=tuple(mock_config.symbols),
            risk_manager=mock_container.risk_manager,
            event_store=mock_container.event_store,
            orders_store=mock_container.orders_store,
            notification_service=mock_container.notification_service,
            price_tick_store=ANY,
        )
        assert bot.context is not None

    def test_init_creates_engine(self, mock_config: Mock, engine_mock: Mock) -> None:
        """Test that initialization creates TradingEngine."""
        mock_container = Mock()
        engine_mock.return_value = Mock()
        bot = TradingBot(config=mock_config, container=mock_container)

        assert bot.engine is not None
        engine_mock.assert_called_once()

    def test_init_container_missing_optional_attributes(
        self, mock_config: Mock, engine_mock: Mock
    ) -> None:
        """Test initialization handles container with missing optional attributes."""
        from types import SimpleNamespace

        # Use SimpleNamespace with only required attributes
        mock_container = SimpleNamespace(
            broker=Mock(),
            risk_manager=Mock(),
            event_store=Mock(),
            orders_store=None,
            notification_service=Mock(),
            # Optional attributes not set - getattr will return None
        )

        engine_mock.return_value = Mock()
        bot = TradingBot(config=mock_config, container=mock_container)

        assert bot.broker is mock_container.broker
        assert bot.runtime_state is None  # Not set in SimpleNamespace
