"""Held-out experiment inputs that carry the public responses they were built from.

A held-out input embeds every decoded Coinbase candles response body exactly
as the client returned it, the SHA-256 of that body's canonical JSON encoding
and the time it was fetched. The parser re-derives the bars from the retained
bodies, so an edited bar or body cannot pass as the original data. Bars are
taken only from ``[window_start, cutoff)``; a missing hour is refused, never
filled. Acquisition lives with the recorder; this module performs no I/O.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from gpt_trader.core.fill_accounting import aware_time

COINBASE_HOURLY_SOURCE = "coinbase:public-market-candles:ONE_HOUR"
_FIELDS = ("open", "high", "low", "close", "volume")


def response_sha256(body: Any) -> str:
    """SHA-256 of the canonical JSON encoding of one decoded response body."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _bars(bodies: list[Any], start: datetime, end: datetime) -> list[dict[str, str]]:
    by_hour: dict[datetime, dict[str, str]] = {}
    for body in bodies:
        if not isinstance(body, dict) or not isinstance(body.get("candles"), list):
            raise ValueError("Candles response body has no candles list")
        for raw in body["candles"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("start"), str):
                raise ValueError("Candle without a unix-seconds start")
            if not raw["start"].isdigit() or any(
                not isinstance(raw.get(name), str) for name in _FIELDS
            ):
                raise ValueError("Candle values must be the venue's decimal strings")
            ts = datetime.fromtimestamp(int(raw["start"]), tz=UTC)
            if not start <= ts < end:
                continue
            row = {"ts": ts.isoformat(), **{name: raw[name] for name in _FIELDS}}
            if by_hour.setdefault(ts, row) != row:
                raise ValueError(f"Responses carry conflicting bars for {ts.isoformat()}")
    return [by_hour[ts] for ts in sorted(by_hour)]


def verify_provenance(payload: dict[str, Any]) -> None:
    """Refuse an input whose bars are not exactly those in its retained responses."""
    provenance = payload["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {
        "granularity",
        "fetched_at",
        "responses",
    }:
        raise ValueError("provenance requires granularity, fetched_at and responses")
    if provenance["granularity"] != "ONE_HOUR":
        raise ValueError("provenance must describe hourly candles")
    responses = provenance["responses"]
    if not isinstance(responses, list) or not responses:
        raise ValueError("provenance requires the retained responses")
    recorded_at = aware_time(payload["recorded_at"])
    if aware_time(provenance["fetched_at"]) < recorded_at:
        raise ValueError("Input was fetched before its cutoff")
    for item in responses:
        if not isinstance(item, dict) or set(item) != {"request", "fetched_at", "body", "sha256"}:
            raise ValueError("Each retained response needs request, fetched_at, body, sha256")
        if aware_time(item["fetched_at"]) < recorded_at:
            raise ValueError("A response was fetched before the cutoff; its last bar was open")
        if response_sha256(item["body"]) != item["sha256"]:
            raise ValueError("Retained response does not match its SHA-256")
    first = aware_time(payload["candles"][0]["ts"])
    derived = _bars([item["body"] for item in responses], first, recorded_at)
    if derived != payload["candles"]:
        raise ValueError("Bars differ from the retained provenance responses")


def build_held_out_input(
    *,
    symbol: str,
    responses: list[dict[str, Any]],
    window_start: datetime,
    scored_from: datetime,
    cutoff: datetime,
    fetched_at: datetime,
    source: str = COINBASE_HOURLY_SOURCE,
) -> dict[str, Any]:
    """Turn retained public hourly responses into one experiment input payload.

    ``responses`` items are ``{"request", "fetched_at", "body"}`` as captured by
    the recorder. The payload carries no benchmark or settings; each case adds
    its own, so every case shares the same fingerprinted market data.
    """
    if fetched_at < cutoff:
        raise ValueError("Held-out data must be fetched after the cutoff has closed")
    retained = [
        {
            "request": deepcopy(item["request"]),
            "fetched_at": item["fetched_at"],
            "body": deepcopy(item["body"]),
            "sha256": response_sha256(item["body"]),
        }
        for item in responses
    ]
    bars = _bars([item["body"] for item in retained], window_start, cutoff)
    expected = [
        (window_start + timedelta(hours=hour)).isoformat()
        for hour in range(int((cutoff - window_start) / timedelta(hours=1)))
    ]
    if [row["ts"] for row in bars] != expected:
        missing = sorted(set(expected) - {row["ts"] for row in bars})
        raise ValueError(
            f"{symbol} has a gap of {len(missing)} hourly bars (first {missing[:1]}); "
            "gaps are refused, never filled"
        )
    return {
        "schema_version": 1,
        "source": source,
        "symbol": symbol,
        "recorded_at": cutoff.isoformat(),
        "scored_from": scored_from.isoformat(),
        "candles": bars,
        "provenance": {
            "granularity": "ONE_HOUR",
            "fetched_at": fetched_at.isoformat(),
            "responses": retained,
        },
    }
