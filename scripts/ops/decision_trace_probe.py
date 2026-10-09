#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sqlite3
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from gpt_trader.app.config import BotConfig
from gpt_trader.app.container import (
    clear_application_container,
    create_application_container,
    set_application_container,
)
from gpt_trader.config.types import Profile
from gpt_trader.core import OrderSide

PROBE_REASON = "ops: decision_id probe (quantity_override=0)"
EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_PROBE_ERROR = 3
EXIT_EVENT_STORE_ERROR = 4
SENSITIVE_FIELD_RE = re.compile(
    r"(?P<quote>['\"]?)"
    r"\b(?P<label>api[_-]?key|api[_-]?secret|client[_-]?secret|private[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer[_-]?token|id[_-]?token|"
    r"secret|token|password|passwd)"
    r"(?P=quote)"
    r"(?P<separator>\s*[=:]\s*|\s+)"
    r"(?P<value>'[^']*'|\"[^\"]*\"|\S+)",
    re.IGNORECASE,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit a decision trace with decision_id via dry-run guard stack."
    )
    parser.add_argument("--profile", default="canary", help="Profile name (default: canary)")
    parser.add_argument("--symbol", default="BTC-USD", help="Symbol to use (default: BTC-USD)")
    parser.add_argument(
        "--side",
        default="buy",
        choices=("buy", "sell"),
        help="Order side (default: buy)",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path("."),
        help="Repo/runtime root (default: .)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output for automation parsing",
    )
    return parser.parse_args()


def _read_latest_trace(events_db: Path) -> tuple[int | None, str | None]:
    if not events_db.exists():
        return None, None
    connection = sqlite3.connect(str(events_db))
    try:
        cursor = connection.execute(
            """
            SELECT id, timestamp
            FROM events
            WHERE event_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            ("order_decision_trace",),
        )
        row = cursor.fetchone()
        if row is None:
            return None, None
        return row[0], row[1]
    finally:
        connection.close()


def _normalize_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    except ValueError:
        return value


def _format_timestamp(value: str | None) -> str:
    normalized = _normalize_timestamp(value)
    return normalized or "-"


def _build_config(profile_name: str, runtime_root: Path) -> BotConfig:
    profile = Profile(profile_name.lower())
    config = BotConfig.from_profile(profile=profile, dry_run=True, mock_broker=True)
    config.runtime_root = str(runtime_root)
    config.dry_run = True
    config.mock_broker = True
    return config


async def _run_probe(
    *,
    symbol: str,
    side: OrderSide,
    config: BotConfig,
) -> tuple[str, str | None, str | None]:
    container = create_application_container(config)
    set_application_container(container)
    bot = container.create_bot()
    try:
        result = await bot.engine.submit_order(
            symbol=symbol,
            side=side,
            price=Decimal("1"),
            equity=Decimal("1000"),
            quantity_override=Decimal("0"),
            reason=PROBE_REASON,
        )
        decision_id = None
        if result.decision_trace is not None:
            decision_id = result.decision_trace.decision_id
        return result.status.value, decision_id, result.reason
    finally:
        await bot.shutdown()
        clear_application_container()


def _redact_sensitive_text(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        label = match.group("label")
        separator = match.group("separator")
        if separator.strip() in {"=", ":"}:
            return f"{label}=[REDACTED]"
        return f"{label} [REDACTED]"

    return SENSITIVE_FIELD_RE.sub(_replace, text)


def _summarize_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    return _redact_sensitive_text(" ".join(reason.split()))


def _build_json_payload(
    *,
    status: str,
    decision_id: str | None,
    reason: str | None,
    latest_id: int | None,
    latest_ts: str | None,
) -> dict[str, object]:
    normalized_ts = _normalize_timestamp(latest_ts)
    found = latest_id is not None or normalized_ts is not None
    return {
        "status": status,
        "decision_id": decision_id,
        "blocked_reason": _summarize_reason(reason),
        "latest_trace": {
            "found": found,
            "id": latest_id,
            "timestamp": normalized_ts,
        },
    }


def _print_json_payload(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _emit_error(message: str, *, json_output: bool, exit_code: int) -> int:
    sanitized_message = _redact_sensitive_text(" ".join(message.split()))
    if json_output:
        _print_json_payload({"status": "error", "error": sanitized_message})
    else:
        print(f"error={sanitized_message}")
    return exit_code


def main() -> int:
    args = _parse_args()

    logging.getLogger("gpt_trader").setLevel(logging.WARNING)

    side = OrderSide.BUY if args.side == "buy" else OrderSide.SELL
    try:
        config = _build_config(args.profile, args.runtime_root)
    except ValueError:
        valid_profiles = ", ".join(profile.value for profile in Profile)
        return _emit_error(
            f"invalid profile: {args.profile} (valid: {valid_profiles})",
            json_output=args.json,
            exit_code=EXIT_INVALID_INPUT,
        )

    events_db = Path(config.runtime_root) / "runtime_data" / args.profile / "events.db"

    try:
        status, decision_id, reason = asyncio.run(
            _run_probe(
                symbol=args.symbol,
                side=side,
                config=config,
            )
        )
        latest_id, latest_ts = _read_latest_trace(events_db)
    except sqlite3.Error as exc:
        return _emit_error(
            f"failed to read events.db: {exc}",
            json_output=args.json,
            exit_code=EXIT_EVENT_STORE_ERROR,
        )
    except Exception as exc:
        return _emit_error(
            f"probe failed: {exc}",
            json_output=args.json,
            exit_code=EXIT_PROBE_ERROR,
        )

    if args.json:
        payload = _build_json_payload(
            status=status,
            decision_id=decision_id,
            reason=reason,
            latest_id=latest_id,
            latest_ts=latest_ts,
        )
        _print_json_payload(payload)
        return EXIT_OK

    print(f"status={status}")
    print(f"decision_id={decision_id or '-'}")
    if reason:
        print(f"blocked_reason={_summarize_reason(reason)}")
    if latest_id is not None:
        print(f"latest_trace_id={latest_id}")
    print(f"latest_trace_ts={_format_timestamp(latest_ts)}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
