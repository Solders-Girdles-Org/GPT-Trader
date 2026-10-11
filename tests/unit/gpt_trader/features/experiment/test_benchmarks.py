"""Benchmark selection, identity binding and warm-up suppression (#1315 phase A)."""

import json
import sqlite3
from copy import deepcopy

import pytest

from gpt_trader.features.experiment.engine import advance, run_experiment
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.ledger import ExperimentIntegrityError, initial_state


class RecordingProposer:
    """Stands in for a composed rule; records the history each decision sees."""

    windows: list[int] = []

    def propose(self, snapshot):
        RecordingProposer.windows.append(len(snapshot.series[0].candles))
        return []


@pytest.fixture
def recording_catalog():
    RecordingProposer.windows = []
    return {"mean-reversion": RecordingProposer}


def replay(source, proposers=None):
    state = initial_state(source)
    events = []
    for index in range(len(source.candles)):
        event = advance(source, index, state, proposers)
        events.append(event)
        state = event["state"]
    return events


def test_benchmark_defaults_to_baseline_and_is_part_of_identity(experiment_payload):
    default = parse_input(experiment_payload)
    assert default.benchmark == "baseline"
    assert default.payload["benchmark"] == "baseline"
    explicit = parse_input({**experiment_payload, "benchmark": "baseline"})
    assert explicit.identity == default.identity
    selected = parse_input({**experiment_payload, "benchmark": "mean-reversion"})
    assert selected.benchmark == "mean-reversion"
    assert selected.identity != default.identity


@pytest.mark.parametrize("benchmark", ["regime-switcher", "", 1, None])
def test_unknown_benchmark_is_rejected(experiment_payload, benchmark):
    experiment_payload["benchmark"] = benchmark
    with pytest.raises(ValueError, match="benchmark"):
        parse_input(experiment_payload)


def test_changed_benchmark_cannot_resume_bound_run(experiment_payload, tmp_path):
    root = tmp_path / "bound"
    run_experiment(root, parse_input(experiment_payload), max_bars=1)
    switched = parse_input({**experiment_payload, "benchmark": "mean-reversion"})
    with pytest.raises(ExperimentIntegrityError, match="changed"):
        run_experiment(root, switched)


def test_selected_rule_sees_only_its_declared_history(experiment_payload, recording_catalog):
    source = parse_input({**experiment_payload, "benchmark": "mean-reversion"})
    events = replay(source, recording_catalog)
    assert RecordingProposer.windows == [min(index + 1, 20) for index in range(60)]
    assert [event["decision"] for event in events[18:20]] == [
        "collecting benchmark history",
        "no mean-reversion signal",
    ]


def test_uncomposed_rule_fails_closed(experiment_payload):
    source = parse_input({**experiment_payload, "benchmark": "mean-reversion"})
    with pytest.raises(ValueError, match="mean-reversion"):
        advance(source, 0, initial_state(source))


def test_warm_up_bars_are_history_only(experiment_payload, recording_catalog):
    payload = {
        **experiment_payload,
        "benchmark": "mean-reversion",
        "scored_from": experiment_payload["candles"][40]["ts"],
    }
    source = parse_input(payload)
    assert source.scored_index == 40
    events = replay(source, recording_catalog)
    for event in events[:40]:
        assert event["decision"] == "warm-up: indicator history only"
        assert event["proposal"] is None
        assert event["fills"] == [] and event["actions"] == []
        assert event["state"] == initial_state(source)
    # The first decision is taken at the first scored close, with full history.
    assert RecordingProposer.windows == [20] * 20


def test_signal_during_warm_up_never_trades(experiment_payload, tmp_path):
    # Bar 54's close is the natural crossover; scoring from bar 55 hides it.
    baseline = run_experiment(tmp_path / "all", parse_input(experiment_payload))
    assert baseline["account"]["fill_count"] == 2
    payload = {**experiment_payload, "scored_from": experiment_payload["candles"][55]["ts"]}
    report = run_experiment(tmp_path / "scored", parse_input(payload))
    with sqlite3.connect(tmp_path / "scored" / "experiment.sqlite3") as connection:
        events = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM events")]
    assert events[54]["decision"] == "warm-up: indicator history only"
    assert events[54]["proposal"] is None
    # Nothing fills at the first scored open; the earliest entry is the second.
    assert events[55]["fills"] == []
    assert events[56]["fills"][0]["side"] == "buy"
    assert report["account"]["fill_count"] == 2


@pytest.mark.parametrize(
    ("scored_from", "message"),
    [
        ("2026-01-01T00:30:00+00:00", "scored_from"),
        ("2030-01-01T00:00:00+00:00", "scored_from"),
        ("not-a-time", None),
    ],
)
def test_scored_start_must_name_a_supplied_bar(experiment_payload, scored_from, message):
    experiment_payload["scored_from"] = scored_from
    with pytest.raises(ValueError, match=message):
        parse_input(experiment_payload)


def test_warm_up_must_cover_the_rules_history(experiment_payload):
    short = deepcopy(experiment_payload)
    short["scored_from"] = short["candles"][51]["ts"]
    with pytest.raises(ValueError, match="warm-up"):
        parse_input(short)
    short["scored_from"] = short["candles"][52]["ts"]
    assert parse_input(short).scored_index == 52
    reverting = {**experiment_payload, "benchmark": "mean-reversion"}
    reverting["scored_from"] = reverting["candles"][18]["ts"]
    with pytest.raises(ValueError, match="warm-up"):
        parse_input(reverting)
    reverting["scored_from"] = reverting["candles"][19]["ts"]
    assert parse_input(reverting).scored_index == 19
