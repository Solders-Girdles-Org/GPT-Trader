from __future__ import annotations

import os
from decimal import Decimal

import pytest

from gpt_trader.config.config_utilities import (
    ValidationError,
    parse_bool_env,
    parse_decimal_env,
    parse_float_env,
    parse_int_env,
    parse_list_env,
    parse_mapping_env,
    validate_required_env,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Clear all environment variables for isolated tests."""
    for key in list(os.environ.keys()):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_parse_list_env_success(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("TEST_LIST", "alpha,beta,gamma")
    result = parse_list_env("TEST_LIST", str)
    assert result == ["alpha", "beta", "gamma"]


def test_parse_mapping_env_wraps_errors(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("BAD_MAP", "value-without-key")
    with pytest.raises(ValidationError) as exc:
        parse_mapping_env("BAD_MAP", int)
    assert "Failed to parse BAD_MAP" in str(exc.value)


def test_numeric_env_helpers_handle_defaults(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("BOOL_FLAG", "true")
    clean_env.setenv("DECIMAL_VALUE", "1.5")
    clean_env.setenv("INT_VALUE", "7")
    clean_env.setenv("FLOAT_VALUE", "2.75")

    assert parse_bool_env("BOOL_FLAG", default=False) is True
    assert parse_decimal_env("DECIMAL_VALUE") == Decimal("1.5")
    assert parse_int_env("INT_VALUE") == 7
    assert parse_float_env("FLOAT_VALUE") == 2.75


def test_validate_required_env_raises_when_missing(clean_env: pytest.MonkeyPatch):
    with pytest.raises(ValidationError):
        validate_required_env(["MISSING_VAR"])


def test_validate_required_env_passes_when_present(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("PRESENT_VAR", "value")
    result = validate_required_env(["PRESENT_VAR"])
    assert result is None


def test_parse_list_env_with_invalid_cast(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("TEST_LIST", "a,b,c")
    with pytest.raises(ValidationError) as exc:
        parse_list_env("TEST_LIST", int)
    assert "Failed to parse TEST_LIST" in str(exc.value)


def test_parse_bool_env_with_invalid_value(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("BAD_BOOL", "not_a_bool")
    with pytest.raises(ValidationError) as exc:
        parse_bool_env("BAD_BOOL")
    assert "Failed to parse BAD_BOOL" in str(exc.value)


def test_parse_decimal_env_with_invalid_value(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("BAD_DECIMAL", "not_a_decimal")
    with pytest.raises(ValidationError) as exc:
        parse_decimal_env("BAD_DECIMAL")
    assert "Failed to parse BAD_DECIMAL" in str(exc.value)


def test_parse_int_env_with_invalid_value(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("BAD_INT", "not_an_int")
    with pytest.raises(ValidationError) as exc:
        parse_int_env("BAD_INT")
    assert "Failed to parse BAD_INT" in str(exc.value)


def test_parse_float_env_with_invalid_value(clean_env: pytest.MonkeyPatch):
    clean_env.setenv("BAD_FLOAT", "not_a_float")
    with pytest.raises(ValidationError) as exc:
        parse_float_env("BAD_FLOAT")
    assert "Failed to parse BAD_FLOAT" in str(exc.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
        (False, False),
        (0, False),
        ("1", True),
        ("true", True),
        ("YES", True),
        (" on ", True),
        (True, True),
        (1, True),
        # Fail closed: an unrecognised value engages the kill switch.
        ("enabled", True),
        ("2", True),
    ],
)
def test_parse_kill_switch_flag_fails_closed(value: object, expected: bool) -> None:
    from gpt_trader.config.config_utilities import parse_kill_switch_flag

    assert parse_kill_switch_flag(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("0", False), ("1", True), ("true", True), ("enabled", True)],
)
def test_kill_switch_env_engaged(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    from gpt_trader.config.config_utilities import KILL_SWITCH_ENV_VAR, kill_switch_env_engaged

    assert KILL_SWITCH_ENV_VAR == "RISK_KILL_SWITCH_ENABLED"
    if value is None:
        monkeypatch.delenv(KILL_SWITCH_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(KILL_SWITCH_ENV_VAR, value)

    assert kill_switch_env_engaged() is expected
