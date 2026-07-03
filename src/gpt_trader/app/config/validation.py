"""Validation helpers for bot configuration.

This module provides configuration validation utilities including:
- ConfigValidationError: Exception for validation failures
- ConfigValidationResult: Pydantic model for validation results
- format_validation_errors: Helper to format Pydantic errors
- validate_config: Validate BotConfig instances
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from gpt_trader.features.live_trade.symbols import US_FUTURES_ALLOWLIST

if TYPE_CHECKING:
    from gpt_trader.app.config.bot_config import BotConfig

# CFM US futures is the only supported derivatives venue. INTX perpetuals were
# removed (see docs/decisions/intx-default-derivatives-venue.md).
ALLOWED_COINBASE_DERIVATIVES_TYPES = frozenset({"us_futures"})
# Retired venue types kept here only to emit a clear migration error.
REMOVED_COINBASE_DERIVATIVES_TYPES = frozenset({"intx_perps", "perpetuals"})


class ConfigValidationError(Exception):
    """Raised when configuration values fail validation."""

    def __init__(self, errors: list[str] | None = None) -> None:
        self.errors = errors or []
        message = "; ".join(self.errors) if self.errors else "Invalid configuration"
        super().__init__(message)


class ConfigValidationResult(BaseModel):
    """Result of configuration validation."""

    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """Check if validation failed."""
        return bool(self.errors)

    @property
    def has_warnings(self) -> bool:
        """Check if there are validation warnings."""
        return bool(self.warnings)


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Coerce Pydantic validation errors into user-facing messages."""

    errors: list[str] = []
    for error in exc.errors():
        loc = error.get("loc", ())
        field_path = ".".join(str(item) for item in loc)
        message = error.get("msg", "")
        if field_path:
            errors.append(f"{field_path}: {message}")
        else:
            errors.append(message or str(exc))
    if not errors:
        errors.append(str(exc))
    return errors


def validate_config(config: BotConfig) -> list[str]:
    """
    Validate the configuration and return a list of error messages.
    Returns an empty list if the configuration is valid.
    """
    errors = []

    # Validate Risk Config
    risk = config.risk
    if risk.position_fraction <= 0 or risk.position_fraction > 1:
        errors.append(
            f"risk.position_fraction must be between 0 and 1, got {risk.position_fraction}"
        )

    if risk.stop_loss_pct <= 0 or risk.stop_loss_pct >= 1:
        errors.append(f"risk.stop_loss_pct must be between 0 and 1, got {risk.stop_loss_pct}")

    if risk.take_profit_pct <= 0:
        errors.append(f"risk.take_profit_pct must be positive, got {risk.take_profit_pct}")

    # Validate Strategy Config
    strategy = config.strategy
    # Check if it's the expected strategy type before accessing fields
    if hasattr(strategy, "short_ma_period") and hasattr(strategy, "long_ma_period"):
        if strategy.short_ma_period >= strategy.long_ma_period:
            errors.append(
                f"strategy.short_ma_period ({strategy.short_ma_period}) must be less than "
                f"strategy.long_ma_period ({strategy.long_ma_period})"
            )

    # Validate Symbols
    if not config.symbols:
        errors.append("symbols list cannot be empty")

    raw_derivatives_type = config.coinbase_derivatives_type
    if not isinstance(raw_derivatives_type, str):
        errors.append(
            "coinbase_derivatives_type must be one of: "
            f"{', '.join(sorted(ALLOWED_COINBASE_DERIVATIVES_TYPES))}"
        )
        derivatives_type = ""
    else:
        derivatives_type = raw_derivatives_type.strip().lower()
        if derivatives_type in REMOVED_COINBASE_DERIVATIVES_TYPES:
            errors.append(
                f"coinbase_derivatives_type {raw_derivatives_type!r} is no longer "
                "supported; INTX perpetuals were removed. Use 'us_futures'."
            )
        elif derivatives_type and derivatives_type not in ALLOWED_COINBASE_DERIVATIVES_TYPES:
            errors.append(
                f"coinbase_derivatives_type must be one of: "
                f"{', '.join(sorted(ALLOWED_COINBASE_DERIVATIVES_TYPES))}; "
                f"got {raw_derivatives_type!r}"
            )
    if config.derivatives_enabled and not derivatives_type:
        errors.append("coinbase_derivatives_type must be set when derivatives_enabled is true")

    raw_trading_modes = config.trading_modes
    if not isinstance(raw_trading_modes, list):
        errors.append("trading_modes must be a list of mode names")
        trading_modes = set()
    elif not all(isinstance(mode, str) for mode in raw_trading_modes):
        errors.append("trading_modes must contain only mode names")
        trading_modes = {mode for mode in raw_trading_modes if isinstance(mode, str)}
    else:
        trading_modes = set(raw_trading_modes)

    cfm_mode_enabled = "cfm" in trading_modes
    if config.cfm_enabled and not cfm_mode_enabled:
        errors.append("cfm_enabled requires trading_modes to include 'cfm'")
    if cfm_mode_enabled and not config.cfm_enabled:
        errors.append("trading_modes includes 'cfm' but cfm_enabled is false")

    if derivatives_type == "us_futures" and (
        config.derivatives_enabled or config.cfm_enabled or cfm_mode_enabled
    ):
        normalized_symbols = [
            symbol.strip().upper() for symbol in config.symbols if isinstance(symbol, str)
        ]
        intx_symbols = sorted(symbol for symbol in normalized_symbols if "-PERP" in symbol)
        if intx_symbols:
            errors.append(
                "CFM US futures configuration does not support INTX perpetual symbols: "
                f"{', '.join(intx_symbols)}. Use US futures symbols or spot symbols."
            )

        unsupported_us_futures = sorted(
            symbol
            for symbol in normalized_symbols
            if symbol.endswith("-FUTURES") and symbol not in US_FUTURES_ALLOWLIST
        )
        if unsupported_us_futures:
            errors.append(
                "CFM US futures configuration does not support these US futures symbols: "
                f"{', '.join(unsupported_us_futures)}. Allowed US futures symbols: "
                f"{', '.join(sorted(US_FUTURES_ALLOWLIST))}."
            )

    return errors


__all__ = [
    "ConfigValidationError",
    "ConfigValidationResult",
    "format_validation_errors",
    "validate_config",
]
