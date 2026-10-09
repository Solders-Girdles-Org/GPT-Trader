from __future__ import annotations

import os
from typing import TYPE_CHECKING

from gpt_trader.features.brokerages.coinbase.credentials import mask_key_name

if TYPE_CHECKING:
    from gpt_trader.preflight.core import PreflightCheck


def check_environment_variables(checker: PreflightCheck) -> bool:
    """Validate critical environment configuration and credential presence."""
    checker.section_header("3. ENVIRONMENT CONFIGURATION")

    ctx = checker.context
    all_good = True

    for var, (expected, strict) in ctx.expected_env_defaults().items():
        actual = os.getenv(var)
        if actual == expected:
            checker.log_info(f"{var}={expected}")
            continue

        if actual is None:
            message = f"{var} not set, expected '{expected}'"
        else:
            message = f"{var}={actual}, expected '{expected}'"

        if strict:
            checker.log_error(message)
            all_good = False
        else:
            checker.log_warning(message)

    modes = ctx.trading_modes()
    raw_modes = os.getenv("TRADING_MODES", "")
    if raw_modes:
        checker.log_info(f"TRADING_MODES={raw_modes}")
    else:
        checker.log_info("TRADING_MODES not set; defaulting to spot")

    unknown_modes = sorted({mode for mode in modes if mode not in {"spot", "cfm"}})
    if unknown_modes:
        checker.log_warning(f"Unknown TRADING_MODES entries: {', '.join(unknown_modes)}")

    cfm_in_modes = "cfm" in modes
    cfm_enabled = ctx.cfm_enabled()
    if cfm_in_modes and not cfm_enabled:
        message = "TRADING_MODES includes cfm but CFM_ENABLED is not set to 1"
        if ctx.profile == "dev":
            checker.log_warning(message)
        else:
            checker.log_error(message)
            all_good = False
    elif cfm_enabled and not cfm_in_modes:
        checker.log_warning("CFM_ENABLED=1 but TRADING_MODES does not include cfm")

    creds = ctx.resolve_cdp_credentials_info()
    if not creds:
        api_key = None
        private_key = None
        if ctx.should_skip_remote_checks():
            checker.log_warning(
                "CDP credentials not configured; remote connectivity checks will be skipped"
            )
        else:
            checker.log_error(
                "CDP credentials not found (set COINBASE_CREDENTIALS_FILE or CDP env vars)"
            )
            all_good = False
    else:
        api_key = creds.key_name
        private_key = creds.private_key
        masked_name = mask_key_name(creds.key_name)
        checker.log_info(f"CDP credentials resolved from {creds.source} ({masked_name})")
        for warning in creds.warnings:
            checker.log_warning(f"CDP credential warning: {warning}")

    if api_key:
        if api_key.startswith("organizations/") and "/apiKeys/" in api_key:
            source = creds.source if creds else "unknown"
            masked_name = mask_key_name(api_key)
            checker.log_success(f"CDP API key format valid ({masked_name}, source={source})")
        else:
            message = "Invalid CDP API key format. Expected: organizations/.../apiKeys/..."
            if ctx.should_skip_remote_checks():
                checker.log_warning(message)
            else:
                checker.log_error(message)
                all_good = False

    if private_key:
        if "BEGIN EC PRIVATE KEY" in private_key:
            checker.log_success("CDP private key found (EC format)")
        else:
            message = "Invalid private key format (must be EC private key)"
            if ctx.should_skip_remote_checks():
                checker.log_warning(message)
            else:
                checker.log_error(message)
                all_good = False

    risk_vars = {
        "RISK_MAX_LEVERAGE": (1, 10),
        "RISK_DAILY_LOSS_LIMIT_PCT": (0.0, 0.20),
        "RISK_MAX_POSITION_PCT_PER_SYMBOL": (0.01, 0.5),
    }

    for var, (min_val, max_val) in risk_vars.items():
        value = os.getenv(var)
        if value:
            try:
                num = float(value)
                if min_val <= num <= max_val:
                    checker.log_info(f"{var}={value} (within safe range)")
                else:
                    checker.log_warning(
                        f"{var}={value} outside recommended range [{min_val}, {max_val}]"
                    )
            except ValueError:
                checker.log_error(f"{var}={value} is not a valid number")
                all_good = False
        else:
            checker.log_warning(f"{var} not set, using defaults")

    return all_good
