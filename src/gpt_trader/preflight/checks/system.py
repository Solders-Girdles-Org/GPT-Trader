from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gpt_trader.preflight.core import PreflightCheck


def _build_coinbase_time_client(api_key: str, private_key: str) -> Any:
    from gpt_trader.features.brokerages.coinbase.auth import create_cdp_jwt_auth
    from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient

    auth = create_cdp_jwt_auth(
        api_key=api_key,
        private_key=private_key,
    )
    return CoinbaseClient(
        base_url="https://api.coinbase.com",
        auth=auth,
        api_mode="advanced",
    )


def check_python_version(checker: PreflightCheck) -> bool:
    """Verify Python version meets minimum requirement."""
    checker.section_header("1. PYTHON VERSION CHECK")

    version = sys.version_info
    if version.major == 3 and version.minor >= 12:
        checker.log_success(
            f"Python {version.major}.{version.minor}.{version.micro} meets requirements"
        )
        return True

    checker.log_error(f"Python {version.major}.{version.minor} < 3.12 required")
    return False


def check_system_time(checker: PreflightCheck) -> bool:
    """Verify system clock synchronization against Coinbase server time."""
    checker.section_header("9. SYSTEM TIME SYNC")

    ctx = checker.context
    try:
        system_time = datetime.now(UTC)

        api_key, private_key = ctx.resolve_cdp_credentials()
        if api_key and private_key:
            if ctx.should_skip_remote_checks():
                checker.log_warning("Skipping Coinbase server time verification by policy")
            else:
                try:
                    client = _build_coinbase_time_client(
                        api_key=api_key,
                        private_key=private_key,
                    )
                    server_time_resp = client.get_time()
                    if not server_time_resp or "iso" not in server_time_resp:
                        raise ValueError("Coinbase time response missing 'iso'")

                    server_time = datetime.fromisoformat(
                        server_time_resp["iso"].replace("Z", "+00:00")
                    )
                    diff = abs((system_time - server_time).total_seconds())

                    if diff < 1:
                        checker.log_success(f"System clock synchronized (drift: {diff:.2f}s)")
                        return True
                    if diff < 5:
                        checker.log_warning(f"System clock drift: {diff:.2f}s (acceptable)")
                        return True

                    checker.log_error(f"System clock drift: {diff:.2f}s - SYNC REQUIRED")
                    checker.log_info("Run: sudo ntpdate -s time.nist.gov")
                    return False
                except Exception as exc:
                    checker.log_error(f"Cannot verify Coinbase server time: {exc}")
                    return False

        if 2024 <= system_time.year <= 2030:
            checker.log_warning("Cannot verify time sync, but system time seems reasonable")
            checker.log_info(f"System time: {system_time.isoformat()}")
            return True

        checker.log_error(f"System time seems wrong: {system_time}")
        return False

    except Exception as exc:
        checker.log_error(f"Failed to check system time: {exc}")
        return False


def check_disk_space(checker: PreflightCheck) -> bool:
    """Ensure adequate disk space available."""
    checker.section_header("10. DISK SPACE CHECK")

    try:
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        used_pct = (usage.used / usage.total) * 100

        if free_gb > 1.0:
            checker.log_success(
                f"Disk space: {free_gb:.1f}GB free of {total_gb:.1f}GB ({used_pct:.0f}% used)"
            )
            return True

        if free_gb > 0.5:
            checker.log_warning(f"Low disk space: {free_gb:.1f}GB free")
            return True

        checker.log_error(f"Critical: Only {free_gb:.1f}GB free")
        return False

    except Exception as exc:
        checker.log_error(f"Failed to check disk space: {exc}")
        return False
