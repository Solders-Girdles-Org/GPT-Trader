"""Held-out inputs keep the public responses they came from (fixtures only, no fetch)."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.provenance import build_held_out_input

START = datetime(2026, 9, 3, tzinfo=UTC)
SCORED = START + timedelta(hours=53)
CUTOFF = START + timedelta(hours=60)
FETCHED = CUTOFF + timedelta(minutes=7)


def bar(hour, close="100"):
    return {
        "start": str(int((START + timedelta(hours=hour)).timestamp())),
        "low": "99.5",
        "high": "101.25",
        "open": "100.00",
        "close": close,
        "volume": "12.5",
    }


def response(hours, *, minute=5):
    # Coinbase returns newest first; the builder must not depend on order.
    return {
        "request": {"product_id": "ETH-USD", "granularity": "ONE_HOUR"},
        "fetched_at": (CUTOFF + timedelta(minutes=minute)).isoformat(),
        "body": {"candles": [bar(hour) for hour in reversed(hours)]},
    }


@pytest.fixture
def responses():
    # Chunks overlap at hour 30, include a pre-window bar (-1) and the
    # still-open bar starting at the cutoff (60); both are excluded.
    return [response(range(-1, 31)), response(range(30, 61), minute=6)]


def build(responses, **overrides):
    arguments = {
        "symbol": "ETH-USD",
        "responses": responses,
        "window_start": START,
        "scored_from": SCORED,
        "cutoff": CUTOFF,
        "fetched_at": FETCHED,
    }
    return build_held_out_input(**{**arguments, **overrides})


def test_builder_retains_raw_responses_digests_and_fetch_time(responses):
    payload = build(responses)
    source = parse_input(payload)
    assert len(source.candles) == 60
    assert source.candles[0].ts == START and source.candles[-1].ts == CUTOFF - timedelta(hours=1)
    assert source.scored_index == 53
    assert payload["recorded_at"] == CUTOFF.isoformat()
    assert payload["candles"][0] == {
        "ts": START.isoformat(),
        "open": "100.00",
        "high": "101.25",
        "low": "99.5",
        "close": "100",
        "volume": "12.5",
    }
    provenance = source.payload["provenance"]
    assert provenance["fetched_at"] == FETCHED.isoformat()
    assert provenance["granularity"] == "ONE_HOUR"
    for kept, original in zip(provenance["responses"], responses, strict=True):
        assert kept["body"] == original["body"]
        assert kept["fetched_at"] == original["fetched_at"]
        canonical = json.dumps(original["body"], sort_keys=True, separators=(",", ":"))
        assert kept["sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert source.payload["source"].startswith("coinbase:")


def test_builder_does_not_alias_callers_responses(responses):
    payload = build(responses)
    responses[0]["body"]["candles"][0]["close"] = "1"
    parse_input(payload)


def test_edited_bar_breaks_the_fingerprint(responses):
    payload = build(responses)
    payload["candles"][10]["close"] = "100.5"
    with pytest.raises(ValueError, match="provenance"):
        parse_input(payload)


def test_edited_response_breaks_its_digest(responses):
    payload = build(responses)
    payload["provenance"]["responses"][1]["body"]["candles"][0]["volume"] = "1"
    with pytest.raises(ValueError, match="SHA-256"):
        parse_input(payload)


def test_response_fetched_before_cutoff_is_refused(responses):
    payload = build(responses)
    early = deepcopy(payload)
    early["provenance"]["responses"][0]["fetched_at"] = CUTOFF.replace(hour=0).isoformat()
    with pytest.raises(ValueError, match="fetched"):
        parse_input(early)
    with pytest.raises(ValueError, match="fetched"):
        build(responses, fetched_at=CUTOFF - timedelta(seconds=1))


def test_gaps_are_refused_not_filled(responses):
    responses[1]["body"]["candles"] = [
        candle for candle in responses[1]["body"]["candles"] if candle["start"] != bar(40)["start"]
    ]
    with pytest.raises(ValueError, match="gap"):
        build(responses)


def test_conflicting_duplicate_bars_are_refused(responses):
    responses[1]["body"]["candles"][-1] = bar(30, close="100.01")
    with pytest.raises(ValueError, match="conflicting"):
        build(responses)
