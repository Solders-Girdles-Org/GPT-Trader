"""The predeclared rulebook-author reading and its frozen protocol, from fixture reports."""

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.author_reading import ACTIONS, read_rulebook_author
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.rulebook_author import (
    INSTRUCTIONS,
    build_author_request,
    protocol_author,
)

PROTOCOL_DIR = Path(__file__).parents[5] / "config/experiments/rulebook-author-2026-10-22"
PROTOCOL = json.loads((PROTOCOL_DIR / "protocol.json").read_text())
SYMBOLS = PROTOCOL["symbols"]
DIGEST = "e" * 64
STARTING = PROTOCOL["author"]["starting_rulebook"]
MARK_BAR = (aware_time(PROTOCOL["cutoff"]) - timedelta(hours=1)).isoformat()


def valuation(net, hold):
    return {
        "mark_bar": MARK_BAR,
        "position_open_at_end": False,
        "net_pnl": str(net),
        "round_trips": 3,
        "fee_share_of_gain": None,
        "slippage_share_of_gain": None,
        "comparators": {"cash_net_pnl": "0", "hold_the_coin": {"net_pnl": str(hold)}},
    }


def coin(symbol, net, hold, case, **changes):
    return {
        "symbol": symbol,
        "benchmark": "ai-rulebook",
        "implementation_sha256": DIGEST,
        "complete": True,
        "halted": False,
        "total_bars": PROTOCOL["bars_per_symbol"],
        "scored_from": PROTOCOL["scored_from"],
        "settings": {**PROTOCOL["settings"], **PROTOCOL["cost_cases"][case]},
        "end_valuation": valuation(net, hold),
        "rulebook": {"starting": STARTING, "version": 6, "activations": []},
    } | changes


def author(net, hold, case, *, failed=0, symbols=SYMBOLS, model=None):
    author_settings = PROTOCOL["author"]
    return {
        "implementation_sha256": DIGEST,
        "complete": True,
        "total_bars": PROTOCOL["bars_per_symbol"],
        "manager": {
            "rulebook": "unused by the author",
            "cadence_bars": 4,
            "history_bars": 48,
            "model": author_settings["model"],
            "effort": author_settings["effort"],
            "max_tokens": author_settings["max_tokens"],
        },
        "settings": {"cadence_bars": 168, "history_days": 30},
        "ledger": {"path": "/ledger", "cap": "25"},
        "author": {
            "turns": 5,
            "outcomes": {"accepted": 5 - failed, "rejected": 0, "failed": failed},
            "models": [model or author_settings["model"]],
            "cost_usd": "4",
        },
        "symbols": [coin(symbol, net, hold, case) for symbol in symbols],
    }


def rule(symbol, net, hold, *, name, case):
    return {
        "symbol": symbol,
        "benchmark": name,
        "implementation_sha256": DIGEST,
        "complete": True,
        "halted": False,
        "total_bars": PROTOCOL["bars_per_symbol"],
        "scored_from": PROTOCOL["scored_from"],
        "settings": {**PROTOCOL["settings"], **PROTOCOL["cost_cases"][case]},
        "end_valuation": valuation(net, hold) | {"round_trips": 40},
    }


def runs(net=10, hold=5, mean_reversion=-3, baseline=-2, **author_changes):
    grouped = {
        name: {
            case: [rule(s, value, hold, name=name, case=case) for s in SYMBOLS]
            for case in PROTOCOL["cost_cases"]
        }
        for name, value in (("mean-reversion", mean_reversion), ("baseline", baseline))
    }
    grouped["ai-rulebook"] = {
        case: author(net, hold, case, **author_changes) for case in PROTOCOL["cost_cases"]
    }
    return grouped


def read(grouped):
    return read_rulebook_author(grouped, PROTOCOL, source_sha256=DIGEST)


def test_protocol_freezes_a_window_after_its_freeze_with_full_context():
    warmup, start = aware_time(PROTOCOL["warmup_start"]), aware_time(PROTOCOL["scored_from"])
    cutoff = aware_time(PROTOCOL["cutoff"])
    settings, manager, rules = protocol_author(
        PROTOCOL["author"], Decimal(PROTOCOL["settings"]["exposure_fraction"])
    )
    assert PROTOCOL["reading"] == "ai-rulebook-author"
    assert (cutoff - start) == timedelta(days=35)
    assert (start - warmup) == timedelta(days=settings.history_days)
    assert (cutoff - warmup) / timedelta(hours=1) == PROTOCOL["bars_per_symbol"]
    # Every rule history the schema allows (500 hourly bars) fits in the warm-up.
    assert (start - warmup) / timedelta(hours=1) >= 500
    assert len(range(0, 840, settings.cadence_bars)) == 5
    assert (manager.model, manager.effort, manager.max_tokens) == (
        "claude-fable-5-1",
        "high",
        16000,
    )
    assert rules["entry"] == "cash" and rules == STARTING
    # The freeze deadline in PROTOCOL.md: a start at least one day after merge.
    text = (PROTOCOL_DIR / "PROTOCOL.md").read_text()
    assert "before 2026-10-21T00:00Z" in text
    assert start - timedelta(days=1) == aware_time("2026-10-21T00:00:00+00:00")


def test_prompt_states_the_window_and_how_it_is_read(experiment_payload):
    source = parse_input(
        {
            **experiment_payload,
            "benchmark": "ai-rulebook",
            "scored_from": experiment_payload["candles"][10]["ts"],
        }
    )
    request = build_author_request(
        [source],
        10,
        {},
        {source.symbol: STARTING},
        protocol_author(PROTOCOL["author"], Decimal("0.20"))[0],
    )
    assert request["context"]["schema"] == "rulebook-author/2"
    assert request["context"]["window"] == {
        "scored_from": source.candles[10].ts.isoformat(),
        "cutoff": (source.candles[-1].ts + timedelta(hours=1)).isoformat(),
    }
    for phrase in ("staying in cash", "mean-reversion", "one round trip's costs"):
        assert phrase in INSTRUCTIONS


def test_author_above_every_comparator_by_the_margin_passes():
    result = read(runs())
    assert result["verdict"] == "passes" and result["action"] == ACTIONS["passes"]
    assert result["bar"] == ["cash", "hold_the_coin", "mean-reversion"]
    base = result["author"]["base"]
    assert base["outcome"] == "pass" and Decimal(base["margin"]) == Decimal("4.80")
    assert base["author"]["checks"] == {
        "complete": True,
        "frozen_settings": True,
        "pinned_model_only": True,
        "failed_turns_within_limit": True,
    }


def test_author_below_holding_fails_and_proposes_a_second_window():
    result = read(runs(net=5.5, hold=5))
    assert result["verdict"] == "fails"
    assert result["author"]["stress"]["trailed"] == ["hold_the_coin"]
    assert result["action"] == "one-fresh-window-then-pause-if-not-passed"


def test_two_failed_turns_or_another_model_leave_the_case_unscored():
    assert read(runs(failed=1))["verdict"] == "passes"
    result = read(runs(failed=2))
    assert result["verdict"] == "inconclusive" and result["action"] == "no-reading"
    assert not result["author"]["base"]["author"]["checks"]["failed_turns_within_limit"]
    other = read(runs(model="claude-opus-5-5"))
    assert other["verdict"] == "inconclusive"
    assert other["models_observed"] == ["claude-opus-5-5"]


def test_dated_snapshot_of_the_pinned_model_counts():
    assert read(runs(model="claude-fable-5-1-20261001"))["verdict"] == "passes"


def test_refused_fetch_leaves_a_coin_out_and_seven_still_read():
    grouped = runs(symbols=SYMBOLS[1:])
    for name in ("mean-reversion", "baseline"):
        for entries in grouped[name].values():
            entries[0] = {"symbol": SYMBOLS[0], "not_run": "fetch refused: gap"}
    result = read(grouped)
    assert result["verdict"] == "passes"
    assert len(result["author"]["base"]["compared_symbols"]) == 7
    assert result["author"]["base"]["symbols"][-1]["symbol"] == SYMBOLS[0]
    # A rule cannot be "not run" for a coin the author did run.
    full = runs()
    full["baseline"]["base"][0] = {"symbol": SYMBOLS[0], "not_run": "skipped"}
    with pytest.raises(ValueError, match="not run while another"):
        read(full)


def test_author_portfolio_missing_a_fetched_coin_is_refused():
    grouped = runs()
    grouped["ai-rulebook"]["base"]["symbols"].pop()
    with pytest.raises(ValueError, match="exactly the coins"):
        read(grouped)


def test_not_run_case_is_inconclusive():
    grouped = runs()
    grouped["ai-rulebook"]["stress"] = {"not_run": "spend stop before start"}
    result = read(grouped)
    assert result["verdict"] == "inconclusive"
    assert result["author"]["stress"]["author"] == {"not_run": "spend stop before start"}


@pytest.mark.parametrize("change", ["digest", "starting", "costs", "outside"])
def test_runs_not_of_this_protocol_are_refused(change):
    grouped = runs()
    base = grouped["ai-rulebook"]["base"]
    if change == "digest":
        base["implementation_sha256"] = "0" * 64
    elif change == "starting":
        base["symbols"][0]["rulebook"]["starting"] = {**STARTING, "entry": "trend"}
    elif change == "costs":
        base["symbols"][0]["settings"]["fee_bps"] = "60"
    else:
        base["symbols"][0]["symbol"] = "DOGE-USD"
    with pytest.raises(ValueError):
        read(grouped)


def test_changed_author_settings_do_not_count():
    grouped = runs()
    for case in grouped["ai-rulebook"].values():
        case["settings"]["cadence_bars"] = 24
    result = read(grouped)
    assert result["verdict"] == "inconclusive"
    assert not result["author"]["base"]["author"]["checks"]["frozen_settings"]
