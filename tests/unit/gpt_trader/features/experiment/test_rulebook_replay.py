"""Atomic activation, tamper rejection and historical mechanics regression."""

import json
import logging
import sqlite3
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.core.fill_accounting import semantic_checksum
from gpt_trader.features.experiment.engine import advance, run_experiment
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.ledger import (
    ExperimentIntegrityError,
    encode,
    initial_state,
    inspect_run,
)
from gpt_trader.features.experiment.rulebook import STARTING_RULEBOOK
from gpt_trader.features.experiment.valuation import end_valuation


def scheduled(payload):
    return {
        **payload,
        "benchmark": "ai-rulebook",
        "rulebooks": [
            {
                "effective_from": payload["candles"][0]["ts"],
                "rules": {**STARTING_RULEBOOK.to_dict(), "entry": "hold"},
            },
            {
                "effective_from": payload["candles"][3]["ts"],
                "rules": {**STARTING_RULEBOOK.to_dict(), "entry": "cash", "version": 2},
            },
        ],
    }


def test_activation_resume_and_replay(experiment_payload, tmp_path):
    parsed = parse_input(scheduled(experiment_payload))
    uninterrupted = run_experiment(tmp_path / "full", parsed)
    run_experiment(tmp_path / "split", parsed, max_bars=4)
    resumed = run_experiment(tmp_path / "split")
    assert uninterrupted == resumed
    assert Decimal(resumed["account"]["quantity"]) == 0
    with sqlite3.connect(tmp_path / "split" / "experiment.sqlite3") as connection:
        # Find the journal through its public SQLite schema.
        rows = [
            json.loads(row[0])
            for row in connection.execute("SELECT payload FROM events ORDER BY sequence")
        ]
    assert rows[0]["rulebook_activation"] == parsed.payload["rulebooks"][0]
    assert rows[3]["rulebook_activation"] == parsed.payload["rulebooks"][1]
    assert rows[2]["rulebook_activation"] is None
    assert rows[3]["state"]["position"]["stop"] == rows[2]["state"]["position"]["stop"]
    assert rows[3]["fills"] == []
    assert rows[4]["fills"][0]["side"] == "sell"
    assert inspect_run(tmp_path / "split") == resumed
    changed = deepcopy(parsed.payload)
    changed["rulebooks"][1]["rules"]["stop_fraction"] = "0.05"
    with pytest.raises(ExperimentIntegrityError, match="changed"):
        run_experiment(tmp_path / "split", parse_input(changed))


@pytest.mark.parametrize("change", ["order", "version", "time", "missing-first"])
def test_reject_bad_schedules(experiment_payload, change):
    payload = scheduled(experiment_payload)
    if change == "order":
        payload["rulebooks"].reverse()
    elif change == "version":
        payload["rulebooks"][1]["rules"]["version"] = 3
    elif change == "time":
        payload["rulebooks"][1]["effective_from"] = "2026-01-01T03:30:00+00:00"
    else:
        payload["rulebooks"][0]["effective_from"] = payload["candles"][1]["ts"]
    with pytest.raises(ValueError):
        parse_input(payload)


def test_rulebook_journal_tamper_rejected(experiment_payload, tmp_path):
    run_experiment(tmp_path, parse_input(scheduled(experiment_payload)), max_bars=1)
    with sqlite3.connect(tmp_path / "experiment.sqlite3") as connection:
        connection.execute("DROP TRIGGER events_update")
        event = json.loads(connection.execute("SELECT payload FROM events").fetchone()[0])
        event["rulebook_activation"]["rules"]["entry"] = "cash"
        connection.execute(
            "UPDATE events SET payload=?, checksum=?", (encode(event), semantic_checksum(event))
        )
    with pytest.raises(ExperimentIntegrityError, match="replay"):
        inspect_run(tmp_path)


def test_historical_hold_gaps_are_measurements(caplog):
    # Exact OHLCV rows 336:480 from retained round-one BTC baseline-base input.
    # Source capture digest and fetch time are preserved in the fixture. This
    # test writes no journal/evidence: a pure transition fold checks mechanics.
    path = Path(__file__).resolve().parents[4] / "fixtures/experiment/round-one-btc-rising.json"
    payload = json.loads(path.read_text())
    gaps = {}
    for costs, fee, slip, tight in (("base", "10", "5", "0.04"), ("stress", "50", "20", "0.03")):
        for variant, stop in (("at-cap", tight), ("wide-stop", "0.15")):
            raw = {
                **payload,
                "benchmark": "ai-rulebook",
                "settings": {
                    **payload["settings"],
                    "fee_bps": fee,
                    "slippage_bps": slip,
                },
                "rulebooks": [
                    {
                        "effective_from": payload["scored_from"],
                        "rules": {
                            **STARTING_RULEBOOK.to_dict(),
                            "entry": "hold",
                            "stop_fraction": stop,
                            "target_fraction": "100",
                            "expiry_hours": 48,
                        },
                    }
                ],
            }
            parsed = parse_input(raw)
            state, events = initial_state(parsed), []
            for index in range(len(parsed.candles)):
                event = advance(parsed, index, state)
                events.append(event)
                state = event["state"]
            first_buy = next(fill for e in events for fill in e["fills"] if fill["side"] == "buy")
            notional = Decimal(first_buy["quantity"]) * Decimal(first_buy["price"])
            assert notional <= Decimal(200)
            assert (
                (notional > Decimal("199.99")) if variant == "at-cap" else (notional < Decimal(70))
            )
            valuation = end_valuation(parsed, events, state)
            hold = Decimal(valuation["comparators"]["hold_the_coin"]["net_pnl"])
            assert hold > 0  # Rising historical stretch, not a performance claim.
            gaps[f"{costs}/{variant}"] = str(Decimal(valuation["net_pnl"]) - hold)
    # No gap threshold: the comparator has no stop-risk sizing or expiry and
    # enters one bar earlier. Step 3 must disclose these structural differences.
    with caplog.at_level(logging.INFO):
        logging.getLogger(__name__).info(
            "Historical hold mechanics gaps: %s", json.dumps(gaps, sort_keys=True)
        )
    assert "base/at-cap" in caplog.text and "stress/wide-stop" in caplog.text
    assert len(gaps) == 4
