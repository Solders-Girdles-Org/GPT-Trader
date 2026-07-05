"""Tests for CLI config building helpers."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.config.profile_loader import DEFAULT_RUNTIME_PROFILE_NAME
from gpt_trader.cli import services
from gpt_trader.config.types import Profile


def _make_args(**overrides: object) -> Namespace:
    defaults: dict[str, object] = {
        "config": None,
        "dry_run": False,
        "symbols": None,
        "interval": None,
        "target_leverage": None,
        "reduce_only_mode": False,
        "time_in_force": None,
        "enable_order_preview": False,
    }
    profile_value = overrides.pop("profile", None)
    defaults.update(overrides)
    if profile_value is not None:
        defaults["profile"] = profile_value
    return Namespace(**defaults)  # type: ignore[arg-type]


def test_default_profile_applied_when_missing() -> None:
    args = _make_args()

    config = services.build_config_from_args(args)

    assert config.profile == Profile.DEV
    assert config.profile.value == DEFAULT_RUNTIME_PROFILE_NAME
    assert config.environment == "development"


def test_profile_overrides_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_SYMBOLS", "BTC-PERP,ETH-PERP")
    monkeypatch.setenv("INTERVAL", "5")

    args = _make_args(profile="dev")
    config = services.build_config_from_args(args)

    assert config.symbols == ["BTC-USD", "ETH-USD"]
    assert config.interval == 60


def test_cli_args_override_profile_settings() -> None:
    args = _make_args(profile="dev", symbols=["DOGE-PERP"], interval=10)

    config = services.build_config_from_args(args)

    assert config.symbols == ["DOGE-PERP"]
    assert config.interval == 10


def test_env_profile_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GPT_TRADER_PROFILE", "spot")

    args = _make_args()
    config = services.build_config_from_args(args)

    assert config.profile == Profile.SPOT


def test_config_file_profile_used_when_no_cli(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.yaml"
    config_path.write_text('profile_name: "prod"\n', encoding="utf-8")

    args = _make_args(config=str(config_path))
    config = services.build_config_from_args(args)

    assert config.profile == "prod"


def test_cli_profile_overrides_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.yaml"
    config_path.write_text('profile_name: "prod"\n', encoding="utf-8")

    args = _make_args(config=str(config_path), profile="dev")
    config = services.build_config_from_args(args)

    assert config.profile == "dev"


def test_load_profile_config_builds_observe_profile() -> None:
    config = services.load_profile_config(Profile.OBSERVE)

    assert config.profile == Profile.OBSERVE
    assert config.environment == "development"
    assert config.dry_run is True
    assert config.mock_broker is False
    assert config.symbols == ["BTC-USD", "ETH-USD"]


def test_load_profile_config_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="Unknown profile"):
        services.load_profile_config("not-a-profile")


def test_load_profile_config_accepts_case_insensitive_name() -> None:
    config = services.load_profile_config("OBSERVE")

    assert config.profile == Profile.OBSERVE


def test_apply_profile_kwargs_maps_strategy_signal_proposals_gate() -> None:
    config = BotConfig(strategy_signal_proposals_enabled=False)

    services._apply_profile_kwargs(config, {"strategy_signal_proposals_enabled": True})

    assert config.strategy_signal_proposals_enabled is True


def test_apply_profile_kwargs_maps_event_driven_paper_lane_gate() -> None:
    config = BotConfig(event_driven_paper_lane_enabled=False)

    services._apply_profile_kwargs(config, {"event_driven_paper_lane_enabled": True})

    assert config.event_driven_paper_lane_enabled is True


def test_apply_profile_kwargs_maps_risk_budget_runtime_seed_gate() -> None:
    config = BotConfig(risk_budget_runtime_seed_enabled=False)

    services._apply_profile_kwargs(config, {"risk_budget_runtime_seed_enabled": True})

    assert config.risk_budget_runtime_seed_enabled is True
