"""Edge-case tests for daily report loaders."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from gpt_trader.monitoring.daily_report.loaders import (
    load_events_since,
    load_liveness_snapshot,
    load_metrics,
    load_runtime_fingerprint,
    load_unfilled_orders_count,
)


def test_load_metrics_missing_file_returns_empty(tmp_path) -> None:
    missing = tmp_path / "metrics.json"

    assert load_metrics(missing) == {}


def test_load_metrics_invalid_json_returns_empty(tmp_path) -> None:
    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text("{bad json")

    assert load_metrics(metrics_file) == {}


def test_load_events_since_skips_invalid_entries(tmp_path) -> None:
    events_file = tmp_path / "events.jsonl"
    cutoff = datetime(2024, 1, 1, tzinfo=UTC)
    valid_event = {"timestamp": "2024-01-01T02:00:00Z", "id": "ok"}
    lines = [
        "",
        "{invalid json",
        json.dumps({"id": "missing-ts"}),
        json.dumps({"timestamp": "not-a-time"}),
        json.dumps(valid_event),
    ]
    events_file.write_text("\n".join(lines))

    events = load_events_since(events_file, cutoff)

    assert len(events) == 1
    assert events[0]["id"] == "ok"
    assert events[0]["timestamp"] == "2024-01-01T02:00:00+00:00"


def test_load_events_since_filters_by_cutoff(tmp_path) -> None:
    events_file = tmp_path / "events.jsonl"
    cutoff = datetime(2024, 1, 1, tzinfo=UTC)
    before = {"timestamp": "2023-12-31T23:59:59Z", "id": "before"}
    at_cutoff = {"timestamp": "2024-01-01T00:00:00Z", "id": "cutoff"}
    after = {"timestamp": "2024-01-01T01:00:00Z", "id": "after"}
    events_file.write_text(
        "\n".join([json.dumps(before), json.dumps(at_cutoff), json.dumps(after)])
    )

    events = load_events_since(events_file, cutoff)

    assert [event["id"] for event in events] == ["cutoff", "after"]


def test_load_events_since_falls_back_to_db(tmp_path) -> None:
    events_db = tmp_path / "events.db"
    connection = sqlite3.connect(str(events_db))
    connection.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        """)
    connection.execute(
        "INSERT INTO events (timestamp, event_type, payload) VALUES (?, ?, ?)",
        ("2024-01-01 00:00:00", "guard_triggered", json.dumps({"guard": "alpha"})),
    )
    connection.execute(
        "INSERT INTO events (timestamp, event_type, payload) VALUES (?, ?, ?)",
        ("2024-01-01 02:00:00", "api_error", json.dumps({"message": "boom"})),
    )
    connection.commit()
    connection.close()

    cutoff = datetime(2024, 1, 1, 1, 0, 0, tzinfo=UTC)
    events = load_events_since(tmp_path / "events.jsonl", cutoff)

    assert len(events) == 1
    assert events[0]["type"] == "api_error"
    assert events[0]["timestamp"] == "2024-01-01T02:00:00+00:00"


def test_load_events_since_normalizes_timestamp(tmp_path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2024-01-01T00:00:00Z", "type": "api_error"}),
            ]
        )
    )
    cutoff = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)

    events = load_events_since(events_file, cutoff)

    assert events[0]["timestamp"] == "2024-01-01T00:00:00+00:00"


def test_load_metrics_falls_back_to_db(tmp_path) -> None:
    events_db = tmp_path / "events.db"
    connection = sqlite3.connect(str(events_db))
    connection.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        """)
    metrics_payload = {
        "bot_id": "bot-1",
        "metrics": {"event_type": "cycle_metrics", "account": {"equity": 123.0}},
    }
    connection.execute(
        "INSERT INTO events (timestamp, event_type, payload) VALUES (?, ?, ?)",
        ("2024-01-01 00:00:00", "metric", json.dumps(metrics_payload)),
    )
    connection.commit()
    connection.close()

    metrics = load_metrics(tmp_path / "metrics.json")

    assert metrics["account"]["equity"] == 123.0


def test_load_unfilled_orders_count_uses_threshold(tmp_path) -> None:
    orders_db = tmp_path / "orders.db"
    connection = sqlite3.connect(str(orders_db))
    connection.executescript("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
    connection.execute(
        "INSERT INTO orders (status, created_at) VALUES (?, ?)",
        ("open", "2024-01-01T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO orders (status, created_at) VALUES (?, ?)",
        ("open", "2024-01-01T00:08:00+00:00"),
    )
    connection.execute(
        "INSERT INTO orders (status, created_at) VALUES (?, ?)",
        ("filled", "2024-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    as_of = datetime(2024, 1, 1, 0, 10, 0, tzinfo=UTC)
    count = load_unfilled_orders_count(
        orders_db,
        as_of=as_of,
        alert_seconds=300,
    )

    assert count == 1


def test_load_liveness_snapshot_missing_db(tmp_path) -> None:
    snapshot = load_liveness_snapshot(tmp_path / "missing.db")

    assert snapshot is None


def test_load_runtime_fingerprint_missing_db(tmp_path) -> None:
    runtime = load_runtime_fingerprint(tmp_path / "missing.db")

    assert runtime is None
