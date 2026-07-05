"""Helper utilities for CLI command implementations."""

import warnings
from argparse import Namespace
from pathlib import Path
from typing import Any

import yaml

from gpt_trader.app.config import BotConfig
from gpt_trader.app.config.profile_loader import (
    DEFAULT_RUNTIME_PROFILE_NAME,
    ProfileLoader,
    get_env_profile_override,
    resolve_profile_override,
)
from gpt_trader.app.config.validation import ConfigValidationError, validate_config
from gpt_trader.app.container import (
    ApplicationContainer,
    create_application_container,
    get_application_container,
    set_application_container,
)
from gpt_trader.config.types import Profile
from gpt_trader.features.live_trade.bot import TradingBot
from gpt_trader.features.live_trade.symbols import coerce_removed_perpetual_symbols
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="cli_services")


def load_config_from_yaml(path: str | Path) -> BotConfig:
    """Load BotConfig from a nested YAML file (e.g., optimize apply output).

    Supports structure:
        strategy:
            short_ma_period: 8
            long_ma_period: 35
            ...
        risk:
            target_leverage: 5
            position_fraction: 0.2
            ...
        symbols: [BTC-USD]
        interval: 60
        ...
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Legacy profile-style YAML mapping via BotConfig.from_dict\\(\\) is deprecated.*",
        )
        return BotConfig.from_dict(data)


def load_profile_config(profile: Profile | str) -> BotConfig:
    """Build a BotConfig for a first-class runtime profile."""
    profile_enum = _coerce_profile(profile)
    loader = ProfileLoader()
    schema = loader.load(profile_enum)
    logger.info("Loaded runtime profile %s from profile loader", profile_enum.value)
    return loader.build_bot_config(schema, profile_enum)


def build_config_from_args(args: Namespace, **kwargs: Any) -> BotConfig:
    """Build configuration from env/profile/config/CLI using a shared precedence engine."""

    cli_profile = getattr(args, "profile", None)
    config_path = getattr(args, "config", None)
    config: BotConfig
    file_profile: str | None = None

    if config_path:
        config = load_config_from_yaml(config_path)
        file_profile = _extract_profile_from_config(config)
        logger.info("Loaded config from %s", config_path)
    else:
        config = BotConfig.from_env()

    profile_decision = resolve_profile_override(
        cli_profile=cli_profile,
        file_profile=file_profile,
        env_profile=get_env_profile_override(),
        default_profile=DEFAULT_RUNTIME_PROFILE_NAME,
    )
    logger.info(
        "Resolved runtime profile %s from %s",
        profile_decision.profile,
        profile_decision.source.value,
    )

    if config_path:
        config.profile = profile_decision.profile
    else:
        config = _hydrate_profile_from_loader(config, profile_decision.profile)

    # 2. Override with CLI Args (always takes highest precedence)
    if getattr(args, "dry_run", False):
        config.dry_run = True

    if getattr(args, "symbols", None):
        config.symbols = args.symbols

    if getattr(args, "interval", None):
        config.interval = args.interval

    if getattr(args, "target_leverage", None):
        # Update nested risk config
        config.risk.target_leverage = args.target_leverage

    if getattr(args, "reduce_only_mode", False):
        config.reduce_only_mode = True

    if getattr(args, "time_in_force", None):
        config.time_in_force = args.time_in_force

    if getattr(args, "enable_order_preview", False):
        config.enable_order_preview = True

    return config


def _extract_profile_from_config(config: BotConfig) -> str | None:
    profile_value = getattr(config, "profile", None)
    if profile_value is None:
        return None
    if isinstance(profile_value, Profile):
        return profile_value.value
    return str(profile_value)


def _coerce_profile(profile: Profile | str) -> Profile:
    if isinstance(profile, Profile):
        return profile
    try:
        return Profile(str(profile).lower())
    except ValueError as exc:
        raise ValueError(f"Unknown profile: {profile}") from exc


def _hydrate_profile_from_loader(config: BotConfig, profile_name: str) -> BotConfig:
    try:
        profile_enum = _coerce_profile(profile_name)
    except ValueError:
        logger.warning(
            "Unknown profile '%s'. Use a Profile enum value or pass --config for YAML.",
            profile_name,
        )
        profile_enum = Profile(DEFAULT_RUNTIME_PROFILE_NAME)
    except Exception as exc:
        logger.warning("Failed to load profile %s: %s", profile_name, exc)
        profile_enum = Profile(DEFAULT_RUNTIME_PROFILE_NAME)

    loader = ProfileLoader()
    schema = loader.load(profile_enum)
    profile_kwargs = loader.to_bot_config_kwargs(schema, profile_enum)
    _apply_profile_kwargs(config, profile_kwargs)

    logger.info(
        "Loaded profile %s with risk/strategy/monitoring settings",
        profile_enum.value,
    )
    return config


def _apply_profile_kwargs(config: BotConfig, profile_kwargs: dict[str, Any]) -> None:
    if "risk" in profile_kwargs:
        config.risk = profile_kwargs["risk"]

    if "strategy" in profile_kwargs:
        config.strategy = profile_kwargs["strategy"]

    if "symbols" in profile_kwargs:
        config.symbols = profile_kwargs["symbols"]
    if "interval" in profile_kwargs:
        config.interval = profile_kwargs["interval"]

    if "dry_run" in profile_kwargs:
        config.dry_run = profile_kwargs["dry_run"]
    if "mock_broker" in profile_kwargs:
        config.mock_broker = profile_kwargs["mock_broker"]
    if "time_in_force" in profile_kwargs:
        config.time_in_force = profile_kwargs["time_in_force"]
    if "strategy_signal_proposals_enabled" in profile_kwargs:
        config.strategy_signal_proposals_enabled = profile_kwargs[
            "strategy_signal_proposals_enabled"
        ]
    if "risk_budget_runtime_seed_enabled" in profile_kwargs:
        config.risk_budget_runtime_seed_enabled = profile_kwargs["risk_budget_runtime_seed_enabled"]

    if "enable_shorts" in profile_kwargs:
        config.set_enable_shorts(profile_kwargs["enable_shorts"])
    if "reduce_only_mode" in profile_kwargs:
        config.reduce_only_mode = profile_kwargs["reduce_only_mode"]
    if "strategy_type" in profile_kwargs:
        config.strategy_type = profile_kwargs["strategy_type"]
    if "log_level" in profile_kwargs:
        config.log_level = profile_kwargs["log_level"]
    if "status_interval" in profile_kwargs:
        config.status_interval = profile_kwargs["status_interval"]
    if "status_enabled" in profile_kwargs:
        config.status_enabled = profile_kwargs["status_enabled"]
    if "profile" in profile_kwargs:
        config.profile = profile_kwargs["profile"]
    if "environment" in profile_kwargs:
        config.environment = profile_kwargs["environment"]


def instantiate_container(config: BotConfig) -> ApplicationContainer:
    """Validate config and return the registered ApplicationContainer.

    Registers the container globally so services can resolve dependencies
    via get_application_container(). Avoids overriding an existing container
    (e.g., one set by tests).
    """
    _coerce_removed_perpetual_startup_symbols(config)
    validation_errors = _validate_startup_config(config)
    if validation_errors:
        raise ConfigValidationError(validation_errors)

    # Check if container already set (e.g., by tests)
    existing = get_application_container()
    if existing is not None:
        logger.debug("Using existing application container")
        return existing

    # Create and register new container
    container = create_application_container(config)
    try:
        container.persist_startup_config_fingerprint()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to persist startup config fingerprint: %s", exc)
    set_application_container(container)
    logger.debug("Created and registered application container")
    return container


def instantiate_bot(config: BotConfig) -> TradingBot:
    """Instantiate a TradingBot using the ApplicationContainer."""
    return instantiate_container(config).create_bot()


def _validate_startup_config(config: BotConfig) -> list[str]:
    errors = validate_config(config)
    if not _is_observe_read_only_config(config):
        return errors

    return [error for error in errors if not _is_observe_position_fraction_error(error)]


def _coerce_removed_perpetual_startup_symbols(config: BotConfig) -> None:
    quote = str(getattr(config, "coinbase_default_quote", "USD") or "USD")
    normalized_symbols, logs = coerce_removed_perpetual_symbols(config.symbols, quote=quote)
    if not logs:
        return

    config.symbols = normalized_symbols
    for record in logs:
        logger.log(record.level, record.message, *record.args)


def _is_observe_read_only_config(config: BotConfig) -> bool:
    profile = getattr(config, "profile", None)
    profile_value = getattr(profile, "value", profile)
    return str(profile_value).lower() == Profile.OBSERVE.value and bool(
        getattr(config, "dry_run", False)
    )


def _is_observe_position_fraction_error(error: str) -> bool:
    return error.startswith("risk.position_fraction must be between 0 and 1, got 0")
