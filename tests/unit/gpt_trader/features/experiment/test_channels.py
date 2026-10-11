"""Benchmark channel selection, identity binding and the scored window."""

from datetime import UTC, datetime, timedelta

import pytest

from gpt_trader.features.experiment.engine import advance, run_experiment
from gpt_trader.features.experiment.inputs import parse_input, with_benchmark
from gpt_trader.features.experiment.ledger import ExperimentIntegrityError, initial_state


@pytest.fixture
def dip_payload():
    """Flat oscillation then a sharp dip and recovery: a mean-reversion setup."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    closes = [100 + (index % 2) for index in range(60)] + [94, 94, 95, 97, 100, 101, 101, 101]
    candles = []
    for index, close in enumerate(closes):
        opening = closes[index - 1] if index else close
        candles.append(
            {
                "ts": (start + timedelta(hours=index)).isoformat(),
                "open": str(opening),
                "high": str(max(opening, close) + 1),
                "low": str(min(opening, close) - 1),
                "close": str(close),
                "volume": "10",
            }
        )
    return {
        "schema_version": 1,
        "source": "Synthetic dip fixture; no performance evidence",
        "symbol": "BTC-USD",
        "recorded_at": (start + timedelta(hours=len(candles))).isoformat(),
        "candles": candles,
    }


def decisions(source):
    state, events = initial_state(source), []
    for index in range(len(source.candles)):
        event = advance(source, index, state)
        events.append(event)
        state = event["state"]
    return events


def test_default_channel_is_baseline_and_bound_into_the_payload(experiment_payload):
    source = parse_input(experiment_payload)
    assert source.benchmark == "baseline"
    assert source.payload["benchmark"] == "baseline"
    assert source.scored_index == 0
    assert source.payload["scored_from"] == experiment_payload["candles"][0]["ts"]


def test_mean_reversion_channel_runs_the_existing_strategy(dip_payload):
    events = decisions(parse_input({**dip_payload, "benchmark": "mean-reversion"}))
    proposals = [event for event in events if event["proposal"]]
    assert proposals[0]["sequence"] == 60
    assert "mean-reversion" in proposals[0]["proposal"]["thesis"]
    assert events[61]["fills"][0]["side"] == "buy"
    assert not any(event["proposal"] for event in decisions(parse_input(dip_payload)))


def test_channel_choice_changes_identity_and_cannot_change_on_resume(dip_payload, tmp_path):
    baseline = parse_input(dip_payload)
    reversion = parse_input({**dip_payload, "benchmark": "mean-reversion"})
    assert baseline.identity != reversion.identity
    run_experiment(tmp_path / "run", baseline, max_bars=1)
    with pytest.raises(ExperimentIntegrityError, match="changed"):
        run_experiment(tmp_path / "run", reversion)
    with pytest.raises(ExperimentIntegrityError, match="different benchmark"):
        run_experiment(tmp_path / "run", benchmark="mean-reversion")
    assert run_experiment(tmp_path / "run", benchmark="baseline")["complete"]


def test_unknown_or_contradictory_channel_is_refused(dip_payload):
    with pytest.raises(ValueError, match="Unknown benchmark"):
        parse_input({**dip_payload, "benchmark": "regime-switcher"})
    with pytest.raises(ValueError, match="different benchmark"):
        with_benchmark({**dip_payload, "benchmark": "baseline"}, "mean-reversion")
    assert with_benchmark(dip_payload, "mean-reversion")["benchmark"] == "mean-reversion"


def test_warm_up_bars_are_history_only(dip_payload):
    dip_payload["benchmark"] = "mean-reversion"
    dip_payload["scored_from"] = dip_payload["candles"][61]["ts"]
    events = decisions(parse_input(dip_payload))
    assert {event["decision"] for event in events[:61]} == {"warm-up bar: indicator history only"}
    assert not any(event["fills"] for event in events[:62])
    with pytest.raises(ValueError, match="scored_from"):
        parse_input({**dip_payload, "scored_from": "2026-01-01T00:30:00+00:00"})
