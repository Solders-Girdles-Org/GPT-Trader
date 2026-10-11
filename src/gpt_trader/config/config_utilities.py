"""Configuration parsing utilities for consistent environment variable handling."""

from __future__ import annotations

import os
from collections.abc import Callable
from decimal import Decimal
from typing import TypeVar

from gpt_trader.config.env_utils import (  # naming: allow
    EnvVarError,
    get_env_bool,
    get_env_decimal,
    get_env_float,
    get_env_int,
    parse_env_list,
    parse_env_mapping,
)
from gpt_trader.errors import ValidationError
from gpt_trader.utilities.parsing import interpret_tristate_bool

T = TypeVar("T")


def _wrap_env_error(var_name: str, error: EnvVarError) -> ValidationError:
    return ValidationError(
        f"Failed to parse {var_name}: {error.message}",
        field=error.var_name,
        value=error.value,
    )


def parse_mapping_env(
    var_name: str,
    cast: Callable[[str], T],
    default: dict[str, T] | None = None,
    separator: str = ":",
    kv_delimiter: str = "=",
) -> dict[str, T]:
    """Parse a delimited environment variable into a mapping."""
    try:
        return parse_env_mapping(
            var_name,
            cast,
            item_delimiter=separator,
            kv_delimiter=kv_delimiter,
            default=default,
        )
    except EnvVarError as exc:
        raise _wrap_env_error(var_name, exc) from exc


def parse_list_env(
    var_name: str,
    cast: Callable[[str], T],
    default: list[T] | None = None,
    separator: str = ",",
) -> list[T]:
    """Parse a delimited environment variable into a list."""
    try:
        return parse_env_list(
            var_name,
            cast,
            separator=separator,
            default=default,
        )
    except EnvVarError as exc:
        raise _wrap_env_error(var_name, exc) from exc


def parse_bool_env(var_name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    try:
        value = get_env_bool(var_name, default=default)
    except EnvVarError as exc:
        raise _wrap_env_error(var_name, exc) from exc
    return default if value is None else value


def parse_decimal_env(var_name: str, default: Decimal | None = None) -> Decimal | None:
    """Parse a decimal environment variable."""
    try:
        return get_env_decimal(var_name, default=default)
    except EnvVarError as exc:
        raise _wrap_env_error(var_name, exc) from exc


def parse_int_env(var_name: str, default: int | None = None) -> int | None:
    """Parse an integer environment variable."""
    try:
        return get_env_int(var_name, default=default)
    except EnvVarError as exc:
        raise _wrap_env_error(var_name, exc) from exc


def parse_float_env(var_name: str, default: float | None = None) -> float | None:
    """Parse a float environment variable."""
    try:
        return get_env_float(var_name, default=default)
    except EnvVarError as exc:
        raise _wrap_env_error(var_name, exc) from exc


def validate_required_env(var_names: list[str]) -> None:
    """Validate that required environment variables are present."""
    missing = [name for name in var_names if not os.environ.get(name)]
    if missing:
        raise ValidationError(f"Missing required environment variables: {', '.join(missing)}")


# Emergency stop: the documented operator control (docs/production.md,
# config/environments/.env.template). Read wherever a risk manager is built so
# the halt cannot be lost by a config path that skips BotConfig.from_env (#1300).
KILL_SWITCH_ENV_VAR = "RISK_KILL_SWITCH_ENABLED"


def parse_kill_switch_flag(value: object) -> bool:
    """Interpret a kill-switch setting, failing closed.

    Unset (None or blank) and recognised false tokens disengage the switch.
    Every other value, including unrecognised strings such as ``"enabled"``,
    engages it: an operator who meant to stop trading must not be overridden
    by a parse error.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip()
    if not text:
        return False
    return interpret_tristate_bool(text) is not False


def kill_switch_env_engaged() -> bool:
    """Return True when ``RISK_KILL_SWITCH_ENABLED`` engages the kill switch."""
    return parse_kill_switch_flag(os.environ.get(KILL_SWITCH_ENV_VAR))
