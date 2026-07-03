"""Tests for profile loader container-driven API."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.config.profile_loader import (
    ProfileSchema,
    build_profile_config,
    load_profile,
)
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
from gpt_trader.config.types import Profile


@pytest.fixture(autouse=True)
def container_fixture():
    """Set up container for tests that require it."""
    config = BotConfig(symbols=["BTC-USD"])
    container = ApplicationContainer(config)
    set_application_container(container)
    yield container
    clear_application_container()


def _create_mock_config_factory() -> MagicMock:
    """Create a mock config factory that returns a mock BotConfig."""

    def factory(**kwargs):
        config = MagicMock()
        for key, value in kwargs.items():
            setattr(config, key, value)
        return config

    return MagicMock(side_effect=factory)


class TestBuildProfileConfig:
    """Tests for build_profile_config function."""

    def test_dev_profile_sets_mock_broker(self) -> None:
        factory = _create_mock_config_factory()

        result = build_profile_config(Profile.DEV, factory)

        assert result.mock_broker is True
        assert result.dry_run is True

    def test_demo_profile_conservative_settings(self) -> None:
        factory = _create_mock_config_factory()

        result = build_profile_config(Profile.DEMO, factory)

        # Demo should be conservative: shorts routed off onto the strategy
        # configs (the canonical source since the top-level alias retired).
        result.set_enable_shorts.assert_called_once_with(False)

    def test_production_profile_settings(self) -> None:
        factory = _create_mock_config_factory()

        result = build_profile_config(Profile.PROD, factory)

        # Production should have shorts enabled
        result.set_enable_shorts.assert_called_once_with(True)

    def test_canary_profile_reduce_only(self) -> None:
        factory = _create_mock_config_factory()

        result = build_profile_config(Profile.CANARY, factory)

        # Canary should be in reduce_only mode
        assert result.reduce_only_mode is True

    def test_spot_profile_no_shorts(self) -> None:
        factory = _create_mock_config_factory()

        result = build_profile_config(Profile.SPOT, factory)

        # Spot should have no shorts
        result.set_enable_shorts.assert_called_once_with(False)
        assert result.mock_broker is False


class TestProfileMapping:
    """Tests for profile to config mapping."""

    @pytest.mark.parametrize(
        "profile",
        [
            Profile.DEV,
            Profile.DEMO,
            Profile.SPOT,
            Profile.CANARY,
            Profile.OBSERVE,
            Profile.PROD,
            Profile.TEST,
        ],
    )
    def test_all_profiles_return_config(self, profile: Profile) -> None:
        factory = _create_mock_config_factory()

        result = build_profile_config(profile, factory)

        assert result is not None
        assert result.profile == profile


class TestLoadProfile:
    """Tests for load_profile convenience function."""

    def test_load_profile_returns_schema(self) -> None:
        """Test that load_profile returns a valid ProfileSchema."""
        schema = load_profile(Profile.DEV)

        assert isinstance(schema, ProfileSchema)
        assert schema.profile_name == "dev"

    @pytest.mark.parametrize("profile", list(Profile))
    def test_load_all_profiles(self, profile: Profile) -> None:
        """Test that all profiles can be loaded."""
        schema = load_profile(profile)

        assert isinstance(schema, ProfileSchema)
        assert schema.profile_name == profile.value
