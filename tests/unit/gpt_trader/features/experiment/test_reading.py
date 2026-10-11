"""The predeclared reading: outcomes, verdicts and the combined action grid."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from gpt_trader.features.experiment.reading import (
    after_follow_up,
    case_outcome,
    combined_action,
    exact_fee_verdict,
    rule_verdict,
)

PROTOCOL = json.loads(
    (Path(__file__).parents[5] / "config/experiments/held-out-2026-09-06/protocol.json").read_text()
)


def report(symbol, net, hold, round_trips=5, *, complete=True, rule="baseline", case="base"):
    return {
        "symbol": symbol,
        "benchmark": rule,
        "complete": complete,
        "halted": False,
        "total_bars": 912,
        "scored_from": "2026-09-06T00:00:00+00:00",
        "settings": {**PROTOCOL["settings"], **PROTOCOL["cost_cases"][case]},
        "end_valuation": {
            "mark_bar": "2026-10-10T23:00:00+00:00",
            "position_open_at_end": False,
            "net_pnl": str(net),
            "round_trips": round_trips,
            "fee_share_of_gain": None,
            "slippage_share_of_gain": None,
            "comparators": {"cash_net_pnl": "0", "hold_the_coin": {"net_pnl": str(hold)}},
        },
    }


def outcome(nets, holds, **kwargs):
    reports = [
        report(symbol, net, hold, **kwargs)
        for symbol, net, hold in zip(PROTOCOL["symbols"], nets, holds, strict=False)
    ]
    return case_outcome(reports, PROTOCOL, rule="baseline", case="base")


def test_protocol_file_matches_the_decision_record():
    assert PROTOCOL["bars_per_symbol"] == 912
    assert PROTOCOL["cost_cases"] == {
        "base": {"fee_bps": "10", "slippage_bps": "5"},
        "stress": {"fee_bps": "60", "slippage_bps": "10"},
    }
    assert (PROTOCOL["min_scored_symbols"], PROTOCOL["min_round_trips"]) == (7, 30)
    assert len(PROTOCOL["symbols"]) == 8


@pytest.mark.parametrize(
    ("net", "hold", "expected"),
    [
        (2, 1, "pass"),
        (2, 3, "positive-but-trails-hold"),
        (-1, -2, "loses-less-than-hold"),
        (0, 0, "fail"),
        (-2, -1, "fail"),
    ],
)
def test_conclusive_outcomes_follow_the_table(net, hold, expected):
    assert outcome([net] * 8, [hold] * 8)["outcome"] == expected


def test_coverage_floors_make_a_case_inconclusive_and_keep_every_row():
    assert outcome([5] * 8, [0] * 8, round_trips=3)["outcome"] == "inconclusive"
    two_missing = outcome([5] * 6, [0] * 6)
    assert two_missing["outcome"] == "inconclusive" and two_missing["scored_symbols"] == 6
    reports = [report(symbol, 5, 0) for symbol in PROTOCOL["symbols"]]
    reports[0]["complete"] = False
    result = case_outcome(reports, PROTOCOL, rule="baseline", case="base")
    assert result["scored_symbols"] == 7 and result["outcome"] == "pass"
    assert len(result["symbols"]) == 8 and result["net_pnl"] == "35"


def test_mismatched_runs_are_refused():
    wrong = [report(PROTOCOL["symbols"][0], 1, 0, rule="mean-reversion")]
    with pytest.raises(ValueError, match="does not match"):
        case_outcome(wrong, PROTOCOL, rule="baseline", case="base")
    wrong = [report(PROTOCOL["symbols"][0], 1, 0, case="stress")]
    with pytest.raises(ValueError, match="does not match"):
        case_outcome(wrong, PROTOCOL, rule="baseline", case="base")
    twice = [report("BTC-USD", 1, 0), deepcopy(report("BTC-USD", 1, 0))]
    with pytest.raises(ValueError, match="at most once"):
        case_outcome(twice, PROTOCOL, rule="baseline", case="base")


def test_verdict_order_and_follow_ups():
    assert rule_verdict("pass", "inconclusive") == "inconclusive"
    assert rule_verdict("pass", "pass") == "survives"
    assert rule_verdict("fail", "pass") == "cost-dependent"
    assert rule_verdict("positive-but-trails-hold", "fail") == "does-not-survive"
    assert exact_fee_verdict("pass") == "survives-at-rj-fee"
    assert exact_fee_verdict("loses-less-than-hold") == "does-not-survive"
    assert after_follow_up("inconclusive") == "not-shown-to-survive"
    assert after_follow_up("cost-dependent") == "not-shown-to-survive"
    assert after_follow_up("survives-at-rj-fee") == "survives-at-rj-fee"


GRID = {  # mean reversion row, baseline column, from the decision record
    "survives": "AAAA",
    "cost-dependent": "ABBB",
    "does-not-survive": "ABDC",
    "inconclusive": "ABCC",
}
COLUMNS = ("survives", "cost-dependent", "does-not-survive", "inconclusive")


@pytest.mark.parametrize("mean_reversion", list(GRID))
def test_combined_action_matches_the_recorded_grid(mean_reversion):
    for baseline, action in zip(COLUMNS, GRID[mean_reversion], strict=True):
        assert combined_action(mean_reversion, baseline) == action
    assert combined_action("survives-at-rj-fee", "does-not-survive") == "A"
    assert combined_action("not-shown-to-survive", "does-not-survive") == "D"
