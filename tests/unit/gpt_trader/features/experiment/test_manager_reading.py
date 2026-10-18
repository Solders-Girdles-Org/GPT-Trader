"""The predeclared round-two reading of the AI trade manager, from fixture reports."""

import json
from datetime import timedelta
from decimal import Decimal
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
DIGEST = "f" * 64


def read(grouped):
    return read_round_two(grouped, PROTOCOL, source_sha256=DIGEST)


def report(symbol, net, hold, *, rule, case, round_trips=40, turns=210, outcomes=None):
    row = {
        "symbol": symbol,
        "benchmark": rule,
        "implementation_sha256": DIGEST,
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
    result = read(runs())
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
    result = read(runs(manager, hold, mean_reversion))
    assert result["manager"]["stress"]["outcome"] == "trails"
    assert result["manager"]["stress"]["trailed"] == trailed
    assert (result["verdict"], result["action"]) == ("fails", "pause")


def test_baseline_joins_the_bar_only_when_it_survives_this_window():
    result = read(runs(manager=10, hold=5, baseline=12))
    assert result["rule_verdicts"]["baseline"] == "survives"
    assert result["bar"][-1] == "baseline"
    assert result["manager"]["base"]["trailed"] == ["baseline"]
    assert result["verdict"] == "fails"

    def few_trades(grouped):
        for row in grouped["baseline"]["base"]:
            row["end_valuation"]["round_trips"] = 1

    result = read(runs(manager=10, hold=5, baseline=12, edit=few_trades))
    assert result["rule_verdicts"]["baseline"] == "inconclusive"
    assert "baseline" not in result["bar"] and result["verdict"] == "passes"


def test_failed_calls_over_the_limit_or_a_foreign_model_unscore_a_symbol():
    def outage(grouped):
        for row in grouped["ai-trade-manager"]["base"][:2]:
            row["manager"]["outcomes"] = {"accepted": 199, "failed": 11}
        stress = grouped["ai-trade-manager"]["stress"]
        stress[0]["manager"]["outcomes"] = {"accepted": 200, "failed": 10}
        stress[1]["manager"]["outcomes"] = {"accepted": 100, "malformed": 60, "refused": 50}

    result = read(runs(edit=outage))
    base = result["manager"]["base"]
    assert base["outcome"] == "inconclusive" and len(base["compared_symbols"]) == 6
    assert not base["symbols"][0]["failed_turns_within_limit"]
    # Ten of 210 is within 5%; refused and malformed turns never unscore.
    assert result["manager"]["stress"]["outcome"] == "pass"
    assert result["verdict"] == "inconclusive" and result["action"] == "pause"

    def foreign(grouped):
        grouped["ai-trade-manager"]["base"][0]["manager"]["models"] = ["claude-opus-5-5"]

    base = read(runs(edit=foreign))["manager"]["base"]
    assert not base["symbols"][0]["pinned_model_only"]
    assert len(base["compared_symbols"]) == 7 and base["outcome"] == "pass"


def test_comparison_uses_only_symbols_scored_for_the_manager_and_each_rule():
    def stopped(grouped):
        grouped["ai-trade-manager"]["stress"][-1]["complete"] = False
        grouped["mean-reversion"]["stress"][0]["complete"] = False

    stress = read(runs(edit=stopped))["manager"]["stress"]
    assert stress["compared_symbols"] == sorted(SYMBOLS[1:-1])
    assert stress["outcome"] == "inconclusive"
    assert len(stress["symbols"]) == 8  # every row is still reported


def test_a_run_outside_the_frozen_settings_is_refused():
    def drifted(grouped):
        grouped["ai-trade-manager"]["base"][0]["manager"]["settings"]["effort"] = "low"

    with pytest.raises(ValueError, match="frozen ai-trade-manager settings"):
        read(runs(edit=drifted))

    def duplicated(grouped):
        grouped["ai-trade-manager"]["base"].append(grouped["ai-trade-manager"]["base"][0])

    with pytest.raises(ValueError, match="every protocol symbol exactly once"):
        read(runs(edit=duplicated))

    def other_source(grouped):
        grouped["mean-reversion"]["stress"][3]["implementation_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="not the frozen"):
        read(runs(edit=other_source))


def test_every_symbol_must_be_listed_as_a_run_or_as_not_run():
    def omitted(grouped):
        grouped["ai-trade-manager"]["base"].pop()

    with pytest.raises(ValueError, match="every protocol symbol exactly once"):
        read(runs(edit=omitted))

    def not_started(grouped):
        stress = grouped["ai-trade-manager"]["stress"]
        stress[-1] = {"symbol": stress[-1]["symbol"], "not_run": "spend stop: never started"}
        grouped["baseline"]["base"][0] = {"symbol": SYMBOLS[0], "not_run": "fetch refused: gap"}

    result = read(runs(edit=not_started))
    stress = result["manager"]["stress"]
    assert len(stress["compared_symbols"]) == 7 and stress["outcome"] == "pass"
    assert stress["symbols"][-1] == {
        "symbol": SYMBOLS[-1],
        "scored": False,
        "not_run": "spend stop: never started",
    }
    assert len(result["rules"]["baseline"]["base"]["symbols"]) == 8

    def bare(grouped):
        grouped["ai-trade-manager"]["base"][0] = {"symbol": SYMBOLS[0], "not_run": " "}

    with pytest.raises(ValueError, match="not-run entry"):
        read(runs(edit=bare))


def test_a_pass_must_clear_one_round_trip_of_costs_per_symbol():
    # Margin: 8 symbols x $200 at the exposure cap x two sides of the case's costs.
    result = read(runs(manager=6, hold=5))
    assert Decimal(result["manager"]["base"]["margin"]) == Decimal("4.80")
    assert Decimal(result["manager"]["stress"]["margin"]) == Decimal("22.40")
    assert result["manager"]["base"]["outcome"] == "pass"  # 48 > 40 + 4.80
    assert result["manager"]["stress"]["trailed"] == ["hold_the_coin"]  # 48 <= 40 + 22.40
    assert result["verdict"] == "cost-dependent"

    def one_lucky_trade(grouped):
        for case in grouped["ai-trade-manager"].values():
            for row in case:
                row["end_valuation"]["net_pnl"] = "0"
                row["end_valuation"]["round_trips"] = 0
            case[0]["end_valuation"]["net_pnl"] = "0.01"
            case[0]["end_valuation"]["round_trips"] = 1

    # A falling window: hold and mean reversion lose, one cent of profit is not a pass.
    result = read(runs(hold=-50, mean_reversion=-20, edit=one_lucky_trade))
    assert {case["trailed"][0] for case in result["manager"].values()} == {"cash"}
    assert result["action"] == "pause"


def test_a_dated_snapshot_of_the_pinned_model_counts_as_pinned():
    def dated(grouped):
        grouped["ai-trade-manager"]["base"][0]["manager"]["models"] = ["claude-fable-5-1-20261001"]

    base = read(runs(edit=dated))["manager"]["base"]
    assert base["symbols"][0]["pinned_model_only"] and len(base["compared_symbols"]) == 8


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
