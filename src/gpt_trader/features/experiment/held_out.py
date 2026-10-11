"""Build experiment inputs from a retained raw candle capture, refusing gaps.

Pure: the capture bytes are the evidence, and the input's source label binds
their SHA-256 and fetch time into the experiment identity. Bars outside the
requested window are dropped; a missing, duplicate-conflicting or extra bar
inside it refuses the build rather than filling anything.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.inputs import parse_input

CAPTURE_SCHEMA = "coinbase-public-candles-capture/1"


def _bar_time(raw: Any) -> datetime:
    if isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit()):
        return datetime.fromtimestamp(int(raw), tz=UTC)
    return aware_time(str(raw).replace("Z", "+00:00"))


def capture_bars(capture_bytes: bytes) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return capture metadata and the contiguous closed hourly bars it holds."""
    capture = json.loads(capture_bytes)
    if capture.get("schema") != CAPTURE_SCHEMA or capture.get("granularity") != "ONE_HOUR":
        raise ValueError("Unsupported candle capture")
    start, end = aware_time(capture["start"]), aware_time(capture["end"])
    bars: dict[datetime, dict[str, str]] = {}
    for request in capture["requests"]:
        response = request["response"]
        if not isinstance(response, dict) or not isinstance(response.get("candles"), list):
            raise ValueError("Capture holds a malformed candle response")
        for raw in response["candles"]:
            ts = _bar_time(raw["start"])
            if not start <= ts < end:
                continue
            bar = {"ts": ts.isoformat()} | {
                name: str(raw[name]) for name in ("open", "high", "low", "close", "volume")
            }
            if bars.setdefault(ts, bar) != bar:
                raise ValueError(f"Conflicting duplicate bar at {ts.isoformat()}")
    expected = int((end - start) / timedelta(hours=1))
    ordered = [bars[ts] for ts in sorted(bars)]
    if len(ordered) != expected or (ordered and aware_time(ordered[0]["ts"]) != start):
        raise ValueError(f"Gap in capture: {len(ordered)} of {expected} hourly bars present")
    return capture, ordered


def build_input_payload(
    capture_bytes: bytes,
    *,
    scored_from: str,
    benchmark: str,
    settings: dict[str, str],
) -> dict[str, Any]:
    """One validated experiment input per rule and cost case, from one capture."""
    capture, bars = capture_bars(capture_bytes)
    digest = hashlib.sha256(capture_bytes).hexdigest()
    payload = {
        "schema_version": 1,
        "source": (
            f"coinbase public hourly candles; raw capture sha256={digest}; "
            f"fetched_at={capture['fetched_at']}"
        ),
        "symbol": capture["symbol"],
        "recorded_at": capture["end"],
        "candles": bars,
        "scored_from": scored_from,
        "benchmark": benchmark,
        "settings": settings,
    }
    parse_input(payload)
    return payload
