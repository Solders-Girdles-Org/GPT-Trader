"""Profile emergency.kill_switch_enabled and env wiring into BotConfig (#1300)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.config.profile_loader import ProfileLoader
from gpt_trader.app.config.profile_schema import ProfileSchema, ProfileValidationError
from gpt_trader.app.containers.risk_validation import RiskValidationContainer
from gpt_trader.cli import services
from gpt_trader.config.types import Profile

KILL_SWITCH_ENV_VAR = "RISK_KILL_SWITCH_ENABLED"


def _live_risk_manager(config: BotConfig):
    """Build the risk manager through the live construction path."""
    return RiskValidationContainer(config=config, event_store_provider=MagicMock).risk_manager


class TestEmergencySection:
    def test_absent_section_leaves_kill_switch_off(self) -> None:
        schema = ProfileSchema.from_yaml({}, "custom")
        assert schema.emergency.kill_switch_enabled is False

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(True, True), (False, False), ("yes", True), ("off", False), ("armed", True)],
    )
    def test_kill_switch_value_parsed_fail_closed(self, value: object, expected: bool) -> None:
        schema = ProfileSchema.from_yaml({"emergency": {"kill_switch_enabled": value}}, "custom")
        assert schema.emergency.kill_switch_enabled is expected

    def test_non_mapping_section_rejected(self) -> None:
        with pytest.raises(ProfileValidationError, match="emergency"):
            ProfileSchema.from_yaml({"emergency": True}, "custom")

    def test_loader_maps_emergency_kill_switch_to_bot_config(self) -> None:
        loader = ProfileLoader()
        schema = ProfileSchema.from_yaml({"emergency": {"kill_switch_enabled": True}}, "dev")

        assert loader.to_bot_config_kwargs(schema, Profile.DEV)["kill_switch_enabled"] is True
        assert loader.build_bot_config(schema, Profile.DEV).kill_switch_enabled is True

    def test_profile_yaml_setting_true_halts_live_risk_manager(self, tmp_path: Path) -> None:
        (tmp_path / "dev.yaml").write_text(
            "profile_name: dev\nemergency:\n  kill_switch_enabled: true\n"
        )
        loader = ProfileLoader(profiles_dir=tmp_path)
        config = loader.build_bot_config(loader.load(Profile.DEV), Profile.DEV)

        assert config.kill_switch_enabled is True
        assert _live_risk_manager(config).config.kill_switch_enabled is True

    def test_repository_canary_profile_is_armed_not_engaged(self) -> None:
        # canary is a live-operation asset (docs/decisions/prod-canary-profile-meaning.md).
        loader = ProfileLoader()
        schema = loader.load(Profile.CANARY)

        assert schema.emergency.kill_switch_enabled is False
        assert loader.build_bot_config(schema, Profile.CANARY).kill_switch_enabled is False

    def test_canary_fallback_defaults_match_yaml(self, tmp_path: Path) -> None:
        # No YAML on disk: the hardcoded canary defaults must match canary.yaml.
        schema = ProfileLoader(profiles_dir=tmp_path).load(Profile.CANARY)
        assert schema.emergency.kill_switch_enabled is False


class TestBotConfigEnv:
    def test_from_env_reads_kill_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV_VAR, "1")
        assert BotConfig.from_env().kill_switch_enabled is True

    def test_from_env_default_off(self) -> None:
        assert BotConfig.from_env().kill_switch_enabled is False

    def test_from_dict_coerces_kill_switch_fail_closed(self) -> None:
        config = BotConfig.from_dict({"symbols": ["BTC-USD"], "kill_switch_enabled": "on"})
        assert config.kill_switch_enabled is True

    def test_legacy_profile_dict_maps_emergency(self) -> None:
        with pytest.warns(DeprecationWarning):
            config = BotConfig.from_dict(
                {"profile_name": "custom", "emergency": {"kill_switch_enabled": True}}
            )
        assert config.kill_switch_enabled is True


class TestCliPrecedence:
    def _args(self, profile: str) -> Namespace:
        return Namespace(
            config=None,
            profile=profile,
            dry_run=False,
            symbols=None,
            interval=None,
            target_leverage=None,
            reduce_only_mode=False,
            time_in_force=None,
            enable_order_preview=False,
        )

    def test_profile_without_halt_does_not_clear_env_kill_switch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV_VAR, "1")
        config = services.build_config_from_args(self._args("dev"))
        assert config.kill_switch_enabled is True

    def test_canary_profile_starts_disengaged_via_cli(self) -> None:
        config = services.build_config_from_args(self._args("canary"))

        assert config.kill_switch_enabled is False
        assert _live_risk_manager(config).config.kill_switch_enabled is False

    def test_env_engages_kill_switch_under_canary_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV_VAR, "1")
        config = services.build_config_from_args(self._args("canary"))

        assert _live_risk_manager(config).config.kill_switch_enabled is True

    def test_dev_profile_without_env_leaves_kill_switch_off(self) -> None:
        config = services.build_config_from_args(self._args("dev"))
        assert config.kill_switch_enabled is False
