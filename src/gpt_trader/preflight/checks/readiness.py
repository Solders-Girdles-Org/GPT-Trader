from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gpt_trader.preflight.core import PreflightCheck


def _is_warn_only() -> bool:
    return os.getenv("GPT_TRADER_PREFLIGHT_WARN_ONLY", "0") == "1"


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _get_env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_report_path(raw_path: str) -> tuple[Path | None, str | None]:
    path = Path(raw_path).expanduser()
    if path.is_dir():
        candidates = sorted(path.glob("daily_report_*.json"))
        if not candidates:
            return None, f"No daily_report_*.json files under {path}"
        return candidates[-1], None
    if path.exists():
        return path, None
    return None, f"Readiness report not found: {path}"


def _load_report(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data, None
        return None, "Report is not a JSON object"
    except Exception as exc:
        return None, f"Failed to read report: {exc}"


def _parse_timestamp(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


DEFAULT_LIVENESS_EVENT_TYPES = ("heartbeat", "price_tick")


def _fetch_last_liveness_event(
    path: Path,
    event_types: tuple[str, ...] = DEFAULT_LIVENESS_EVENT_TYPES,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
            connection.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in event_types)
            row = connection.execute(
                f"""
                SELECT id, timestamp, event_type
                FROM events
                WHERE event_type IN ({placeholders})
                ORDER BY datetime(timestamp) DESC, id DESC
                LIMIT 1
                """,
                event_types,
            ).fetchone()
    except sqlite3.Error as exc:
        return None, f"Failed to read events DB: {exc}"

    if row is None or row["timestamp"] is None:
        return None, None

    timestamp = _parse_timestamp(str(row["timestamp"]))
    if timestamp is None:
        return None, None

    age_seconds = max(0.0, (datetime.now(UTC) - timestamp).total_seconds())
    return (
        {
            "id": int(row["id"]),
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "event_type": str(row["event_type"]),
            "age_seconds": age_seconds,
        },
        None,
    )


def _resolve_event_store_path(profile: str, report_path: Path) -> Path:
    override_root = os.getenv("EVENT_STORE_ROOT")
    if override_root:
        override_path = Path(override_root).expanduser()
        if "runtime_data" not in set(override_path.parts):
            override_path = override_path / "runtime_data" / profile
        return override_path / "events.db"

    candidate = report_path.parent.parent / "events.db"
    if candidate.exists():
        return candidate

    runtime_root = Path(os.getenv("GPT_TRADER_RUNTIME_ROOT", ".")).expanduser()
    return runtime_root / "runtime_data" / profile / "events.db"


def check_readiness_report(checker: PreflightCheck) -> bool:
    """Validate readiness report metrics (optional)."""
    checker.section_header("12. READINESS GATE (OPTIONAL)")

    report_env = os.getenv("GPT_TRADER_READINESS_REPORT")
    if not report_env:
        checker.log_info("Readiness gate skipped (set GPT_TRADER_READINESS_REPORT to enable)")
        return True

    warn_only = _is_warn_only()
    report_path, path_error = _resolve_report_path(report_env)
    if path_error:
        if warn_only:
            checker.log_warning(path_error)
            return True
        checker.log_error(path_error)
        return False

    assert report_path is not None
    report, load_error = _load_report(report_path)
    if load_error or report is None:
        message = load_error or "Readiness report is empty"
        if warn_only:
            checker.log_warning(message)
            return True
        checker.log_error(message)
        return False

    checker.log_info(f"Using readiness report: {report_path}")

    report_profile = report.get("profile")
    if report_profile and report_profile != checker.profile:
        checker.log_warning(
            f"Readiness report profile '{report_profile}' != preflight profile '{checker.profile}'"
        )

    health = report.get("health")
    risk = report.get("risk")
    if not isinstance(health, dict):
        message = "Readiness report missing 'health' section"
        if warn_only:
            checker.log_warning(message)
            return True
        checker.log_error(message)
        return False
    if not isinstance(risk, dict):
        message = "Readiness report missing 'risk' section"
        if warn_only:
            checker.log_warning(message)
            return True
        checker.log_error(message)
        return False

    thresholds = {
        "stale_marks_max": _get_env_int("GPT_TRADER_READINESS_STALE_MARKS_MAX", 0),
        "ws_reconnects_max": _get_env_int("GPT_TRADER_READINESS_WS_RECONNECTS_MAX", 3),
        "unfilled_orders_max": _get_env_int("GPT_TRADER_READINESS_UNFILLED_ORDERS_MAX", 0),
        "api_errors_max": _get_env_int("GPT_TRADER_READINESS_API_ERRORS_MAX", 0),
        "guard_triggers_max": _get_env_int("GPT_TRADER_READINESS_GUARD_TRIGGERS_MAX", 0),
        "liveness_max_age_seconds": _get_env_int(
            "GPT_TRADER_READINESS_LIVENESS_MAX_AGE_SECONDS", 300
        ),
    }

    stale_marks = _coerce_int(health.get("stale_marks", health.get("stale_marks_count")))
    ws_reconnects = _coerce_int(health.get("ws_reconnects"))
    unfilled_orders = _coerce_int(health.get("unfilled_orders"))
    api_errors = _coerce_int(health.get("api_errors"))

    guard_triggers = risk.get("guard_triggers", {})
    guard_triggers_total = 0
    if isinstance(guard_triggers, dict):
        guard_triggers_total = sum(_coerce_int(value) for value in guard_triggers.values())
    else:
        guard_triggers_total = _coerce_int(guard_triggers)

    circuit_breaker_state = risk.get("circuit_breaker_state", {})
    circuit_breaker_triggered = False
    if isinstance(circuit_breaker_state, dict):
        circuit_breaker_triggered = bool(circuit_breaker_state.get("triggered"))

    checks = [
        ("stale marks", stale_marks, thresholds["stale_marks_max"]),
        ("ws reconnects", ws_reconnects, thresholds["ws_reconnects_max"]),
        ("unfilled orders", unfilled_orders, thresholds["unfilled_orders_max"]),
        ("api errors", api_errors, thresholds["api_errors_max"]),
        ("guard triggers", guard_triggers_total, thresholds["guard_triggers_max"]),
    ]

    all_good = True
    for label, value, max_allowed in checks:
        if value <= max_allowed:
            checker.log_success(f"Readiness {label}: {value} <= {max_allowed}")
        else:
            message = f"Readiness {label}: {value} > {max_allowed}"
            if warn_only:
                checker.log_warning(message)
            else:
                checker.log_error(message)
            all_good = False

    event_store_path = _resolve_event_store_path(checker.profile, report_path)
    last_event, last_event_error = (None, None)
    if event_store_path.exists():
        last_event, last_event_error = _fetch_last_liveness_event(event_store_path)
    if last_event_error:
        if warn_only:
            checker.log_warning(last_event_error)
        else:
            checker.log_error(last_event_error)
            all_good = False
    if last_event is None:
        message = f"Readiness liveness: no heartbeat/price_tick events in {event_store_path}"
        if warn_only:
            checker.log_warning(message)
        else:
            checker.log_error(message)
            all_good = False
    else:
        max_age = thresholds["liveness_max_age_seconds"]
        age_seconds = float(last_event.get("age_seconds", 0.0) or 0.0)
        event_type = last_event.get("event_type", "event")
        if age_seconds <= max_age:
            checker.log_success(
                f"Readiness liveness ({event_type}): {int(age_seconds)}s <= {max_age}s"
            )
        else:
            message = f"Readiness liveness ({event_type}): {int(age_seconds)}s > {max_age}s"
            if warn_only:
                checker.log_warning(message)
            else:
                checker.log_error(message)
            all_good = False

    if circuit_breaker_triggered:
        message = "Readiness circuit breaker triggered in report"
        if warn_only:
            checker.log_warning(message)
        else:
            checker.log_error(message)
        all_good = False
    else:
        checker.log_success("Readiness circuit breaker: not triggered")

    return all_good or warn_only


__all__ = ["check_readiness_report"]
