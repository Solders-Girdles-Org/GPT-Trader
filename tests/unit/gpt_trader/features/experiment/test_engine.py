"""Behavioral checks for causal fills, costs, sizing and recorded accounting."""

import json
import sqlite3
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.engine import advance, run_experiment
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.ledger import (
    ExperimentIntegrityError,
    initial_state,
    reconcile,
)


def events_at(root):
    with sqlite3.connect(root / "experiment.sqlite3") as connection:
        return [
            json.loads(row[0])
            for row in connection.execute("SELECT payload FROM events ORDER BY sequence")
        ]


def test_natural_crossover_fills_next_bar_and_settles_net_account(experiment_input, tmp_path):
    report = run_experiment(tmp_path / "natural", experiment_input)
    events = events_at(tmp_path / "natural")
    signal = events[54]
    assert signal["proposal"] is not None
    assert signal["fills"] == []
    assert events[55]["fills"][0]["side"] == "buy"
    assert events[57]["fills"][0]["side"] == "sell"
    assert report["complete"]
    assert report["account"]["reconciled"]
    assert report["account"]["fill_count"] == 2
    assert Decimal(report["account"]["quantity"]) == 0
    assert Decimal(report["account"]["fees"]) > 0
    assert Decimal(report["account"]["cash"]) - experiment_input.settings.initial_cash == Decimal(
        report["account"]["realized_net"]
    )
    assert "no AI decisions" in report["evidence_class"]


def test_future_prices_cannot_change_prior_decision_or_finances(experiment_payload):
    changed = deepcopy(experiment_payload)
    changed["candles"][55].update(open="900", high="901", low="899", close="900")
    states = []
    for source in (parse_input(experiment_payload), parse_input(changed)):
        state = initial_state(source)
        for index in range(55):
            event = advance(source, index, state)
            state = event["state"]
        states.append(event)
    assert states[0] == states[1]


def test_stop_gap_uses_worse_open_and_charges_both_fees(experiment_payload):
    experiment_payload["candles"][56].update(open="90", high="91", low="89", close="90")
    source = parse_input(experiment_payload)
    state = initial_state(source)
    events = []
    for index in range(57):
        event = advance(source, index, state)
        events.append(event)
        state = event["state"]
    entry, exit_fill = events[55]["fills"][0], events[56]["fills"][0]
    quantity = Decimal(entry["quantity"])
    assert Decimal(entry["price"]) == Decimal("110.055")
    assert Decimal(exit_fill["price"]) == Decimal("89.955")
    fees = Decimal(entry["fee"]) + Decimal(exit_fill["fee"])
    expected = quantity * (Decimal(exit_fill["price"]) - Decimal(entry["price"])) - fees
    assert Decimal(state["realized_net"]) == expected
    assert Decimal(state["fees"]) == fees
    assert quantity * Decimal(entry["price"]) <= Decimal("200")
    assert quantity % source.settings.quantity_increment == 0
    assert reconcile(source, events)["reconciled"]


def test_entry_outside_plan_is_refused_without_cash_change(experiment_payload):
    experiment_payload["candles"][55].update(open="200", high="201", low="199", close="200")
    source = parse_input(experiment_payload)
    state = initial_state(source)
    for index in range(56):
        event = advance(source, index, state)
        state = event["state"]
    assert event["fills"] == []
    assert any("entry refused" in action for action in event["actions"])
    assert Decimal(state["cash"]) == source.settings.initial_cash


def test_dataset_end_keeps_open_inventory_unrealized(experiment_payload, tmp_path):
    experiment_payload["candles"] = experiment_payload["candles"][:56]
    experiment_payload["recorded_at"] = (
        aware_time(experiment_payload["candles"][-1]["ts"]) + timedelta(hours=1)
    ).isoformat()
    report = run_experiment(tmp_path / "open", parse_input(experiment_payload))
    assert report["complete"]
    assert report["position"] is not None
    assert report["account"]["fill_count"] == 1
    assert Decimal(report["account"]["realized_net"]) == 0
    assert Decimal(report["account"]["unrealized_net"]) < 0


def test_compensating_cost_basis_and_realized_errors_are_rejected(experiment_input):
    event = advance(experiment_input, 0, initial_state(experiment_input))
    event["state"].update(cost_basis="1", realized_net="1")
    with pytest.raises(ExperimentIntegrityError, match="independent reconciliation"):
        reconcile(experiment_input, [event])


def test_expiry_compares_instants_across_utc_offsets(experiment_payload):
    experiment_payload["candles"][57].update(open="111", high="112", low="110", close="111")
    source = parse_input(experiment_payload)
    state = initial_state(source)
    for index in range(56):
        state = advance(source, index, state)["state"]
    # 03:00 at -07:00 is 10:00 UTC. The previous bar ends at 09:00 UTC.
    state["position"]["expires_at"] = "2026-01-03T03:00:00-07:00"
    before_expiry = advance(source, 56, state)
    assert before_expiry["fills"] == []
    at_expiry = advance(source, 57, before_expiry["state"])
    assert at_expiry["fills"][0]["side"] == "sell"
    assert "position settled: expiry" in at_expiry["actions"]


def test_duplicate_identified_fill_is_refused(experiment_input):
    events = []
    state = initial_state(experiment_input)
    for index in range(56):
        event = advance(experiment_input, index, state)
        events.append(event)
        state = event["state"]
    events[-1]["fills"].append(deepcopy(events[-1]["fills"][0]))
    with pytest.raises(ExperimentIntegrityError, match="Duplicate simulated fill"):
        reconcile(experiment_input, events)
