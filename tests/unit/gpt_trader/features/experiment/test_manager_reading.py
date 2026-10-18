"""The predeclared round-two reading of the AI trade manager, from fixture reports."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.inputs import ManagerSettings
from gpt_trader.features.experiment.manager_reading import (
    frozen_manager,
    manager_verdict,
    read_round_two,
)

PROTOCOL = json.loads(
    (
        Path(__file__).parents[5] / "config/experiments/round-two-2026-10-20/protocol.json"
    ).read_text()
)
SYMBOLS = PROTOCOL["symbols"]


def report(symbol, net, hold, *, rule, case, round_trips=40, turns=210, outcomes=None):
    row = {
        "symbol": symbol,
        "benchmark": rule,
        "complete": True,
        "halted": False,
        "total_bars": 912,
        "scored_from": PROTOCOL["scored_from"],
        "settings": {**PROTOCOL["settings"], **PROTOCOL["cost_cases"][case]},
        "end_valuation": {
            "mark_bar": "2026-11-23T23:00:00+00:00",
            "position_open_at_end": False,
            "net_pnl": str(net),
            "round_trips": round_trips,
            "fee_share_of_gain": None,
            "slippage_share_of_gain": None,
            "comparators": {"cash_net_pnl": "0", "hold_the_coin": {"net_pnl": str(hold)}},
        },
    }
    if rule == "ai-trade-manager":
        row["manager"] = {
            "turns": turns,
            "outcomes": outcomes or {"accepted": turns},
            "models": [PROTOCOL["manager"]["model"]],
            "cost_usd": "1.5",
            "rulebook_version": 3,
            "settings": frozen_manager(PROTOCOL),
        }
    return row


def runs(manager=10, hold=5, mean_reversion=-3, baseline=-2, *, edit=None):
    """Every symbol, channel and case; ``edit`` may change any report in place."""
    nets = {"ai-trade-manager": manager, "mean-reversion": mean_reversion, "baseline": baseline}
    grouped = {
        rule: {
            case: [report(symbol, nets[rule], hold, rule=rule, case=case) for symbol in SYMBOLS]
            for case in PROTOCOL["cost_cases"]
        }
        for rule in PROTOCOL["benchmarks"]
    }
    if edit:
        edit(grouped)
    return grouped


def test_protocol_file_freezes_the_decision_record_window_and_settings():
    start, cutoff = aware_time(PROTOCOL["scored_from"]), aware_time(PROTOCOL["cutoff"])
    warmup = aware_time(PROTOCOL["warmup_start"])
    assert cutoff - start == timedelta(days=35)
    assert (start.hour, start.minute) == (0, 0)
    assert (cutoff - warmup) / timedelta(hours=1) == PROTOCOL["bars_per_symbol"] == 912
    assert start - warmup >= timedelta(hours=53)  # baseline's history need
    held_out = json.loads(
        (
            Path(__file__).parents[5] / "config/experiments/held-out-2026-09-06/protocol.json"
        ).read_text()
    )
    for key in ("symbols", "settings", "cost_cases", "min_scored_symbols", "min_round_trips"):
        assert PROTOCOL[key] == held_out[key]
    manager = ManagerSettings(**PROTOCOL["manager"])
    assert (manager.model, manager.effort, manager.max_tokens) == (
        "claude-fable-5-1",
        "high",
        16000,
    )
    assert (manager.cadence_bars, manager.history_bars) == (4, 48)
    assert PROTOCOL["reading"] == "ai-trade-manager"
    assert PROTOCOL["max_failed_turn_fraction"] == "0.05"


def test_a_pass_beats_cash_hold_and_mean_reversion_under_both_cases():
    result = read_round_two(runs(), PROTOCOL)
    assert result["bar"] == ["cash", "hold_the_coin", "mean-reversion"]
    assert {case["outcome"] for case in result["manager"].values()} == {"pass"}
    base = result["manager"]["base"]
    assert base["net_pnl"] == "80" and base["comparators"]["hold_the_coin"] == "40"
    assert len(base["compared_symbols"]) == 8
    assert (result["verdict"], result["action"]) == ("passes", "propose-round-three")


@pytest.mark.parametrize(
    ("manager", "hold", "mean_reversion", "trailed"),
    [
        (4, 5, -3, ["hold_the_coin"]),
        (0, -5, -3, ["cash"]),
        (-1, -5, 1, ["cash", "mean-reversion"]),
        (5, 5, -3, ["hold_the_coin"]),
    ],
)
def test_anything_short_of_every_comparator_trails_and_pauses(
    manager, hold, mean_reversion, trailed
):
    result = read_round_two(runs(manager, hold, mean_reversion), PROTOCOL)
    assert result["manager"]["stress"]["outcome"] == "trails"
    assert result["manager"]["stress"]["trailed"] == trailed
    assert (result["verdict"], result["action"]) == ("fails", "pause")


def test_baseline_joins_the_bar_only_when_it_survives_this_window():
    result = read_round_two(runs(manager=10, hold=5, baseline=12), PROTOCOL)
    assert result["rule_verdicts"]["baseline"] == "survives"
    assert result["bar"][-1] == "baseline"
    assert result["manager"]["base"]["trailed"] == ["baseline"]
    assert result["verdict"] == "fails"

    def few_trades(grouped):
        for row in grouped["baseline"]["base"]:
            row["end_valuation"]["round_trips"] = 1

    result = read_round_two(runs(manager=10, hold=5, baseline=12, edit=few_trades), PROTOCOL)
    assert result["rule_verdicts"]["baseline"] == "inconclusive"
    assert "baseline" not in result["bar"] and result["verdict"] == "passes"


def test_failed_calls_over_the_limit_or_a_foreign_model_unscore_a_symbol():
    def outage(grouped):
        for row in grouped["ai-trade-manager"]["base"][:2]:
            row["manager"]["outcomes"] = {"accepted": 199, "failed": 11}
        stress = grouped["ai-trade-manager"]["stress"]
        stress[0]["manager"]["outcomes"] = {"accepted": 200, "failed": 10}
        stress[1]["manager"]["outcomes"] = {"accepted": 100, "malformed": 60, "refused": 50}

    result = read_round_two(runs(edit=outage), PROTOCOL)
    base = result["manager"]["base"]
    assert base["outcome"] == "inconclusive" and len(base["compared_symbols"]) == 6
    assert not base["symbols"][0]["failed_turns_within_limit"]
    # Ten of 210 is within 5%; refused and malformed turns never unscore.
    assert result["manager"]["stress"]["outcome"] == "pass"
    assert result["verdict"] == "inconclusive" and result["action"] == "pause"

    def foreign(grouped):
        grouped["ai-trade-manager"]["base"][0]["manager"]["models"] = ["claude-opus-5-5"]

    base = read_round_two(runs(edit=foreign), PROTOCOL)["manager"]["base"]
    assert not base["symbols"][0]["pinned_model_only"]
    assert len(base["compared_symbols"]) == 7 and base["outcome"] == "pass"


def test_comparison_uses_only_symbols_scored_for_the_manager_and_each_rule():
    def stopped(grouped):
        grouped["ai-trade-manager"]["stress"][-1]["complete"] = False
        grouped["mean-reversion"]["stress"][0]["complete"] = False

    stress = read_round_two(runs(edit=stopped), PROTOCOL)["manager"]["stress"]
    assert stress["compared_symbols"] == sorted(SYMBOLS[1:-1])
    assert stress["outcome"] == "inconclusive"
    assert len(stress["symbols"]) == 8  # every row is still reported


def test_a_run_outside_the_frozen_settings_is_refused():
    def drifted(grouped):
        grouped["ai-trade-manager"]["base"][0]["manager"]["settings"]["effort"] = "low"

    with pytest.raises(ValueError, match="frozen ai-trade-manager settings"):
        read_round_two(runs(edit=drifted), PROTOCOL)

    def duplicated(grouped):
        grouped["ai-trade-manager"]["base"].append(grouped["ai-trade-manager"]["base"][0])

    with pytest.raises(ValueError, match="at most once"):
        read_round_two(runs(edit=duplicated), PROTOCOL)


@pytest.mark.parametrize(
    ("base", "stress", "expected"),
    [
        ("pass", "pass", "passes"),
        ("pass", "trails", "cost-dependent"),
        ("trails", "trails", "fails"),
        ("pass", "inconclusive", "inconclusive"),
    ],
)
def test_verdict_order(base, stress, expected):
    assert manager_verdict(base, stress) == expected
