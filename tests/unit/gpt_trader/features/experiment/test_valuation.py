"""Liquidation-value end valuation and the hold-the-coin comparator."""

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.engine import run_experiment
from gpt_trader.features.experiment.inputs import parse_input


def test_open_position_and_hold_are_valued_at_liquidation(experiment_payload, tmp_path):
    experiment_payload["candles"] = experiment_payload["candles"][:56]
    experiment_payload["recorded_at"] = (
        aware_time(experiment_payload["candles"][-1]["ts"]) + timedelta(hours=1)
    ).isoformat()
    experiment_payload["scored_from"] = experiment_payload["candles"][53]["ts"]
    source = parse_input(experiment_payload)
    report = run_experiment(tmp_path / "open", source)
    valuation = report["end_valuation"]
    account = report["account"]
    close = source.candles[-1].close
    exit_price = close * Decimal("0.9995")
    quantity = Decimal(account["quantity"])
    exit_fee = quantity * exit_price * Decimal("0.001")
    assert valuation["position_open_at_end"] and report["position"] is not None
    assert Decimal(valuation["liquidation_equity"]) == (
        Decimal(account["cash"]) + quantity * exit_price - exit_fee
    )
    assert Decimal(valuation["net_pnl"]) < Decimal(account["net_pnl"])
    assert valuation["round_trips"] == 0
    gross = Decimal(valuation["gain_before_costs"])
    assert gross == (
        Decimal(valuation["net_pnl"]) + Decimal(valuation["fees"]) + Decimal(valuation["slippage"])
    )

    hold = valuation["comparators"]["hold_the_coin"]
    assert valuation["comparators"]["cash_net_pnl"] == "0"
    opening = source.candles[53].open
    entry = opening * Decimal("1.0005")
    held = min(
        Decimal(1000) / (entry * Decimal("1.001")), Decimal(1000) * Decimal("0.20") / entry
    ).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    expected = (
        Decimal(1000)
        - held * entry * Decimal("1.001")
        + held * exit_price
        - held * exit_price * Decimal("0.001")
    )
    assert hold["entry_bar"] == experiment_payload["candles"][53]["ts"]
    assert Decimal(hold["quantity"]) == held
    assert Decimal(hold["liquidation_equity"]) == expected
    assert Decimal(hold["net_pnl"]) == expected - 1000
    assert Decimal(hold["full_size_move_pct"]) == (close / opening - 1) * 100


def test_completed_trade_reports_round_trip_and_cost_shares(experiment_input, tmp_path):
    valuation = run_experiment(tmp_path / "done", experiment_input)["end_valuation"]
    assert valuation["round_trips"] == 1
    assert not valuation["position_open_at_end"]
    gross = Decimal(valuation["gain_before_costs"])
    assert gross > 0
    assert Decimal(valuation["fee_share_of_gain"]) == Decimal(valuation["fees"]) / gross


def test_nothing_processed_has_no_valuation(experiment_input, tmp_path):
    from gpt_trader.features.experiment.ledger import inspect_run, open_run

    open_run(tmp_path / "empty", experiment_input).close()
    assert inspect_run(tmp_path / "empty")["end_valuation"] is None
