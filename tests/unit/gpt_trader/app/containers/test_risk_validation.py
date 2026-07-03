"""Tests for RiskValidationContainer."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.containers.risk_validation import RiskValidationContainer


@pytest.fixture
def mock_config() -> BotConfig:
    """Create a mock BotConfig with risk settings."""
    return BotConfig(
        symbols=["BTC-USD"],
    )


@pytest.fixture
def mock_event_store() -> MagicMock:
    """Create a mock EventStore."""
    return MagicMock()


class TestRiskValidationContainer:
    """Test cases for RiskValidationContainer."""

    def test_initialization(self, mock_config: BotConfig, mock_event_store: MagicMock) -> None:
        """Test that container initializes correctly."""
        container = RiskValidationContainer(
            config=mock_config,
            event_store_provider=lambda: mock_event_store,
        )

        assert container._config == mock_config
        assert container._risk_manager is None
        assert container._validation_failure_tracker is None

    def test_risk_manager_lazy_creation(
        self, mock_config: BotConfig, mock_event_store: MagicMock
    ) -> None:
        """Test that risk manager is created lazily on first access."""
        from gpt_trader.features.live_trade.risk.manager import LiveRiskManager

        container = RiskValidationContainer(
            config=mock_config,
            event_store_provider=lambda: mock_event_store,
        )

        # Should be None before access
        assert container._risk_manager is None

        # First access should create the manager
        risk_manager = container.risk_manager

        assert risk_manager is not None
        assert isinstance(risk_manager, LiveRiskManager)
        assert container._risk_manager is risk_manager

        # Second access should return same instance
        risk_manager2 = container.risk_manager
        assert risk_manager is risk_manager2

    def test_validation_failure_tracker_lazy_creation(
        self, mock_config: BotConfig, mock_event_store: MagicMock
    ) -> None:
        """Test that validation tracker is created lazily on first access."""
        from gpt_trader.features.live_trade.execution.validation import ValidationFailureTracker

        container = RiskValidationContainer(
            config=mock_config,
            event_store_provider=lambda: mock_event_store,
        )

        # Should be None before access
        assert container._validation_failure_tracker is None

        # First access should create the tracker
        tracker = container.validation_failure_tracker

        assert tracker is not None
        assert isinstance(tracker, ValidationFailureTracker)
        assert container._validation_failure_tracker is tracker

        # Verify default configuration
        assert tracker.escalation_threshold == 5
        assert tracker.escalation_callback is None

        # Second access should return same instance
        tracker2 = container.validation_failure_tracker
        assert tracker is tracker2

    def test_reset_risk_manager(self, mock_config: BotConfig, mock_event_store: MagicMock) -> None:
        """Test that risk manager can be reset."""
        container = RiskValidationContainer(
            config=mock_config,
            event_store_provider=lambda: mock_event_store,
        )

        # Create the manager
        rm1 = container.risk_manager
        assert rm1 is not None

        # Reset it
        container.reset_risk_manager()
        assert container._risk_manager is None

        # Next access should create new instance
        rm2 = container.risk_manager
        assert rm2 is not None
        assert rm2 is not rm1

    def test_reset_validation_failure_tracker(
        self, mock_config: BotConfig, mock_event_store: MagicMock
    ) -> None:
        """Test that validation tracker can be reset."""
        container = RiskValidationContainer(
            config=mock_config,
            event_store_provider=lambda: mock_event_store,
        )

        # Create the tracker
        t1 = container.validation_failure_tracker
        assert t1 is not None

        # Reset it
        container.reset_validation_failure_tracker()
        assert container._validation_failure_tracker is None

        # Next access should create new instance
        t2 = container.validation_failure_tracker
        assert t2 is not None
        assert t2 is not t1

    def test_risk_manager_uses_config_values(self, mock_event_store: MagicMock) -> None:
        """Test that risk manager is created with config values."""
        from gpt_trader.app.config import BotRiskConfig

        # Create config with specific risk settings
        config = BotConfig(
            symbols=["BTC-USD"],
            risk=BotRiskConfig(
                max_leverage=5,
                daily_loss_limit_pct=0.10,
                position_fraction=Decimal("0.25"),
            ),
        )

        container = RiskValidationContainer(
            config=config,
            event_store_provider=lambda: mock_event_store,
        )

        risk_manager = container.risk_manager

        # Verify config was applied
        assert risk_manager.config.max_leverage == 5
        assert risk_manager.config.daily_loss_limit_pct == 0.10
        assert risk_manager.config.max_position_pct_per_symbol == 0.25
        assert risk_manager.state_file is None

    def test_risk_manager_scopes_state_file_by_profile(self, mock_event_store: MagicMock) -> None:
        from gpt_trader.config.types import Profile

        config = BotConfig(
            symbols=["BTC-USD"],
            profile=Profile.CANARY,
        )
        container = RiskValidationContainer(
            config=config,
            event_store_provider=lambda: mock_event_store,
        )

        risk_manager = container.risk_manager
        expected = str(Path(config.runtime_root) / "runtime_data" / "canary" / "risk_state.json")
        assert risk_manager.state_file == expected


def _runtime_seed(*, allow_futures_leverage: bool = False) -> object:
    from gpt_trader.app.risk_budget_seed import RiskBudgetRuntimeSeed

    return RiskBudgetRuntimeSeed(
        budget_version=1,
        budget_source="default",
        daily_loss_limit_pct=0.10,
        max_exposure_pct=1.0,
        allow_futures_leverage=allow_futures_leverage,
        allow_naked_shorts=False,
    )


class TestRiskBudgetSeedApplication:
    """Stage 2 derivation seam (#1120): budget-seeded runtime limits."""

    def test_no_seed_keeps_bot_risk_defaults(self, mock_event_store: MagicMock) -> None:
        container = RiskValidationContainer(
            config=BotConfig(symbols=["BTC-USD"]),
            event_store_provider=lambda: mock_event_store,
        )

        risk_manager = container.risk_manager

        assert risk_manager.config.daily_loss_limit_pct == 0.05
        assert risk_manager.config.max_exposure_pct == 0.8
        assert risk_manager.config.cfm_max_leverage == 5
        mock_event_store.append.assert_not_called()

    def test_seed_overrides_appetite_fields_and_clamps_cfm(
        self, mock_event_store: MagicMock
    ) -> None:
        container = RiskValidationContainer(
            config=BotConfig(symbols=["BTC-USD"]),
            event_store_provider=lambda: mock_event_store,
            risk_budget_seed=_runtime_seed(),
        )

        risk_manager = container.risk_manager

        assert risk_manager.config.daily_loss_limit_pct == 0.10
        assert risk_manager.config.max_exposure_pct == 1.0
        assert risk_manager.config.cfm_max_leverage == 1

    def test_futures_permission_keeps_cfm_caps(self, mock_event_store: MagicMock) -> None:
        container = RiskValidationContainer(
            config=BotConfig(symbols=["BTC-USD"]),
            event_store_provider=lambda: mock_event_store,
            risk_budget_seed=_runtime_seed(allow_futures_leverage=True),
        )

        assert container.risk_manager.config.cfm_max_leverage == 5

    def test_seed_emits_startup_telemetry_once(self, mock_event_store: MagicMock) -> None:
        from gpt_trader.app.risk_budget_seed import RISK_BUDGET_SEED_EVENT_TYPE

        container = RiskValidationContainer(
            config=BotConfig(symbols=["BTC-USD"]),
            event_store_provider=lambda: mock_event_store,
            risk_budget_seed=_runtime_seed(),
        )

        container.risk_manager
        container.risk_manager  # cached access must not re-emit

        mock_event_store.append.assert_called_once_with(
            RISK_BUDGET_SEED_EVENT_TYPE,
            {
                "budget_version": 1,
                "budget_source": "default",
                "daily_loss_limit_pct": 0.10,
                "max_exposure_pct": 1.0,
                "allow_futures_leverage": False,
                "allow_naked_shorts": False,
            },
        )
