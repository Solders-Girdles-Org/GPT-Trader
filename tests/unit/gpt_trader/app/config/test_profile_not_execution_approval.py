"""A profile name is never execution approval (prod/canary ADR Option A, #1122).

Pins the accepted decision in
docs/decisions/prod-canary-profile-meaning.md: profiles are config
snapshots; the gates in docs/DIRECTION.md and recorded human approval are
the only authorization path. No profile value — prod and canary included —
may switch on AI-submitted order flow, autonomy escalation, or approval
bypass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gpt_trader.app.config.profile_loader import (
    PROFILE_REGISTRY,
    ProfileLoader,
)
from gpt_trader.config.types import Profile

_PROFILES_DIR = Path("config/profiles")

# The event-driven paper lane is operator-enabled on the paper profile only
# (recorded approval 2026-07-07, PR #1205). Every other profile — the live
# prod/canary assets above all — must leave it off.
_EVENT_LANE_ENABLED_PROFILES = {Profile.PAPER}

# Keys whose presence in a profile YAML would let a config snapshot express
# an authorization decision. Autonomy lives in the audited autonomy log and
# the GPT_TRADER_IDEAS_AUTO_* env gates, never in a profile.
_FORBIDDEN_KEY_FRAGMENTS = ("autonomy", "auto_approval", "auto_execution")


def _iter_keys(node: object) -> list[str]:
    keys: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            keys.append(str(key))
            keys.extend(_iter_keys(value))
    elif isinstance(node, list):
        for item in node:
            keys.extend(_iter_keys(item))
    return keys


@pytest.mark.parametrize("profile", sorted(PROFILE_REGISTRY, key=lambda p: p.value))
def test_no_profile_enables_strategy_signal_order_flow(profile: Profile) -> None:
    loader = ProfileLoader()
    schema = loader.load(profile)
    config = loader.build_bot_config(schema, profile)

    # Strategy-signal routing into live order flow stays default-off for
    # every profile; turning it on is an explicit config act reviewed under
    # DIRECTION gates, not a property of any profile name.
    assert config.strategy_signal_proposals_enabled is False


@pytest.mark.parametrize("profile", sorted(PROFILE_REGISTRY, key=lambda p: p.value))
def test_event_lane_is_enabled_only_where_approval_was_recorded(profile: Profile) -> None:
    loader = ProfileLoader()
    schema = loader.load(profile)
    config = loader.build_bot_config(schema, profile)

    expected = profile in _EVENT_LANE_ENABLED_PROFILES
    assert config.event_driven_paper_lane_enabled is expected, (
        f"Profile '{profile.value}' must {'keep' if not expected else 'have'} the "
        "event-driven lane gate "
        f"{'off — a profile name is not execution approval' if not expected else 'on per the recorded operator approval'}"
    )


@pytest.mark.parametrize("yaml_path", sorted(_PROFILES_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_profile_yaml_cannot_express_autonomy_or_approval(yaml_path: Path) -> None:
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    offending = [
        key
        for key in _iter_keys(payload)
        if any(fragment in key.lower() for fragment in _FORBIDDEN_KEY_FRAGMENTS)
        # auto_shutdown_on_limit is a safety brake (halts trading), not an
        # authorization lever; tightening is always allowed.
        and key != "auto_shutdown_on_limit"
    ]

    assert not offending, (
        f"{yaml_path} carries key(s) {offending} that would let a profile express "
        "an authorization decision; autonomy belongs to the audited autonomy log "
        "(docs/decisions/prod-canary-profile-meaning.md)"
    )


def test_live_profiles_never_default_dry_run_protection_away_from_yaml() -> None:
    # The prod/canary YAML files are the reviewed source of truth for live
    # execution settings. This guards against a hardcoded fallback silently
    # widening what the reviewed YAML allows (e.g. canary losing
    # reduce-only) if the YAML fails to load.
    loader = ProfileLoader()
    canary = loader.load(Profile.CANARY)
    assert canary.trading.mode == "reduce_only"
    prod = loader.load(Profile.PROD)
    assert prod.execution.mock_broker is False
