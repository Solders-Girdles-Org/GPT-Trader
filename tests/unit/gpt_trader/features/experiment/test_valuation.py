"""End valuation and passive comparators, computed independently of the engine."""

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.engine import run_experiment
from gpt_trader.features.experiment.inputs import parse_input

BPS = Decimal(10000)


def truncate(payload, bars):
    payload["candles"] = payload["candles"][:bars]
    payload["recorded_at"] = (
        aware_time(payload["candles"][-1]["ts"]) + timedelta(hours=1)
    ).isoformat()
    return payload


def expected_hold(payload, scored, last, fee_bps="10", slip_bps="5"):
    fee, slip = Decimal(fee_bps) / BPS, Decimal(slip_bps) / BPS
    opening = Decimal(payload["candles"][scored]["open"])
    close = Decimal(payload["candles"][last]["close"])
    entry = opening * (1 + slip)
    quantity = (Decimal(1000) * Decimal("0.20") / entry).quantize(
        Decimal("0.00000001"), rounding=ROUND_DOWN
    )
    exit_price = close * (1 - slip)
    net = (
        quantity * exit_price
        - quantity * exit_price * fee
        - quantity * entry
        - quantity * entry * fee
    )
    return quantity, entry, exit_price, net, close / opening - 1


def test_open_position_is_valued_at_liquidation_but_stays_open(experiment_payload, tmp_path):
    report = run_experiment(tmp_path / "open", parse_input(truncate(experiment_payload, 56)))
    assert report["position"] is not None
    valuation = report["end_valuation"]
    assert valuation["position_open"] is True
    quantity = Decimal(report["account"]["quantity"])
    close = Decimal(experiment_payload["candles"][55]["close"])
    exit_price = close * (1 - Decimal(5) / BPS)
    exit_fee = quantity * exit_price * Decimal(10) / BPS
    value = Decimal(report["account"]["cash"]) + quantity * exit_price - exit_fee
    assert Decimal(valuation["exit_price"]) == exit_price
    assert Decimal(valuation["exit_fee"]) == exit_fee
    assert Decimal(valuation["value"]) == value
    assert Decimal(valuation["net_pnl"]) == value - 1000
    # Liquidation costs make end valuation strictly below the marked equity.
    assert Decimal(valuation["net_pnl"]) < Decimal(report["account"]["net_pnl"])
    assert valuation["as_of"] == experiment_payload["recorded_at"]


def test_flat_account_end_valuation_equals_realized_net(experiment_input, tmp_path):
    report = run_experiment(tmp_path / "flat", experiment_input)
    valuation = report["end_valuation"]
    assert valuation["position_open"] is False
    assert Decimal(valuation["exit_fee"]) == 0
    assert Decimal(valuation["net_pnl"]) == Decimal(report["account"]["net_pnl"])
    assert report["round_trips"] == 1


def test_hold_the_coin_buys_first_scored_open_under_exposure_cap(experiment_payload, tmp_path):
    experiment_payload["scored_from"] = experiment_payload["candles"][53]["ts"]
    experiment_payload["settings"] = {"fee_bps": "60", "slippage_bps": "10"}
    report = run_experiment(tmp_path / "hold", parse_input(experiment_payload))
    quantity, entry, exit_price, net, move = expected_hold(
        experiment_payload, 53, 59, fee_bps="60", slip_bps="10"
    )
    hold = report["comparators"]["hold_the_coin"]
    assert hold["entry_bar"] == experiment_payload["candles"][53]["ts"]
    assert Decimal(hold["quantity"]) == quantity
    assert Decimal(hold["entry_price"]) == entry
    assert Decimal(hold["exit_price"]) == exit_price
    assert Decimal(hold["net_pnl"]) == net
    assert Decimal(hold["price_move_fraction"]) == move
    assert quantity * entry <= Decimal(200)
    assert report["comparators"]["cash"] == {"net_pnl": "0"}


def test_window_reports_warm_up_and_scored_bars(experiment_payload, tmp_path):
    experiment_payload["scored_from"] = experiment_payload["candles"][53]["ts"]
    report = run_experiment(tmp_path / "window", parse_input(experiment_payload))
    assert report["window"] == {
        "first_bar": experiment_payload["candles"][0]["ts"],
        "last_bar": experiment_payload["candles"][59]["ts"],
        "scored_from": experiment_payload["candles"][53]["ts"],
        "recorded_at": experiment_payload["recorded_at"],
        "warmup_bars": 53,
        "scored_bars": 7,
    }


def test_partial_run_values_both_sides_at_the_last_processed_bar(experiment_payload, tmp_path):
    experiment_payload["scored_from"] = experiment_payload["candles"][53]["ts"]
    root = tmp_path / "partial"
    before = run_experiment(root, parse_input(experiment_payload), max_bars=53)
    assert before["comparators"]["hold_the_coin"] is None
    partial = run_experiment(root, max_bars=4)
    _quantity, _entry, _exit, net, _move = expected_hold(experiment_payload, 53, 56)
    assert Decimal(partial["comparators"]["hold_the_coin"]["net_pnl"]) == net
    assert (
        partial["end_valuation"]["as_of"]
        == (aware_time(experiment_payload["candles"][56]["ts"]) + timedelta(hours=1)).isoformat()
    )
