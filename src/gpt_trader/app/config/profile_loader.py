"""
Unified profile loading from YAML configuration files.

Provides YAML-first profile configuration with hardcoded fallbacks.
All profiles can be defined in YAML files under config/profiles/.

Schema Structure:
    profile_name: str
    environment: str
    trading:
        symbols: list[str]
        mode: str  # normal | reduce_only | paper
    strategy:
        type: str
        short_ma_period: int
        long_ma_period: int
    risk_management:
        max_leverage: int
        max_position_size: Decimal
        enable_shorts: bool
        daily_loss_limit_pct: float
    execution:
        time_in_force: str
        dry_run: bool
        mock_broker: bool
    session:
        start_time: str (HH:MM)
        end_time: str (HH:MM)
        trading_days: list[str]
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from gpt_trader.app.config.profile_error_payloads import (
    profile_yaml_missing_payload,
    profile_yaml_parse_error_payload,
)
from gpt_trader.app.config.profile_schema import (
    ExecutionConfig,
    MonitoringConfig,
    ProfileSchema,
    ProfileValidationError,
    RiskConfig,
    SessionConfig,
    StrategyConfig,
    TradingConfig,
)
from gpt_trader.config import path_registry
from gpt_trader.config.types import Profile
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.app.config.bot_config import BotConfig

logger = get_logger(__name__, component="profile_loader")


class ProfileOverrideSource(Enum):
    """Describes which source supplied the resolved profile."""

    CLI = "cli"
    FILE = "file"
    ENV = "env"
    DEFAULT = "default"


@dataclass(frozen=True)
class ProfileOverrideDecision:
    """Pure result of evaluating profile overrides."""

    profile: str
    source: ProfileOverrideSource


def _normalize_profile_candidate(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if normalized else None


def resolve_profile_override(
    *,
    cli_profile: str | None,
    file_profile: str | None,
    env_profile: str | None,
    default_profile: str,
) -> ProfileOverrideDecision:
    """Choose the active profile according to CLI > file > env > default."""

    candidates: list[tuple[ProfileOverrideSource, str | None]] = [
        (ProfileOverrideSource.CLI, _normalize_profile_candidate(cli_profile)),
        (ProfileOverrideSource.FILE, _normalize_profile_candidate(file_profile)),
        (ProfileOverrideSource.ENV, _normalize_profile_candidate(env_profile)),
    ]
    for source, value in candidates:
        if value is not None:
            return ProfileOverrideDecision(profile=value, source=source)

    normalized_default = _normalize_profile_candidate(default_profile)
    return ProfileOverrideDecision(
        profile=normalized_default or default_profile,
        source=ProfileOverrideSource.DEFAULT,
    )


def get_env_profile_override() -> str | None:
    """Return a profile override from the environment, if any."""

    primary_override = _normalize_profile_candidate(os.getenv("GPT_TRADER_PROFILE"))
    if primary_override is not None:
        return primary_override
    return _normalize_profile_candidate(os.getenv("BOT_PROFILE"))


@dataclass(frozen=True)
class ProfileRegistryEntry:
    """Shared metadata for runtime and preflight profile consumers."""

    profile: Profile
    schema: ProfileSchema
    env_defaults: Mapping[str, tuple[str, bool]]
    preflight_supported: bool = False
    preflight_default: bool = False

    @property
    def profile_name(self) -> str:
        return self.profile.value

    @property
    def yaml_filename(self) -> str:
        return f"{self.profile.value}.yaml"


# Environment defaults grouped by prod-like/dev-like semantics so multiple
# profiles share the same expectations.
_DEV_ENV_DEFAULTS = MappingProxyType(
    {
        "BROKER": ("coinbase", True),
        "COINBASE_SANDBOX": ("1", False),
        "COINBASE_API_MODE": ("advanced", False),
    }
)
_PROD_ENV_DEFAULTS = MappingProxyType(
    {
        "BROKER": ("coinbase", True),
        "COINBASE_SANDBOX": ("0", True),
        "COINBASE_API_MODE": ("advanced", True),
    }
)

# Profiles that are treated as dev/test/paper style for env defaults + remote checks
_DEV_LIKE_PROFILES = {Profile.DEV, Profile.TEST, Profile.PAPER}


# Hardcoded profile defaults for when YAML files don't exist
_PROFILE_DEFAULTS: dict[Profile, ProfileSchema] = {
    Profile.DEV: ProfileSchema(
        profile_name="dev",
        environment="development",
        description="Development profile with mock broker",
        trading=TradingConfig(symbols=["BTC-USD", "ETH-USD"], mode="normal"),
        risk=RiskConfig(max_position_size=Decimal("10000"), enable_shorts=True),
        execution=ExecutionConfig(
            dry_run=True,
            mock_broker=True,
            mock_fills=True,
            use_limit_orders=False,
            market_order_fallback=True,
        ),
        monitoring=MonitoringConfig(log_level="DEBUG"),
    ),
    Profile.TEST: ProfileSchema(
        profile_name="test",
        environment="test",
        description="Test profile for unit/integration tests",
        trading=TradingConfig(symbols=["BTC-USD"], mode="normal"),
        risk=RiskConfig(max_position_size=Decimal("1000")),
        execution=ExecutionConfig(
            dry_run=True,
            mock_broker=True,
            mock_fills=True,
            use_limit_orders=False,
            market_order_fallback=True,
        ),
        monitoring=MonitoringConfig(log_level="DEBUG"),
    ),
    Profile.DEMO: ProfileSchema(
        profile_name="demo",
        environment="demo",
        description="Conservative demo profile for demonstrations",
        trading=TradingConfig(symbols=["BTC-USD"], mode="normal"),
        risk=RiskConfig(
            max_position_size=Decimal("100"),
            max_leverage=1,
            enable_shorts=False,
        ),
        execution=ExecutionConfig(
            dry_run=False,
            mock_broker=False,
            use_limit_orders=False,
            market_order_fallback=True,
        ),
    ),
    Profile.SPOT: ProfileSchema(
        profile_name="spot",
        environment="production",
        description="Production spot trading profile",
        trading=TradingConfig(symbols=["BTC-USD", "ETH-USD"], mode="normal"),
        risk=RiskConfig(
            max_position_size=Decimal("50000"),
            max_leverage=1,
            enable_shorts=False,
        ),
        execution=ExecutionConfig(
            dry_run=False,
            mock_broker=False,
            use_limit_orders=False,
            market_order_fallback=True,
        ),
    ),
    Profile.CANARY: ProfileSchema(
        profile_name="canary",
        environment="production",
        description="Ultra-conservative production testing profile",
        trading=TradingConfig(symbols=["BTC-USD"], mode="reduce_only"),
        risk=RiskConfig(
            max_position_size=Decimal("500"),
            max_leverage=1,
            enable_shorts=True,  # But reduce_only mode
            daily_loss_limit_pct=0.01,
        ),
        execution=ExecutionConfig(
            time_in_force="IOC",
            dry_run=False,
            mock_broker=False,
            use_limit_orders=True,
            market_order_fallback=False,
        ),
        session=SessionConfig(
            start_time=time(14, 0),  # 2 PM UTC
            end_time=time(15, 0),  # 3 PM UTC
        ),
        monitoring=MonitoringConfig(log_level="DEBUG", update_interval=5),
    ),
    Profile.OBSERVE: ProfileSchema(
        profile_name="observe",
        environment="development",
        description="Read-only observation profile with live market data and no execution",
        trading=TradingConfig(
            symbols=["BTC-USD", "ETH-USD"],
            mode="normal",
            interval=15,
        ),
        risk=RiskConfig(
            max_position_size=Decimal("0"),
            position_fraction=Decimal("0"),
            max_leverage=1,
            enable_shorts=False,
            daily_loss_limit_pct=0.0,
        ),
        execution=ExecutionConfig(
            dry_run=True,
            mock_broker=False,
            mock_fills=False,
            use_limit_orders=False,
            market_order_fallback=True,
        ),
        monitoring=MonitoringConfig(log_level="INFO", update_interval=10),
    ),
    Profile.PROD: ProfileSchema(
        profile_name="prod",
        environment="production",
        description="Full production profile",
        trading=TradingConfig(symbols=["BTC-USD", "ETH-USD"], mode="normal"),
        risk=RiskConfig(
            max_position_size=Decimal("50000"),
            max_leverage=3,
            enable_shorts=True,
        ),
        execution=ExecutionConfig(
            dry_run=False,
            mock_broker=False,
            use_limit_orders=False,
            market_order_fallback=True,
        ),
    ),
    Profile.PAPER: ProfileSchema(
        profile_name="paper",
        environment="paper",
        description="Paper trading profile with simulated execution",
        trading=TradingConfig(symbols=["BTC-USD", "ETH-USD"], mode="normal"),
        risk=RiskConfig(
            max_position_size=Decimal("10000"),
            max_leverage=3,
            enable_shorts=True,
            daily_loss_limit_pct=0.05,
        ),
        execution=ExecutionConfig(
            dry_run=True,
            mock_broker=True,
            mock_fills=True,
            use_limit_orders=False,
            market_order_fallback=True,
        ),
        monitoring=MonitoringConfig(log_level="INFO"),
    ),
}


_PROFILE_REGISTRY: dict[Profile, ProfileRegistryEntry] = {}
for profile_value, schema in _PROFILE_DEFAULTS.items():
    entry = ProfileRegistryEntry(
        profile=profile_value,
        schema=schema,
        env_defaults=(
            _DEV_ENV_DEFAULTS if profile_value in _DEV_LIKE_PROFILES else _PROD_ENV_DEFAULTS
        ),
        preflight_supported=profile_value in {Profile.DEV, Profile.CANARY, Profile.PROD},
        preflight_default=profile_value is Profile.CANARY,
    )
    _PROFILE_REGISTRY[profile_value] = entry

PROFILE_REGISTRY = MappingProxyType(_PROFILE_REGISTRY)
PROFILE_REGISTRY_BY_NAME = MappingProxyType(
    {entry.profile.value: entry for entry in PROFILE_REGISTRY.values()}
)

RUNTIME_PROFILE_CHOICES = (
    Profile.DEV.value,
    Profile.DEMO.value,
    Profile.PROD.value,
    Profile.CANARY.value,
    Profile.SPOT.value,
    Profile.OBSERVE.value,
    Profile.PAPER.value,
)
DEFAULT_RUNTIME_PROFILE_NAME = Profile.DEV.value

_PREFLIGHT_PROFILE_SEQUENCE = (Profile.DEV, Profile.CANARY, Profile.PROD)
PREFLIGHT_PROFILE_CHOICES = tuple(entry.value for entry in _PREFLIGHT_PROFILE_SEQUENCE)
DEFAULT_PREFLIGHT_PROFILE_NAME = Profile.CANARY.value


def get_profile_registry_entry(profile: Profile) -> ProfileRegistryEntry | None:
    """Return the registry entry for a Profile enum member."""
    return PROFILE_REGISTRY.get(profile)


def get_profile_registry_entry_by_name(name: str | None) -> ProfileRegistryEntry | None:
    """Return the registry entry keyed by the lowercase profile name."""
    if not name:
        return None
    return PROFILE_REGISTRY_BY_NAME.get(name.lower())


def get_env_defaults_for_profile(name: str | None) -> Mapping[str, tuple[str, bool]]:
    """Return the expected env defaults for the given profile name."""
    entry = get_profile_registry_entry_by_name(name)
    if entry is not None:
        return entry.env_defaults
    return _PROD_ENV_DEFAULTS


def is_dev_profile(name: str | None) -> bool:
    """Return True if the profile name belongs to a dev-like preset."""
    entry = get_profile_registry_entry_by_name(name)
    if entry is None:
        return False
    return entry.profile in _DEV_LIKE_PROFILES


class ProfileLoader:
    """Loads profile configuration from YAML files with hardcoded fallbacks.

    Usage:
        loader = ProfileLoader()
        schema = loader.load(Profile.DEV)
        bot_config = loader.to_bot_config(schema, Profile.DEV)
    """

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self.profiles_dir = profiles_dir or (path_registry.PROJECT_ROOT / "config" / "profiles")

    def load(self, profile: Profile) -> ProfileSchema:
        """Load profile schema from YAML file or fall back to defaults.

        Args:
            profile: The profile enum to load

        Returns:
            ProfileSchema with merged YAML + defaults
        """
        yaml_path = self.profiles_dir / f"{profile.value}.yaml"

        # Start with hardcoded defaults from the shared registry
        entry = get_profile_registry_entry(profile)
        defaults = entry.schema if entry is not None else ProfileSchema(profile_name=profile.value)

        # Try to load and merge YAML
        if yaml_path.exists():
            try:
                import yaml

                with yaml_path.open("r") as f:
                    yaml_data = yaml.safe_load(f) or {}

                schema = ProfileSchema.from_yaml(yaml_data, profile.value)

                logger.info(
                    "Loaded profile from YAML",
                    operation="profile_load",
                    profile=profile.value,
                    path=str(yaml_path),
                )

                return schema
            except ProfileValidationError as exc:
                payload = profile_yaml_parse_error_payload(
                    profile=profile.value,
                    path=yaml_path,
                    exception=exc,
                )
                logger.warning(
                    "Failed to validate profile YAML",
                    operation="profile_load",
                    profile=profile.value,
                    path=str(yaml_path),
                    error=payload["reason"],
                    details=payload,
                )
                raise
            except Exception as exc:
                payload = profile_yaml_parse_error_payload(
                    profile=profile.value,
                    path=yaml_path,
                    exception=exc,
                )
                logger.warning(
                    "Failed to load profile YAML, using defaults",
                    operation="profile_load",
                    profile=profile.value,
                    path=str(yaml_path),
                    error=payload["reason"],
                    details=payload,
                )
                return defaults

        payload = profile_yaml_missing_payload(profile=profile.value, path=yaml_path)
        logger.warning(
            "Profile YAML not found, using defaults",
            operation="profile_load",
            profile=profile.value,
            path=str(yaml_path),
            details=payload,
        )
        return defaults

    def to_bot_config_kwargs(self, schema: ProfileSchema, profile: Profile) -> dict[str, Any]:
        """Convert ProfileSchema to BotConfig constructor kwargs.

        Args:
            schema: The loaded profile schema
            profile: The profile enum

        Returns:
            Dictionary of kwargs for BotConfig constructor
        """
        from gpt_trader.app.config.bot_config import BotRiskConfig
        from gpt_trader.features.live_trade.strategies.baseline import (
            PerpsStrategyConfig,
        )

        # Build BotRiskConfig from schema.risk
        risk_config = BotRiskConfig(
            max_leverage=schema.risk.max_leverage,
            max_position_size=schema.risk.max_position_size,
            position_fraction=schema.risk.position_fraction,
            stop_loss_pct=schema.risk.stop_loss_pct,
            take_profit_pct=schema.risk.take_profit_pct,
            daily_loss_limit_pct=schema.risk.daily_loss_limit_pct,
        )

        # Build PerpsStrategyConfig from schema.strategy
        strategy_config = PerpsStrategyConfig(
            short_ma_period=schema.strategy.short_ma_period,
            long_ma_period=schema.strategy.long_ma_period,
            rsi_period=schema.strategy.rsi_period,
            rsi_overbought=schema.strategy.rsi_overbought,
            rsi_oversold=schema.strategy.rsi_oversold,
        )

        kwargs: dict[str, Any] = {
            "profile": profile,
            "environment": schema.environment,
            "symbols": schema.trading.symbols,
            "interval": schema.trading.interval,
            # Nested risk config (BotRiskConfig instance)
            "risk": risk_config,
            # Nested strategy config (PerpsStrategyConfig instance)
            "strategy": strategy_config,
            # Top-level config fields
            "enable_shorts": schema.risk.enable_shorts,
            # Execution settings
            "time_in_force": schema.execution.time_in_force,
            "dry_run": schema.execution.dry_run,
            "mock_broker": schema.execution.mock_broker,
            "use_limit_orders": schema.execution.use_limit_orders,
            "market_order_fallback": schema.execution.market_order_fallback,
            "strategy_signal_proposals_enabled": schema.execution.strategy_signal_proposals,
            "risk_budget_runtime_seed_enabled": schema.execution.risk_budget_runtime_seed,
            # Monitoring
            "log_level": schema.monitoring.log_level,
            "status_interval": schema.monitoring.update_interval,
            "status_enabled": schema.monitoring.status_enabled,
            # Strategy type
            "strategy_type": schema.strategy.type,
        }

        # Mode mapping
        if schema.trading.mode == "reduce_only":
            kwargs["reduce_only_mode"] = True

        # Note: Session time fields (trading_window_start, trading_window_end, trading_days)
        # are not currently in BotConfig. If needed, add them to BotConfig first.

        return kwargs


def get_profile_loader() -> ProfileLoader:
    """Get the ProfileLoader instance from the application container.

    Requires that an ApplicationContainer has been set via
    set_application_container(). This ensures proper dependency
    injection and avoids hidden global state.

    Returns:
        The ProfileLoader instance from the container.

    Raises:
        RuntimeError: If no application container has been set.
    """
    from gpt_trader.app.container import get_application_container

    container = get_application_container()
    if container is None:
        raise RuntimeError(
            "No application container set. Call set_application_container() "
            "before using get_profile_loader(). For tests, ensure the container "
            "is set up in a fixture."
        )
    return container.profile_loader


def load_profile(profile: Profile) -> ProfileSchema:
    """Convenience function to load a profile schema."""
    return get_profile_loader().load(profile)


ConfigFactory = Callable[..., "BotConfig"]


def build_profile_config(profile: Profile, create_config: ConfigFactory) -> BotConfig:
    """Construct a configuration tailored to the requested profile.

    Uses YAML-first loading: attempts to load from config/profiles/{profile}.yaml
    and falls back to hardcoded defaults if the file doesn't exist.

    Args:
        profile: The profile enum to load
        create_config: Factory function to create BotConfig

    Returns:
        BotConfig configured for the specified profile
    """
    # Load profile schema (YAML-first with hardcoded fallback)
    schema = load_profile(profile)

    # Convert schema to BotConfig
    return _schema_to_bot_config(schema, profile, create_config)


def _schema_to_bot_config(
    schema: ProfileSchema,
    profile: Profile,
    create_config: ConfigFactory,
) -> BotConfig:
    """Convert ProfileSchema to BotConfig using the factory.

    Args:
        schema: The loaded profile schema
        profile: The profile enum
        create_config: Factory function to create BotConfig

    Returns:
        BotConfig instance
    """
    from gpt_trader.app.config.bot_config import BotRiskConfig

    # Build risk config
    risk = BotRiskConfig(
        max_leverage=schema.risk.max_leverage,
        max_position_size=schema.risk.max_position_size,
        position_fraction=schema.risk.position_fraction,
        stop_loss_pct=schema.risk.stop_loss_pct,
        take_profit_pct=schema.risk.take_profit_pct,
    )

    # Base kwargs
    kwargs: dict = {
        "profile": profile,
        "symbols": schema.trading.symbols,
        "interval": schema.trading.interval,
        "risk": risk,
        "enable_shorts": schema.risk.enable_shorts,
        "time_in_force": schema.execution.time_in_force,
        "dry_run": schema.execution.dry_run,
        "mock_broker": schema.execution.mock_broker,
        "strategy_signal_proposals_enabled": schema.execution.strategy_signal_proposals,
        "risk_budget_runtime_seed_enabled": schema.execution.risk_budget_runtime_seed,
        "log_level": schema.monitoring.log_level,
        "status_interval": schema.monitoring.update_interval,
        "status_enabled": schema.monitoring.status_enabled,
        "strategy_type": schema.strategy.type,
    }

    # Mode mapping
    if schema.trading.mode == "reduce_only":
        kwargs["reduce_only_mode"] = True

    # Optional session settings
    if schema.session.start_time is not None:
        kwargs["trading_window_start"] = schema.session.start_time
    if schema.session.end_time is not None:
        kwargs["trading_window_end"] = schema.session.end_time
    if schema.session.trading_days:
        kwargs["trading_days"] = schema.session.trading_days

    return create_config(**kwargs)


__all__ = [
    "ExecutionConfig",
    "MonitoringConfig",
    "ProfileLoader",
    "ProfileOverrideDecision",
    "ProfileOverrideSource",
    "ProfileValidationError",
    "ProfileSchema",
    "ProfileRegistryEntry",
    "PROFILE_REGISTRY",
    "get_env_defaults_for_profile",
    "get_env_profile_override",
    "get_profile_registry_entry",
    "get_profile_registry_entry_by_name",
    "is_dev_profile",
    "resolve_profile_override",
    "DEFAULT_RUNTIME_PROFILE_NAME",
    "RUNTIME_PROFILE_CHOICES",
    "DEFAULT_PREFLIGHT_PROFILE_NAME",
    "PREFLIGHT_PROFILE_CHOICES",
    "RiskConfig",
    "SessionConfig",
    "StrategyConfig",
    "TradingConfig",
    "build_profile_config",
    "get_profile_loader",
    "load_profile",
]
