#!/usr/bin/env python3
"""Report readiness-window timing from runtime event storage."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_EVENT_TYPES = ("api_error", "guard_triggered")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Show last api_error/guard_triggered timestamp and UTC time remaining until an N-hour "
            "readiness window clears, based on runtime_data/<profile>/events.db."
        )
    )
    parser.add_argument("--profile", default="canary", help="Runtime profile under runtime_data/.")
    parser.add_argument("--hours", type=float, default=24.0, help="Window size in hours.")
    parser.add_argument(
        "--runtime-root",
        default=".",
        help="Project root containing runtime_data/ (defaults to CWD).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Optional explicit path to events.db (overrides --profile/--runtime-root).",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="SQLite read timeout seconds.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-event-type timestamps in addition to the combined window.",
    )
    return parser.parse_args(argv)


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _parse_timestamp(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_duration(seconds: float) -> str:
    remaining = int(max(0, round(seconds)))
    days, remainder = divmod(remaining, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _open_events_db(path: Path, timeout: float) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=timeout)


def _fetch_last_timestamp(
    connection: sqlite3.Connection,
    event_types: Sequence[str],
) -> tuple[datetime | None, str | None]:
    placeholders = ",".join("?" for _ in event_types)
    row = connection.execute(
        f"""
        SELECT timestamp, event_type
        FROM events
        WHERE event_type IN ({placeholders})
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
        """,
        list(event_types),
    ).fetchone()
    if not row or row[0] is None or row[1] is None:
        return None, None
    return _parse_timestamp(str(row[0])), str(row[1])


def _fetch_last_timestamp_for_type(
    connection: sqlite3.Connection,
    event_type: str,
) -> datetime | None:
    row = connection.execute(
        """
        SELECT timestamp
        FROM events
        WHERE event_type = ?
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
        """,
        (event_type,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return _parse_timestamp(str(row[0]))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.db:
        db_path = Path(args.db).expanduser()
    else:
        db_path = (
            Path(args.runtime_root).expanduser() / "runtime_data" / str(args.profile) / "events.db"
        )

    if not db_path.exists():
        print(f"events.db not found: {db_path}", file=sys.stderr)
        return 2

    try:
        with _open_events_db(db_path, timeout=args.timeout) as connection:
            last_event_at, last_event_type = _fetch_last_timestamp(
                connection,
                DEFAULT_EVENT_TYPES,
            )

            if args.verbose:
                for event_type in DEFAULT_EVENT_TYPES:
                    last_for_type = _fetch_last_timestamp_for_type(connection, event_type)
                    label = _format_utc(last_for_type) if last_for_type else "none"
                    print(f"last_{event_type}: {label}")

    except sqlite3.Error as exc:
        print(f"Failed to read {db_path}: {exc}", file=sys.stderr)
        return 3

    print(f"profile: {args.profile}")
    print(f"events_db: {db_path}")
    print(f"window_hours: {args.hours:g}")

    if last_event_at is None:
        print("last_api_error_or_guard_triggered: none")
        print("window_status: cleared (no events found)")
        return 0

    clears_at = last_event_at + timedelta(hours=float(args.hours))
    now = datetime.now(UTC)
    remaining_seconds = (clears_at - now).total_seconds()
    status = "cleared" if remaining_seconds <= 0 else "pending"

    label_type = last_event_type or "api_error/guard_triggered"
    print(f"last_api_error_or_guard_triggered: {_format_utc(last_event_at)} ({label_type})")
    print(f"window_clears_at_utc: {_format_utc(clears_at)}")
    print(f"time_until_clear: {_format_duration(remaining_seconds)} ({status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
