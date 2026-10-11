"""Tests for risk configuration preflight checks."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

import gpt_trader.features.live_trade.risk.config as risk_config_module
from gpt_trader.features.live_trade.risk.config import RiskConfig
from gpt_trader.preflight.checks.risk import check_risk_configuration
from gpt_trader.preflight.core import PreflightCheck


def _risk_config(**overrides: object) -> RiskConfig:
    defaults: dict[str, object] = {
        "max_leverage": 3,
        "daily_loss_limit_pct": 0.05,
        "min_liquidation_buffer_pct": 0.15,
        "max_position_pct_per_symbol": 0.10,
        "slippage_guard_bps": 50,
        "kill_switch_enabled": False,
        "reduce_only_mode": False,
    }
    defaults.update(overrides)
    return RiskConfig(**defaults)


@dataclass(slots=True)
class RiskConfigStub:
    from_env: MagicMock

    def set(self, **overrides: object) -> RiskConfig:
        config = _risk_config(**overrides)
        self.from_env.return_value = config
        return config


@pytest.fixture
def risk_config_stub(monkeypatch: pytest.MonkeyPatch) -> RiskConfigStub:
    from_env = MagicMock(name="RiskConfig.from_env", return_value=_risk_config())
    monkeypatch.setattr(risk_config_module.RiskConfig, "from_env", from_env)
    return RiskConfigStub(from_env=from_env)


class TestCheckRiskConfiguration:
    """Test risk configuration validation."""

    def test_passes_with_valid_config(self, risk_config_stub: RiskConfigStub) -> None:
        """Should pass when all risk parameters are within bounds."""
        checker = PreflightCheck(profile="dev")
        risk_config_stub.set()
        result = check_risk_configuration(checker)

        assert result is True
        assert any("Max leverage" in s for s in checker.successes)

    def test_fails_with_unsafe_leverage(self, risk_config_stub: RiskConfigStub) -> None:
        """Should fail when leverage is outside safe range."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(max_leverage=15)
        result = check_risk_configuration(checker)

        assert result is False
        assert any("UNSAFE VALUE" in e for e in checker.errors)

    def test_warns_when_no_daily_loss_limit_configured(
        self, risk_config_stub: RiskConfigStub
    ) -> None:
        """Should warn when the daily loss limit pct is not configured."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(daily_loss_limit_pct=0.0)
        result = check_risk_configuration(checker)

        assert result is True
        assert any("No daily loss limit configured" in w for w in checker.warnings)

    def test_fails_with_low_liquidation_buffer(self, risk_config_stub: RiskConfigStub) -> None:
        """Should fail when liquidation buffer is below 10%."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(min_liquidation_buffer_pct=0.05)
        result = check_risk_configuration(checker)

        assert result is False

    def test_fails_with_excessive_position_size(self, risk_config_stub: RiskConfigStub) -> None:
        """Should fail when position size exceeds 25%."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(max_position_pct_per_symbol=0.50)
        result = check_risk_configuration(checker)

        assert result is False

    def test_fails_with_slippage_guard_out_of_range(self, risk_config_stub: RiskConfigStub) -> None:
        """Should fail when slippage guard is outside 10-100 bps."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(slippage_guard_bps=5)
        result = check_risk_configuration(checker)

        assert result is False

    def test_warns_when_kill_switch_enabled(self, risk_config_stub: RiskConfigStub) -> None:
        """Should warn when kill switch is enabled."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(kill_switch_enabled=True)
        result = check_risk_configuration(checker)

        assert result is True
        assert any("Kill switch" in w for w in checker.warnings)

    @pytest.mark.parametrize("profile_name", ["prod", "PROD", " Prod "])
    def test_warns_when_profile_engages_kill_switch(
        self,
        risk_config_stub: RiskConfigStub,
        monkeypatch: pytest.MonkeyPatch,
        profile_name: str,
    ) -> None:
        """Should report a halt engaged by the profile, not only by the env (#1300).

        The profile name is matched case-insensitively, as the live command does.
        """
        from gpt_trader.app.config.profile_loader import ProfileLoader
        from gpt_trader.app.config.profile_schema import EmergencyConfig, ProfileSchema
        from gpt_trader.config.types import Profile

        halted = ProfileSchema(
            profile_name="prod", emergency=EmergencyConfig(kill_switch_enabled=True)
        )
        loaded: list[Profile] = []

        def fake_load(self: ProfileLoader, profile: Profile) -> ProfileSchema:
            loaded.append(profile)
            return halted

        monkeypatch.setattr(ProfileLoader, "load", fake_load)
        checker = PreflightCheck(profile=profile_name)
        risk_config_stub.set(kill_switch_enabled=False)

        assert check_risk_configuration(checker) is True
        assert loaded == [Profile.PROD]
        assert any(
            "Kill switch" in w and "emergency.kill_switch_enabled" in w for w in checker.warnings
        )

    def test_shipped_canary_profile_does_not_report_kill_switch(
        self, risk_config_stub: RiskConfigStub
    ) -> None:
        """The repository canary profile is armed-not-engaged (#1300)."""
        checker = PreflightCheck(profile="canary")
        risk_config_stub.set(kill_switch_enabled=False)

        assert check_risk_configuration(checker) is True
        assert not any("Kill switch" in w for w in checker.warnings)

    def test_warns_when_reduce_only_mode(self, risk_config_stub: RiskConfigStub) -> None:
        """Should warn when reduce-only mode is enabled."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(reduce_only_mode=True)
        result = check_risk_configuration(checker)

        assert result is True
        assert any("Reduce-only" in w for w in checker.warnings)

    def test_warns_on_high_daily_loss_limit(self, risk_config_stub: RiskConfigStub) -> None:
        """Should warn when daily loss limit pct is high."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(daily_loss_limit_pct=0.15)
        result = check_risk_configuration(checker)

        assert result is True
        assert any("seems high" in w for w in checker.warnings)

    def test_warns_on_aggressive_leverage(self, risk_config_stub: RiskConfigStub) -> None:
        """Should warn when leverage exceeds 5x."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.set(max_leverage=8)
        result = check_risk_configuration(checker)

        assert result is True
        assert any("aggressive" in w for w in checker.warnings)

    def test_fails_on_exception(self, risk_config_stub: RiskConfigStub) -> None:
        """Should fail when config loading throws an exception."""
        checker = PreflightCheck(profile="prod")
        risk_config_stub.from_env.side_effect = Exception("Config error")
        result = check_risk_configuration(checker)

        assert result is False
        assert any("Failed to validate" in e for e in checker.errors)

    def test_prints_section_header(
        self, risk_config_stub: RiskConfigStub, capsys: pytest.CaptureFixture
    ) -> None:
        """Should print section header."""
        checker = PreflightCheck(profile="dev")
        risk_config_stub.set()
        check_risk_configuration(checker)

        captured = capsys.readouterr()
        assert "RISK MANAGEMENT" in captured.out
