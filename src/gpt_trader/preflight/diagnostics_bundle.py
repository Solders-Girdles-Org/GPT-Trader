from __future__ import annotations

import io
import os
import platform
from collections.abc import Mapping
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gpt_trader.preflight.context import PreflightContext
from gpt_trader.preflight.core import PreflightCheck
from gpt_trader.preflight.report import evaluate_preflight_status
from gpt_trader.preflight.validation_result import PreflightResultPayload

SCHEMA_VERSION = "gpt_trader:preflight:diagnostics:v1"
CHECK_NAMES = (
    "check_environment_variables",
    "check_pretrade_diagnostics",
    "check_readiness_report",
)

MAX_CHECKS = 20
MAX_DETAIL_LENGTH = 200
MAX_COLLECTION_ITEMS = 5
MAX_DEPTH = 2
TRUNCATION_SUFFIX = "..."
SENSITIVE_TOKENS = (
    "key",
    "secret",
    "token",
    "password",
    "private",
    "credentials",
)


def build_diagnostics_bundle(
    profile: str,
    *,
    verbose: bool = False,
    warn_only: bool = False,
) -> dict[str, Any]:
    """Return a deterministic diagnostics bundle built from a subset of preflight checks."""

    checker = PreflightCheck(verbose=verbose, profile=profile)
    context = checker.context
    diagnostic_only = warn_only or os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY") == "1"

    previous_warn_only = os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY")
    if warn_only:
        os.environ["GPT_TRADER_PREFLIGHT_WARN_ONLY"] = "1"

    try:
        with redirect_stdout(io.StringIO()):
            for check_name in CHECK_NAMES:
                check = getattr(checker, check_name, None)
                if check is None:
                    continue
                try:
                    check()
                except Exception as exc:  # pragma: no cover - defensive safeguard
                    checker.log_error(f"{check_name} failed to run: {exc}")
    finally:
        if warn_only:
            if previous_warn_only is None:
                os.environ.pop("GPT_TRADER_PREFLIGHT_WARN_ONLY", None)
            else:
                os.environ["GPT_TRADER_PREFLIGHT_WARN_ONLY"] = previous_warn_only

    readiness = _format_readiness_payload(context.results, diagnostic_only=diagnostic_only)
    bundle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "profile": profile,
        "bundle": {
            "readiness": readiness,
            "config": _build_config_summary(context, warn_only),
            "environment": _build_environment_summary(context),
        },
    }

    return bundle


def _format_readiness_payload(
    results: list[PreflightResultPayload],
    *,
    diagnostic_only: bool = False,
) -> dict[str, Any]:
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for result in results:
        status = result.get("status")
        if status in counts:
            counts[status] += 1

    total = sum(counts.values())
    if total == 0:
        status = "UNKNOWN"
        message = "Diagnostics bundle collected no checks"
    else:
        status, message = evaluate_preflight_status(
            success_count=counts["pass"],
            warning_count=counts["warn"],
            error_count=counts["fail"],
        )
    if diagnostic_only:
        message = (
            "DIAGNOSTIC-ONLY: warn-only results do not satisfy the live readiness gate. "
            f"{message}"
        )

    checks = []
    for result in results[:MAX_CHECKS]:
        checks.append(
            {
                "status": result.get("status", "pass"),
                "message": _bound_string(result.get("message", "")),
                "details": _sanitize_details(result.get("details", {})),
            }
        )

    return {
        "status": status,
        "message": message,
        "diagnostic_only": diagnostic_only,
        "counts": {
            "pass": counts["pass"],
            "warn": counts["warn"],
            "fail": counts["fail"],
            "total": total,
        },
        "checks": checks,
    }


def _build_config_summary(context: PreflightContext, warn_only: bool) -> dict[str, Any]:
    expected: dict[str, dict[str, Any]] = {}
    defaults = context.expected_env_defaults()
    for key in sorted(defaults):
        value, required = defaults[key]
        expected[key] = {
            "value": _redact_if_sensitive(key, str(value)),
            "required": bool(required),
        }

    return {
        "profile": context.profile,
        "warn_only": bool(warn_only),
        "trading_modes": sorted(context.trading_modes()),
        "cfm_enabled": context.cfm_enabled(),
        "intx_perps_enabled": False,
        "intends_real_orders": context.intends_real_orders(),
        "requires_trade_permission": context.requires_trade_permission(),
        "expected_env_defaults": expected,
        "checks_run": list(CHECK_NAMES),
    }


def _build_environment_summary(context: PreflightContext) -> dict[str, Any]:
    credentials = context.resolve_cdp_credentials_info()
    tzname = datetime.now(UTC).astimezone().tzname() or "UTC"

    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cwd": Path.cwd().as_posix(),
        "timezone": tzname,
        "remote_checks_skipped": context.should_skip_remote_checks(),
        "cdp_credentials_present": credentials is not None,
    }


def _should_redact(name: str) -> bool:
    lower_name = name.lower()
    return any(token in lower_name for token in SENSITIVE_TOKENS)


def _redact_if_sensitive(name: str, value: str) -> str:
    if _should_redact(name):
        return "<redacted>"
    return value


def _bound_string(value: str) -> str:
    if len(value) <= MAX_DETAIL_LENGTH:
        return value
    return value[: MAX_DETAIL_LENGTH - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX


def _sanitize_details(details: Any, depth: int = MAX_DEPTH) -> Any:
    return _sanitize_value(details, depth=depth)


def _sanitize_value(value: Any, *, depth: int) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key in sorted(value):
            if len(sanitized) >= MAX_COLLECTION_ITEMS:
                break
            if _should_redact(key):
                sanitized[key] = "<redacted>"
                continue
            sanitized[key] = _sanitize_value(value[key], depth=depth - 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        limited: list[Any] = []
        for item in value:
            if len(limited) >= MAX_COLLECTION_ITEMS:
                break
            limited.append(_sanitize_value(item, depth=depth - 1))
        return limited
    if depth <= 0:
        return _bound_string(str(value))
    if isinstance(value, str):
        return _bound_string(value)
    if isinstance(value, (int, float, bool)):
        return value
    if value is None:
        return None
    return _bound_string(str(value))
