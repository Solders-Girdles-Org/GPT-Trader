#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.ops import formatting
except ModuleNotFoundError:  # pragma: no cover
    # Allow direct script execution (e.g. `python3 scripts/ops/liveness_check.py ...`).
    import formatting  # type: ignore

DEFAULT_EVENT_TYPES = ("heartbeat", "price_tick")
DEFAULT_MAX_AGE_SECONDS = 300
STALE_AGE_SECONDS = 999999


@dataclass(frozen=True)
class EventAge:
    event_type: str
    last_ts: str
    age_seconds: int
    event_id: int | None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check canary liveness from heartbeat/price_tick events."
    )
    parser.add_argument("--profile", default="canary", help="Profile name (default: canary)")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path("."),
        help="Repo/runtime root (default: .)",
    )
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
        help="Max age in seconds before marking RED (default: 300)",
    )
    parser.add_argument(
        "--event-type",
        action="append",
        default=None,
        help="Event type to consider (repeatable)",
    )
    parser.add_argument(
        "--min-event-id",
        type=int,
        default=None,
        help="Minimum event id required for liveness",
    )
    return parser.parse_args()


def _fetch_latest_event(
    connection: sqlite3.Connection, event_type: str
) -> tuple[int | None, str | None]:
    cursor = connection.execute(
        """
        SELECT id, timestamp
        FROM events
        WHERE event_type = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (event_type,),
    )
    row = cursor.fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def check_liveness(
    events_db: Path,
    event_types: Iterable[str],
    max_age_seconds: int,
    *,
    min_event_id: int | None = None,
    now: datetime | None = None,
) -> tuple[list[EventAge], bool]:
    if not events_db.exists():
        raise FileNotFoundError(f"events.db not found: {events_db}")

    connection = sqlite3.connect(str(events_db))
    try:
        rows: list[EventAge] = []
        now_ts = now or datetime.now(UTC)
        for event_type in event_types:
            event_id, raw_ts = _fetch_latest_event(connection, event_type)
            parsed = formatting.parse_timestamp(raw_ts)
            if parsed is None:
                rows.append(
                    EventAge(
                        event_type=event_type,
                        last_ts=formatting.format_timestamp(None),
                        age_seconds=STALE_AGE_SECONDS,
                        event_id=None,
                    )
                )
                continue
            age_seconds = int((now_ts - parsed).total_seconds())
            rows.append(
                EventAge(
                    event_type=event_type,
                    last_ts=formatting.format_timestamp(parsed),
                    age_seconds=age_seconds,
                    event_id=event_id,
                )
            )
        is_green = any(
            row.age_seconds <= max_age_seconds
            and (min_event_id is None or (row.event_id or 0) > min_event_id)
            for row in rows
        )
        return rows, is_green
    finally:
        connection.close()


def main() -> int:
    args = _parse_args()
    event_types = args.event_type or list(DEFAULT_EVENT_TYPES)

    events_db = args.runtime_root / "runtime_data" / args.profile / "events.db"
    print(formatting.format_status_line("events_db", events_db))

    try:
        rows, is_green = check_liveness(
            events_db,
            event_types,
            args.max_age_seconds,
            min_event_id=args.min_event_id,
        )
    except (sqlite3.Error, FileNotFoundError) as exc:
        print(formatting.format_status_line("liveness_status", "ERROR"))
        print(formatting.format_status_line("error", exc))
        return 2

    for row in rows:
        print(
            f"{row.event_type} "
            f"{formatting.format_status_line('last_ts', row.last_ts)} "
            f"{formatting.format_status_line('age_seconds', row.age_seconds)} "
            f"{formatting.format_status_line('event_id', row.event_id)}"
        )

    status = "GREEN" if is_green else "RED"
    print(formatting.format_status_line("liveness_status", status))
    return 0 if is_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
