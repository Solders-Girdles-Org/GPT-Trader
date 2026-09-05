"""A selected profile must load successfully before runtime creation."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config.profile_loader import ProfileLoader
from gpt_trader.app.config.profile_schema import ProfileValidationError
from gpt_trader.config.types import Profile


@pytest.mark.parametrize("profile", list(Profile))
def test_malformed_existing_profile_refuses_defaults(tmp_path: Path, profile: Profile) -> None:
    (tmp_path / f"{profile.value}.yaml").write_text("trading: [unterminated")
    with pytest.raises(ProfileValidationError, match="Cannot load profile"):
        ProfileLoader(tmp_path).load(profile)


def test_unreadable_existing_profile_refuses_defaults(tmp_path: Path) -> None:
    # A directory is present but cannot be read as a YAML file, even as root.
    (tmp_path / "prod.yaml").mkdir()
    with pytest.raises(ProfileValidationError, match="Cannot load profile 'prod'"):
        ProfileLoader(tmp_path).load(Profile.PROD)


def test_cli_does_not_construct_runtime_after_profile_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gpt_trader.cli import main, services

    (tmp_path / "prod.yaml").write_text("trading: [unterminated")
    monkeypatch.setattr(services, "ProfileLoader", lambda: ProfileLoader(tmp_path))
    instantiate = MagicMock(side_effect=AssertionError("Runtime must not be constructed"))
    monkeypatch.setattr(services, "instantiate_bot", instantiate)
    assert main(["run", "--profile", "prod"]) != 0
    instantiate.assert_not_called()
