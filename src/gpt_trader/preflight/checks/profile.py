from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from gpt_trader.app.config.profile_error_payloads import (
    profile_yaml_missing_payload,
    profile_yaml_parse_error_payload,
)
from gpt_trader.app.config.profile_loader import get_profile_registry_entry_by_name

if TYPE_CHECKING:
    from gpt_trader.preflight.core import PreflightCheck


def check_profile_configuration(checker: PreflightCheck) -> bool:
    """Validate the selected trading profile configuration file."""
    checker.section_header("8. PROFILE CONFIGURATION")

    entry = get_profile_registry_entry_by_name(checker.profile)
    profile_filename = entry.yaml_filename if entry else f"{checker.profile}.yaml"
    profile_path = Path("config") / "profiles" / profile_filename
    if profile_path.exists():
        checker.log_success(f"Profile '{checker.profile}' found at {profile_path}")

        try:
            import yaml

            with open(profile_path) as handle:
                profile_config = yaml.safe_load(handle)

            if checker.profile == "canary":
                # Appetite keys (daily loss, leverage) are no longer profile
                # settings: they derive from the RiskBudget (#1120 stage 3).
                expected = {
                    "trading.mode": "reduce_only",
                    "trading.position_sizing.max_position_size": 0.01,
                }

                for key, expected_value in expected.items():
                    keys = key.split(".")
                    value = profile_config
                    for part in keys:
                        value = value.get(part, {})

                    if value == expected_value:
                        checker.log_info(f"{key} = {value} ✓")
                    else:
                        checker.log_warning(f"{key} = {value}, expected {expected_value}")

            checker.log_success(f"Profile '{checker.profile}' validated")
            return True
        except Exception as exc:
            payload = profile_yaml_parse_error_payload(
                profile=checker.profile,
                path=profile_path,
                exception=exc,
            )
            checker.log_error(f"Failed to parse profile: {exc}", details=payload)
            return False

    payload = profile_yaml_missing_payload(
        profile=checker.profile,
        path=profile_path,
    )
    checker.log_warning(
        f"Profile '{checker.profile}' not found, will use defaults",
        details=payload,
    )
    if checker.profile == "canary":
        checker.log_info("Canary defaults: 0.01 BTC max, reduce-only")
    elif checker.profile == "prod":
        checker.log_warning("Production profile - ensure you've tested with canary first!")

    return True
