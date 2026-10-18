"""Rulebook schema, closed-bar signals and risk mechanics (no research evidence)."""

from copy import deepcopy
from decimal import Decimal

import pytest

from gpt_trader.features.experiment.engine import advance
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.ledger import initial_state
from gpt_trader.features.experiment.rulebook import STARTING_RULEBOOK, parse_rulebook


def source(payload, **rules):
    return parse_input(
        {
            **payload,
            "benchmark": "ai-rulebook",
            "rulebooks": [
                {
                    "effective_from": payload.get("scored_from", payload["candles"][0]["ts"]),
                    "rules": {**STARTING_RULEBOOK.to_dict(), **rules},
                }
            ],
        }
    )


def walk(input_):
    state, events = initial_state(input_), []
    for sequence in range(len(input_.candles)):
        event = advance(input_, sequence, state)
        state = event["state"]
        events.append(event)
    return events


@pytest.mark.parametrize(
    "change",
    [
        {"code": "print('hello')"},
        {"schema_version": 2},
        {"version": True},
        {"entry": "eval"},
        {"fast_bars": 50},
        {"slow_bars": 501},
        {"reversion_bars": 1},
        {"trend_filter_bars": -1},
        {"entry_z": "NaN"},
        {"entry_z": "bad"},
        {"exposure_fraction": "0.21"},
        {"exposure_fraction": 0.1},
        {"stop_fraction": "0"},
        {"target_fraction": "Infinity"},
        {"expiry_hours": 721},
        {"exit_on_signal": 1},
    ],
)
def test_strict_validation(change):
    with pytest.raises(ValueError):
        parse_rulebook({**STARTING_RULEBOOK.to_dict(), **change}, Decimal("0.20"))


def test_defaults_and_channel_binding(experiment_payload):
    parsed = parse_input({**experiment_payload, "benchmark": "ai-rulebook"})
    assert parsed.payload["rulebooks"][0]["rules"] == STARTING_RULEBOOK.to_dict()
    assert parsed.manager is None
    with pytest.raises(ValueError, match="only"):
        parse_input({**experiment_payload, "rulebooks": []})
    assert parsed.identity != source(experiment_payload, entry="cash").identity


def test_signals_use_closed_history_only(experiment_payload):
    original = source(experiment_payload)
    events = walk(original)
    entries = [event for event in events if event["proposal"]]
    assert entries[0]["sequence"] == 54
    assert events[54]["fills"] == []
    assert events[55]["fills"][0]["side"] == "buy"
    changed = deepcopy(experiment_payload)
    for bar in changed["candles"][55:]:
        bar.update(open="200", high="201", low="199", close="200")
    altered = walk(source(changed))
    assert [e["decision"] for e in events[:55]] == [e["decision"] for e in altered[:55]]
    # Identity changes, while levels before the changed future remain identical.
    assert events[54]["proposal"]["stop"] == altered[54]["proposal"]["stop"]


def test_mean_reversion_and_trend_filter(experiment_payload):
    payload = deepcopy(experiment_payload)
    for bar in payload["candles"]:
        bar.update(open="100", high="101", low="99", close="100")
    payload["candles"][30].update(open="100", high="101", low="89", close="90")
    payload["candles"][31]["open"] = "90"
    payload["candles"][31]["low"] = "89"
    events = walk(source(payload, entry="mean-reversion", target_fraction="0.5"))
    assert events[30]["proposal"] is not None
    assert events[31]["decision"] == "ai-rulebook: exit at next open"
    assert events[32]["fills"][0]["side"] == "sell"
    filtered = walk(source(payload, entry="mean-reversion", trend_filter_bars=20))
    assert not any(e["proposal"] for e in filtered)


def test_hold_expiry_renews_and_cash_never_enters(experiment_payload):
    events = walk(source(experiment_payload, entry="hold", expiry_hours=2))
    assert "expiry" in events[2]["actions"][0]
    assert events[2]["proposal"] is not None
    assert events[3]["fills"][0]["side"] == "buy"
    assert not any(
        e["fills"] or e["proposal"] for e in walk(source(experiment_payload, entry="cash"))
    )


@pytest.mark.parametrize("costs,stop", [(False, "0.04"), (True, "0.03"), (False, "0.15")])
def test_exposure_and_stop_risk_with_costs(experiment_payload, costs, stop):
    if costs:
        experiment_payload["settings"] = {"fee_bps": "50", "slippage_bps": "20"}
    parsed = source(experiment_payload, entry="hold", stop_fraction=stop)
    event = walk(parsed)[1]
    fill = event["fills"][0]
    qty, price = Decimal(fill["quantity"]), Decimal(fill["price"])
    assert qty * price <= Decimal(200)
    stop_fill = Decimal(event["state"]["position"]["stop"]) * (
        1 - parsed.settings.slippage_bps / 10000
    )
    risk = qty * (price - stop_fill + parsed.settings.fee_bps / 10000 * (price + stop_fill))
    assert risk <= Decimal(10)
    if stop == "0.15":
        assert qty * price < Decimal(70)
    else:
        assert qty * price > Decimal("199.99")


def test_halt_blocks_entries_but_allows_exit(experiment_payload):
    parsed = source(experiment_payload, entry="hold")
    state = initial_state(parsed)
    state["halted"] = True
    assert advance(parsed, 0, state)["proposal"] is None
    events = walk(parsed)
    state = deepcopy(events[1]["state"])
    state["halted"] = True
    payload = deepcopy(parsed.payload)
    payload["rulebooks"].append(
        {
            "effective_from": payload["candles"][2]["ts"],
            "rules": {**STARTING_RULEBOOK.to_dict(), "entry": "cash", "version": 2},
        }
    )
    parsed = parse_input(payload)
    exit_ = advance(parsed, 2, state)
    assert exit_["state"]["rulebook"]["exit_pending"]
    settled = advance(parsed, 3, exit_["state"])
    assert settled["fills"][0]["side"] == "sell"
    assert settled["proposal"] is None
