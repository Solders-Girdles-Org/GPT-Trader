"""Held-out input builder: provenance binding and gap refusal, fixtures only."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from gpt_trader.features.experiment.held_out import (
    build_input_payload,
    capture_bars,
    verify_input_payload,
)
from gpt_trader.features.experiment.inputs import parse_input

START = datetime(2026, 9, 3, tzinfo=UTC)


def raw_bar(hour, close=100):
    ts = START + timedelta(hours=hour)
    return {
        "start": str(int(ts.timestamp())),
        "low": str(close - 1),
        "high": str(close + 1),
        "open": str(close),
        "close": str(close),
        "volume": "5.5",
    }


def capture(hours, *, chunks=None, end_hours=6):
    chunks = chunks or [list(hours)]
    payload = {
        "schema": "coinbase-public-candles-capture/1",
        "symbol": "ETH-USD",
        "granularity": "ONE_HOUR",
        "start": START.isoformat(),
        "end": (START + timedelta(hours=end_hours)).isoformat(),
        "fetched_at": "2026-10-12T08:00:00+00:00",
        "requests": [
            {"response": {"candles": [raw_bar(hour) for hour in reversed(chunk)]}}
            for chunk in chunks
        ],
    }
    return json.dumps(payload, sort_keys=True).encode()


SETTINGS = {"fee_bps": "60", "slippage_bps": "10"}


def test_capture_digest_and_fetch_time_bind_the_input_identity():
    # Overlapping chunks and a bar at the exclusive end are tolerated, not counted.
    data = capture(range(7), chunks=[[0, 1, 2, 3], [3, 4, 5, 6]])
    payload = build_input_payload(
        data,
        scored_from=(START + timedelta(hours=3)).isoformat(),
        benchmark="mean-reversion",
        settings=SETTINGS,
    )
    digest = hashlib.sha256(data).hexdigest()
    assert f"sha256={digest}" in payload["source"]
    assert "fetched_at=2026-10-12T08:00:00+00:00" in payload["source"]
    assert [bar["ts"] for bar in payload["candles"]][0] == START.isoformat()
    assert len(payload["candles"]) == 6
    source = parse_input(payload)
    assert source.scored_index == 3 and source.benchmark == "mean-reversion"
    assert source.settings.fee_bps == 60
    altered = capture(range(7), chunks=[[0, 1, 2, 3], [3, 4, 5, 6]]).replace(b"5.5", b"5.6")
    other = build_input_payload(
        altered, scored_from=payload["scored_from"], benchmark="mean-reversion", settings=SETTINGS
    )
    assert parse_input(other).identity != source.identity


def test_gaps_and_conflicting_duplicates_are_refused():
    with pytest.raises(ValueError, match="Gap"):
        capture_bars(capture([0, 1, 3, 4, 5]))
    with pytest.raises(ValueError, match="Gap"):
        capture_bars(capture([1, 2, 3, 4, 5]))
    conflicting = json.loads(capture(range(6)))
    conflicting["requests"].append({"response": {"candles": [raw_bar(2, close=200)]}})
    with pytest.raises(ValueError, match="Conflicting"):
        capture_bars(json.dumps(conflicting).encode())
    with pytest.raises(ValueError, match="Unsupported"):
        capture_bars(
            json.dumps({**json.loads(capture(range(6))), "granularity": "ONE_DAY"}).encode()
        )


def test_verify_refuses_any_input_its_capture_does_not_rebuild():
    data = capture(range(6))
    scored_from = (START + timedelta(hours=3)).isoformat()
    payload = build_input_payload(
        data, scored_from=scored_from, benchmark="baseline", settings=SETTINGS
    )
    verify_input_payload(data, json.loads(json.dumps(payload)))
    edited_bar = json.loads(json.dumps(payload))
    edited_bar["candles"][4]["close"] = "101"
    with pytest.raises(ValueError, match="differs from its capture: candles"):
        verify_input_payload(data, edited_bar)
    relabelled = {**payload, "source": payload["source"].replace("sha256=", "sha256=0")}
    with pytest.raises(ValueError, match="source"):
        verify_input_payload(data, relabelled)
    with pytest.raises(ValueError, match="unexpected"):
        verify_input_payload(data, {**payload, "unexpected": None})
    other_capture = data.replace(b"5.5", b"5.6")
    with pytest.raises(ValueError, match="candles, source"):
        verify_input_payload(other_capture, payload)
