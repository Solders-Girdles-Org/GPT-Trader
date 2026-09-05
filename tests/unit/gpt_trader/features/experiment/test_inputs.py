"""Reject evidence that cannot support a reproducible hourly simulation."""

from copy import deepcopy

import pytest

from gpt_trader.features.experiment.inputs import parse_input


@pytest.mark.parametrize(
    ("field", "value"),
    [("open", "NaN"), ("high", "Infinity"), ("low", "0"), ("volume", "-1")],
)
def test_invalid_bar_money_is_rejected(experiment_payload, field, value):
    experiment_payload["candles"][0][field] = value
    with pytest.raises(ValueError):
        parse_input(experiment_payload)


@pytest.mark.parametrize("defect", ["stale", "unfinished", "duplicate", "ohlc", "missing"])
def test_incomplete_or_inconsistent_market_evidence_is_rejected(experiment_payload, defect):
    if defect == "stale":
        experiment_payload["candles"].pop()
    elif defect == "unfinished":
        experiment_payload["recorded_at"] = experiment_payload["candles"][-1]["ts"]
    elif defect == "duplicate":
        experiment_payload["candles"][1]["ts"] = experiment_payload["candles"][0]["ts"]
    elif defect == "ohlc":
        experiment_payload["candles"][0]["low"] = "101"
    else:
        del experiment_payload["candles"][0]["volume"]
    with pytest.raises(ValueError):
        parse_input(experiment_payload)


@pytest.mark.parametrize(
    "settings",
    [{"risk_fraction": "0"}, {"fee_bps": "10000"}, {"initial_cash": "NaN"}, {"live": "1"}],
)
def test_invalid_or_unknown_controls_are_rejected(experiment_payload, settings):
    experiment_payload["settings"] = settings
    with pytest.raises(ValueError):
        parse_input(experiment_payload)


def test_parsed_input_is_detached_from_callers_payload(experiment_payload):
    original = deepcopy(experiment_payload)
    source = parse_input(experiment_payload)
    identity = source.identity
    experiment_payload["candles"][0]["close"] = "999"
    assert source.identity == identity
    assert source.payload["candles"] == original["candles"]
