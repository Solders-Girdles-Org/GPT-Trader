from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from gpt_trader.app.config import BotConfig
from gpt_trader.app.config.controller import ConfigController
from gpt_trader.app.containers.brokerage import BrokerageContainer
from gpt_trader.app.containers.config import ConfigContainer
from gpt_trader.app.containers.observability import ObservabilityContainer
from gpt_trader.app.containers.persistence import PersistenceContainer
from gpt_trader.app.containers.risk_validation import RiskValidationContainer
from gpt_trader.app.health_server import HealthState
from gpt_trader.app.protocols import EventStoreProtocol
from gpt_trader.app.runtime import (
    RuntimePaths,
    RuntimeSettingsSnapshot,
    create_runtime_settings_snapshot,
)
from gpt_trader.app.runtime.fingerprint import (
    StartupConfigFingerprint,
    compute_startup_config_fingerprint,
    write_startup_config_fingerprint,
)
from gpt_trader.config.types import Profile
from gpt_trader.core.protocols import BrokerProtocol
from gpt_trader.features.brokerages.coinbase.market_data_service import MarketDataService
from gpt_trader.features.brokerages.coinbase.utilities import ProductCatalog
from gpt_trader.features.brokerages.factory import create_brokerage
from gpt_trader.persistence.event_store import EventStore
from gpt_trader.persistence.orders_store import OrdersStore
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.app.config.profile_loader import ProfileLoader
    from gpt_trader.app.risk_budget_seed import RiskBudgetRuntimeSeed
    from gpt_trader.features.live_trade.bot import TradingBot
    from gpt_trader.features.live_trade.execution.validation import ValidationFailureTracker
    from gpt_trader.features.live_trade.risk.manager import LiveRiskManager
    from gpt_trader.monitoring.notifications.service import NotificationService
    from gpt_trader.security.secrets_manager import SecretsManager


logger = get_logger(__name__, component="container")


class ApplicationContainer:
    """
    Canonical composition root for GPT-Trader application.

    This container lazily initializes all application services and provides
    the single source of truth for dependency injection. All services should
    be accessed through this container.

    Usage:
        container = ApplicationContainer(config)
        bot = container.create_bot()  # Creates fully-wired TradingBot
    """

    def __init__(self, config: BotConfig):
        self.config = config
        # Stage 2 derivation seam (default ON): resolve the active RiskBudget
        # version once at startup so the shorts gate and the runtime risk
        # limits are seeded from the same version, before the settings
        # snapshot/fingerprint capture the effective config. See
        # docs/decisions/canonical-risk-limit-vocabulary.md (#1120).
        self._risk_budget_seed: RiskBudgetRuntimeSeed | None = None
        if config.risk_budget_runtime_seed_enabled:
            from gpt_trader.app.risk_budget_seed import (
                apply_shorts_permission,
                resolve_risk_budget_runtime_seed,
            )

            self._risk_budget_seed = resolve_risk_budget_runtime_seed()
            apply_shorts_permission(config, self._risk_budget_seed)
            logger.info(
                "Runtime risk limits seeded from risk budget",
                budget_version=self._risk_budget_seed.budget_version,
                budget_source=self._risk_budget_seed.budget_source,
            )
        self._runtime_settings_snapshot = create_runtime_settings_snapshot(config)
        self._startup_config_fingerprint = compute_startup_config_fingerprint(
            self._runtime_settings_snapshot
        )

        # Sub-containers (lazily delegate to these for grouped dependencies)
        self._config_container = ConfigContainer(config=config)
        self._observability = ObservabilityContainer(config=config)
        self._persistence = PersistenceContainer(
            config=config,
            runtime_settings_snapshot=self._runtime_settings_snapshot,
            profile_provider=lambda: cast(Profile, config.profile),
        )
        self._brokerage = BrokerageContainer(
            config=config,
            event_store_provider=lambda: self.event_store,
            broker_factory=create_brokerage,
        )
        self._risk_validation = RiskValidationContainer(
            config=config,
            event_store_provider=lambda: self.event_store,
            risk_budget_seed=self._risk_budget_seed,
        )

    @property
    def config_controller(self) -> ConfigController:
        """Delegate to ConfigContainer."""
        return self._config_container.config_controller

    @property
    def runtime_paths(self) -> RuntimePaths:
        """Delegate to PersistenceContainer."""
        return self._persistence.runtime_paths

    @property
    def startup_config_fingerprint(self) -> StartupConfigFingerprint:
        """Immutable fingerprint of the startup configuration."""
        return self._startup_config_fingerprint

    def persist_startup_config_fingerprint(self) -> Path:
        """Persist the startup configuration fingerprint for diagnostics."""
        path = self.runtime_paths.config_fingerprint_path
        write_startup_config_fingerprint(path, self._startup_config_fingerprint)
        return path

    @property
    def runtime_settings_snapshot(self) -> RuntimeSettingsSnapshot:
        """Immutable snapshot of the runtime configuration."""
        return self._runtime_settings_snapshot

    @property
    def event_store(self) -> EventStore:
        """Delegate to PersistenceContainer."""
        return self._persistence.event_store

    @property
    def orders_store(self) -> OrdersStore:
        """Delegate to PersistenceContainer."""
        return self._persistence.orders_store

    @property
    def market_data_service(self) -> MarketDataService:
        """Delegate to BrokerageContainer."""
        return self._brokerage.market_data_service

    @property
    def product_catalog(self) -> ProductCatalog:
        """Delegate to BrokerageContainer."""
        return self._brokerage.product_catalog

    @property
    def broker(self) -> BrokerProtocol:
        """Delegate to BrokerageContainer."""
        return cast(BrokerProtocol, self._brokerage.broker)

    @property
    def risk_manager(self) -> LiveRiskManager:
        """Delegate to RiskValidationContainer."""
        return self._risk_validation.risk_manager

    @property
    def notification_service(self) -> NotificationService:
        """Delegate to ObservabilityContainer."""
        return self._observability.notification_service

    @property
    def validation_failure_tracker(self) -> ValidationFailureTracker:
        """Delegate to RiskValidationContainer."""
        return self._risk_validation.validation_failure_tracker

    @property
    def profile_loader(self) -> ProfileLoader:
        """Delegate to ConfigContainer."""
        return self._config_container.profile_loader

    @property
    def health_state(self) -> HealthState:
        """Delegate to ObservabilityContainer."""
        return self._observability.health_state

    @property
    def secrets_manager(self) -> SecretsManager:
        """Delegate to ObservabilityContainer."""
        return self._observability.secrets_manager

    def reset_broker(self) -> None:
        """Delegate to BrokerageContainer."""
        self._brokerage.reset_broker()

    def reset_config(self) -> None:
        """Delegate to ConfigContainer."""
        self._config_container.reset_config()

    def reset_risk_manager(self) -> None:
        """Delegate to RiskValidationContainer."""
        self._risk_validation.reset_risk_manager()

    def reset_validation_failure_tracker(self) -> None:
        """Delegate to RiskValidationContainer."""
        self._risk_validation.reset_validation_failure_tracker()

    def create_bot(self) -> TradingBot:
        """
        Create a fully-wired TradingBot instance.

        This is the canonical way to create a TradingBot. The container
        provides all necessary dependencies.

        Returns:
            TradingBot: A fully configured trading bot ready to run.
        """
        from gpt_trader.features.live_trade.bot import TradingBot

        return TradingBot(
            config=self.config,
            container=self,
            event_store=cast(EventStoreProtocol, self.event_store),
            orders_store=self.orders_store,
            notification_service=self.notification_service,
        )


def create_application_container(
    config: BotConfig,
) -> ApplicationContainer:
    return ApplicationContainer(config)


# ---------------------------------------------------------------------------
# Module-level container registry
# ---------------------------------------------------------------------------
# Allows services to resolve dependencies via get_application_container()
# without requiring explicit container passing. The container must be set
# during application startup via set_application_container().

_current_container: ApplicationContainer | None = None


def _sync_validation_failure_tracker_provider(container: ApplicationContainer | None) -> None:
    from gpt_trader.features.live_trade.execution.validation import (
        set_failure_tracker_provider,
    )

    if container is None:
        set_failure_tracker_provider(None)
        return

    set_failure_tracker_provider(lambda: container.validation_failure_tracker)


def set_application_container(container: ApplicationContainer | None) -> None:
    """Set the current application container for global access.

    Call this during application startup after creating the container.
    Pass None to clear the container (e.g., during shutdown or tests).

    Args:
        container: The ApplicationContainer instance, or None to clear.
    """
    global _current_container
    if container is not None:
        config = getattr(container, "config", None)
        profile = getattr(config, "profile", None) if config else None
        logger.debug("Application container set", profile=profile)
    else:
        logger.debug("Application container cleared via set_application_container(None)")
    _current_container = container
    _sync_validation_failure_tracker_provider(container)


def get_application_container() -> ApplicationContainer | None:
    """Get the current application container.

    Returns:
        The current ApplicationContainer if set, None otherwise.

    Note:
        Returns None if no container has been set. Callers should handle
        this case gracefully (e.g., fall back to creating services directly).
    """
    return _current_container


def clear_application_container() -> None:
    """Clear the current application container.

    Convenience function equivalent to set_application_container(None).
    Useful in tests to ensure clean state between test cases.
    """
    global _current_container
    was_set = _current_container is not None
    _current_container = None
    _sync_validation_failure_tracker_provider(None)
    if was_set:
        logger.debug("Application container cleared")
