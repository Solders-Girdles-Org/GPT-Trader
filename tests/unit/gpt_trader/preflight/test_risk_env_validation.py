"""Exercise real environment parsing through the offline risk preflight section."""

import os
from decimal import Decimal

import pytest

from gpt_trader.app.config.bot_config import BotConfig
from gpt_trader.features.live_trade.risk.config import RiskConfig
from gpt_trader.preflight.checks.risk import check_risk_configuration
from gpt_trader.preflight.core import PreflightCheck


@pytest.fixture(autouse=True)
def clear_risk_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in os.environ:
        if variable.startswith(("RISK_", "CFM_")):
            monkeypatch.delenv(variable)


@pytest.mark.parametrize("token", ["1", "true", "yes", "on", "t", "y", " TRUE "])
def test_reduce_only_tokens_match_bot_and_warn(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv("RISK_REDUCE_ONLY_MODE", token)
    assert BotConfig.from_env().reduce_only_mode is True
    assert RiskConfig.from_env().reduce_only_mode is True
    checker = PreflightCheck(profile="dev")
    assert check_risk_configuration(checker) is True
    assert any("Reduce-only mode ENABLED" in warning for warning in checker.warnings)


@pytest.mark.parametrize("token", ["0", "false", "no", "off", "f", "n"])
def test_false_reduce_only_tokens_match_bot(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv("RISK_REDUCE_ONLY_MODE", token)
    assert BotConfig.from_env().reduce_only_mode is False
    assert RiskConfig.from_env().reduce_only_mode is False


@pytest.mark.parametrize("token", ["true", "yes", "on", "1"])
def test_mark_staleness_reduce_only_accepts_true_tokens(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("RISK_MARK_STALENESS_ALLOW_REDUCE_ONLY", token)
    assert RiskConfig.from_env().mark_staleness_allow_reduce_only is True


@pytest.mark.parametrize(
    "variable",
    [
        "RISK_DAILY_LOSS_LIMIT_PCT",
        "RISK_MAX_EXPOSURE_PCT",
        "RISK_MAX_POSITION_PCT_PER_SYMBOL",
        "RISK_MIN_LIQUIDATION_BUFFER_PCT",
        "RISK_API_ERROR_RATE_THRESHOLD",
        "RISK_API_RATE_LIMIT_USAGE_THRESHOLD",
        "RISK_MARK_STALENESS_THRESHOLD_SECONDS",
        "CFM_MIN_LIQUIDATION_BUFFER_PCT",
        "CFM_MAX_EXPOSURE_PCT",
        "CFM_MAX_POSITION_SIZE_PCT",
    ],
)
@pytest.mark.parametrize(
    "token", ["nan", "NaN", "sNaN", "inf", "-inf", "-0.01", "1e999", "1e-999", "bad", ""]
)
def test_invalid_limit_is_rejected_and_reported_unsafe(
    monkeypatch: pytest.MonkeyPatch, variable: str, token: str
) -> None:
    monkeypatch.setenv(variable, token)
    with pytest.raises(ValueError, match=variable):
        RiskConfig.from_env()
    checker = PreflightCheck(profile="dev")
    assert check_risk_configuration(checker) is False
    assert any(variable in error and "UNSAFE VALUE" in error for error in checker.errors)
    assert not any("No daily loss limit configured" in warning for warning in checker.warnings)


@pytest.mark.parametrize("token", ["nan", "inf", "-inf", "-0.01", "1e400", "1e-999", "bad", ""])
def test_shared_position_limit_rejected_by_bot_and_preflight(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("RISK_MAX_POSITION_PCT_PER_SYMBOL", token)
    for loader in (BotConfig.from_env, RiskConfig.from_env):
        with pytest.raises(ValueError, match="RISK_MAX_POSITION_PCT_PER_SYMBOL"):
            loader()


@pytest.mark.parametrize("token", ["0.15", " 0.15 ", "1.5e-1"])
def test_shared_position_limit_agrees(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv("RISK_MAX_POSITION_PCT_PER_SYMBOL", token)
    assert BotConfig.from_env().risk.position_fraction == Decimal("0.15")
    assert RiskConfig.from_env().max_position_pct_per_symbol == 0.15


def test_zero_daily_limit_retains_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RISK_DAILY_LOSS_LIMIT_PCT", "0")
    checker = PreflightCheck(profile="dev")
    assert check_risk_configuration(checker) is True
    assert any("No daily loss limit configured" in warning for warning in checker.warnings)


@pytest.mark.parametrize("token", ["true", "1", "enabled"])
def test_engaged_kill_switch_preserved(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv("RISK_KILL_SWITCH_ENABLED", token)
    monkeypatch.setenv("RISK_REDUCE_ONLY_MODE", "true")
    assert BotConfig.from_env().kill_switch_enabled is True
    assert RiskConfig.from_env().kill_switch_enabled is True
    checker = PreflightCheck(profile="dev")
    assert check_risk_configuration(checker) is True
    assert any("Kill switch is ENABLED" in warning for warning in checker.warnings)
    assert any("Reduce-only mode ENABLED" in warning for warning in checker.warnings)


@pytest.mark.parametrize(
    "variable",
    [
        "RISK_MAX_LEVERAGE",
        "RISK_SLIPPAGE_GUARD_BPS",
        "RISK_UNFILLED_ORDER_ALERT_SECONDS",
        "CFM_MAX_LEVERAGE",
    ],
)
@pytest.mark.parametrize("token", ["nan", "inf", "-1", "1.5", ""])
def test_invalid_integer_limit_is_unsafe(
    monkeypatch: pytest.MonkeyPatch, variable: str, token: str
) -> None:
    monkeypatch.setenv(variable, token)
    checker = PreflightCheck(profile="dev")
    assert check_risk_configuration(checker) is False
    assert any(variable in error and "UNSAFE VALUE" in error for error in checker.errors)
