"""Tests for readiness gate preflight checks."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpt_trader.preflight.checks.readiness import (
    DEFAULT_LIVENESS_EVENT_TYPES,
    check_readiness_report,
)
from gpt_trader.preflight.core import PreflightCheck


def _write_report(report_dir: Path, *, profile: str = "dev") -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "daily_report_2026-01-16.json"
    report_path.write_text(
        json.dumps(
            {
                "profile": profile,
                "health": {
                    "stale_marks": 0,
                    "ws_reconnects": 0,
                    "unfilled_orders": 0,
                    "api_errors": 0,
                },
                "risk": {
                    "guard_triggers": {"api_health": 0},
                    "circuit_breaker_state": {"triggered": False},
                },
            }
        ),
        encoding="utf-8",
    )
    return report_path


def _write_events_db(
    events_db_path: Path,
    *,
    events: list[tuple[str, datetime]] | None = None,
) -> None:
    events_db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(events_db_path) as connection:
        connection.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp TEXT, event_type TEXT)"
        )
        if not events:
            return
        connection.executemany(
            "INSERT INTO events (timestamp, event_type) VALUES (?, ?)",
            [
                (
                    timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    event_type,
                )
                for event_type, timestamp in events
            ],
        )


def test_readiness_liveness_passes_when_recent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime_data" / "dev"
    report_dir = runtime_root / "reports"
    _write_report(report_dir, profile="dev")

    _write_events_db(
        runtime_root / "events.db",
        events=[
            ("heartbeat", datetime.now(UTC) - timedelta(seconds=1)),
        ],
    )

    monkeypatch.delenv("EVENT_STORE_ROOT", raising=False)
    monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GPT_TRADER_READINESS_REPORT", str(report_dir))

    checker = PreflightCheck(profile="dev")
    assert check_readiness_report(checker) is True
    assert any("Readiness liveness" in message for message in checker.successes)


def test_readiness_liveness_fails_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime_data" / "dev"
    report_dir = runtime_root / "reports"
    _write_report(report_dir, profile="dev")

    _write_events_db(
        runtime_root / "events.db",
        events=[
            ("heartbeat", datetime.now(UTC) - timedelta(seconds=120)),
        ],
    )

    monkeypatch.delenv("EVENT_STORE_ROOT", raising=False)
    monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GPT_TRADER_READINESS_REPORT", str(report_dir))
    monkeypatch.setenv("GPT_TRADER_READINESS_LIVENESS_MAX_AGE_SECONDS", "10")

    checker = PreflightCheck(profile="dev")
    assert check_readiness_report(checker) is False
    assert any("Readiness liveness" in message for message in checker.errors)


def test_readiness_liveness_fails_when_events_db_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime_data" / "dev"
    report_dir = runtime_root / "reports"
    _write_report(report_dir, profile="dev")

    monkeypatch.delenv("EVENT_STORE_ROOT", raising=False)
    monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GPT_TRADER_READINESS_REPORT", str(report_dir))

    checker = PreflightCheck(profile="dev")
    assert check_readiness_report(checker) is False
    assert any("no heartbeat/price_tick events" in message for message in checker.errors)


def test_readiness_liveness_warn_only_allows_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime_data" / "dev"
    report_dir = runtime_root / "reports"
    _write_report(report_dir, profile="dev")

    _write_events_db(
        runtime_root / "events.db",
        events=[
            ("heartbeat", datetime.now(UTC) - timedelta(seconds=120)),
        ],
    )

    monkeypatch.delenv("EVENT_STORE_ROOT", raising=False)
    monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GPT_TRADER_READINESS_REPORT", str(report_dir))
    monkeypatch.setenv("GPT_TRADER_READINESS_LIVENESS_MAX_AGE_SECONDS", "10")
    monkeypatch.setenv("GPT_TRADER_PREFLIGHT_WARN_ONLY", "1")

    checker = PreflightCheck(profile="dev")
    assert check_readiness_report(checker) is True
    assert any("Readiness liveness" in message for message in checker.warnings)


def test_readiness_liveness_ignores_decision_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime_data" / "dev"
    report_dir = runtime_root / "reports"
    _write_report(report_dir, profile="dev")

    now = datetime.now(UTC)
    _write_events_db(
        runtime_root / "events.db",
        events=[
            ("order_decision_trace", now - timedelta(seconds=1)),
            ("heartbeat", now - timedelta(seconds=600)),
            ("price_tick", now - timedelta(seconds=600)),
        ],
    )

    monkeypatch.delenv("EVENT_STORE_ROOT", raising=False)
    monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GPT_TRADER_READINESS_REPORT", str(report_dir))
    monkeypatch.setenv("GPT_TRADER_READINESS_LIVENESS_MAX_AGE_SECONDS", "300")

    checker = PreflightCheck(profile="dev")
    assert check_readiness_report(checker) is False
    assert any("Readiness liveness" in message for message in checker.errors)


def test_readiness_liveness_passes_with_price_tick_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime_data" / "dev"
    report_dir = runtime_root / "reports"
    _write_report(report_dir, profile="dev")

    now = datetime.now(UTC)
    _write_events_db(
        runtime_root / "events.db",
        events=[
            ("price_tick", now - timedelta(seconds=20)),
        ],
    )

    monkeypatch.delenv("EVENT_STORE_ROOT", raising=False)
    monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GPT_TRADER_READINESS_REPORT", str(report_dir))
    monkeypatch.setenv("GPT_TRADER_READINESS_LIVENESS_MAX_AGE_SECONDS", "300")

    checker = PreflightCheck(profile="dev")
    assert check_readiness_report(checker) is True
    assert any("Readiness liveness" in message for message in checker.successes)


def test_readiness_liveness_default_event_types_include_price_tick() -> None:
    assert DEFAULT_LIVENESS_EVENT_TYPES == ("heartbeat", "price_tick")
