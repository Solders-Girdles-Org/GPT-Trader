"""Tests for the event store redaction preflight check."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gpt_trader.preflight.checks.event_store import check_event_store_redaction
from gpt_trader.preflight.core import PreflightCheck


def _write_events_db(events_db_path: Path, *, events: list[tuple[str, str]] | None = None) -> None:
    events_db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(events_db_path) as connection:
        connection.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, payload TEXT)"
        )
        if not events:
            return
        connection.executemany(
            "INSERT INTO events (event_type, payload) VALUES (?, ?)",
            events,
        )


class TestCheckEventStoreRedaction:
    """Tests for event store redaction check."""

    def test_passes_when_no_sensitive_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime_root = tmp_path / "runtime_data" / "dev"
        events_path = runtime_root / "events.db"
        payload = json.dumps({"status": "ok", "nested": {"value": 1}})
        _write_events_db(events_path, events=[("heartbeat", payload)])

        monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
        checker = PreflightCheck(profile="dev")

        assert check_event_store_redaction(checker) is True
        assert any("Event store redaction check passed" in s for s in checker.successes)

    def test_passes_when_redacted_values_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime_root = tmp_path / "runtime_data" / "dev"
        events_path = runtime_root / "events.db"
        payload = json.dumps({"private_key": "[REDACTED]", "apiKey": "[REDACTED]"})
        _write_events_db(events_path, events=[("order", payload)])

        monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
        checker = PreflightCheck(profile="dev")

        assert check_event_store_redaction(checker) is True
        assert any("Event store redaction check passed" in s for s in checker.successes)

    def test_fails_on_pem_private_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime_root = tmp_path / "runtime_data" / "prod"
        events_path = runtime_root / "events.db"
        payload = json.dumps({"payload": "-----BEGIN EC PRIVATE KEY-----\\nABC"})
        _write_events_db(events_path, events=[("order", payload)])

        monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
        checker = PreflightCheck(profile="prod")

        assert check_event_store_redaction(checker) is False
        assert any("Event store redaction check found" in e for e in checker.errors)
        joined = " ".join(checker.errors)
        assert "BEGIN" not in joined
        assert "ABC" not in joined

    def test_fails_on_unredacted_authorization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime_root = tmp_path / "runtime_data" / "prod"
        events_path = runtime_root / "events.db"
        payload = json.dumps({"authorization": "Bearer abc.def.ghi"})
        _write_events_db(events_path, events=[("auth", payload)])

        monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
        checker = PreflightCheck(profile="prod")

        assert check_event_store_redaction(checker) is False
        assert any("Unredacted secret in event" in e for e in checker.errors)
        assert any("authorization" in e for e in checker.errors)
        assert not any("Bearer" in e for e in checker.errors)

    def test_missing_events_db_warns_in_dev(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
        checker = PreflightCheck(profile="dev")

        assert check_event_store_redaction(checker) is True
        assert any("Events DB not found" in w for w in checker.warnings)

    def test_missing_events_db_errors_in_prod(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
        checker = PreflightCheck(profile="prod")

        assert check_event_store_redaction(checker) is False
        assert any("Events DB not found" in e for e in checker.errors)

    def test_missing_events_db_warn_only_in_prod(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GPT_TRADER_RUNTIME_ROOT", str(tmp_path))
        monkeypatch.setenv("GPT_TRADER_PREFLIGHT_WARN_ONLY", "1")
        checker = PreflightCheck(profile="prod")

        assert check_event_store_redaction(checker) is True
        assert any("Events DB not found" in w for w in checker.warnings)
