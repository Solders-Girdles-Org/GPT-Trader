"""End valuation and passive comparators, priced like the simulator's own fills.

Anything still held at the last processed bar, a rule's open position or the
hold-the-coin account alike, is valued at liquidation: that bar's close less
exit slippage, less the exit fee on the slipped notional. Nothing is sold in
the journal; open positions stay reported as open. Cash is the zero row.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any

from gpt_trader.features.experiment.inputs import ExperimentInput


def _liquidation(source: ExperimentInput, quantity: Decimal, close: Decimal) -> dict[str, Decimal]:
    slip = source.settings.slippage_bps / 10000
    price = close * (1 - slip)
    fee = quantity * price * source.settings.fee_bps / 10000
    return {
        "value": quantity * price - fee,
        "fee": fee,
        "slippage": quantity * (close - price),
    }


def _fill_slippage(source: ExperimentInput, fill: dict[str, Any]) -> Decimal:
    """Rebuild the slippage already inside a fill price (buys pay up, sells give up)."""
    slip = source.settings.slippage_bps / 10000
    quantity, price = Decimal(fill["quantity"]), Decimal(fill["price"])
    if fill["side"] == "buy":
        return quantity * (price - price / (1 + slip))
    return quantity * (price / (1 - slip) - price)


def hold_the_coin(source: ExperimentInput, last_sequence: int) -> dict[str, Any] | None:
    """Buy at the first scored open under the exposure cap with entry costs; hold."""
    first = source.scored_index
    if last_sequence < first:
        return None
    settings = source.settings
    slip, fee = settings.slippage_bps / 10000, settings.fee_bps / 10000
    opening = source.candles[first].open
    price = opening * (1 + slip)
    cash = settings.initial_cash
    quantity = min(cash / (price * (1 + fee)), cash * settings.exposure_fraction / price)
    quantity = (quantity / settings.quantity_increment).to_integral_value(
        rounding=ROUND_DOWN
    ) * settings.quantity_increment
    entry_fee = quantity * price * fee
    close = source.candles[last_sequence].close
    exit_ = _liquidation(source, quantity, close)
    value = cash - quantity * price - entry_fee + exit_["value"]
    return {
        "entry_bar": source.payload["candles"][first]["ts"],
        "entry_price": str(price),
        "quantity": str(quantity),
        "mark": str(close),
        "liquidation_equity": str(value),
        "net_pnl": str(value - cash),
        "fees": str(entry_fee + exit_["fee"]),
        "slippage": str(quantity * (price - opening) + exit_["slippage"]),
        "full_size_move_pct": str((close / opening - 1) * 100),
    }


def end_valuation(
    source: ExperimentInput, events: list[dict[str, Any]], account: dict[str, Any]
) -> dict[str, Any] | None:
    """Rule account at liquidation value, its cost shares and its comparators."""
    if not events:
        return None
    last = len(events) - 1
    fills = [fill for event in events for fill in event["fills"]]
    quantity = Decimal(account["quantity"])
    exit_ = _liquidation(source, quantity, source.candles[last].close)
    equity = Decimal(account["cash"]) + exit_["value"]
    net = equity - source.settings.initial_cash
    fees = Decimal(account["fees"]) + exit_["fee"]
    slippage = sum((_fill_slippage(source, fill) for fill in fills), Decimal(0))
    slippage += exit_["slippage"]
    gross = net + fees + slippage
    return {
        "mark_bar": source.payload["candles"][last]["ts"],
        "position_open_at_end": quantity > 0,
        "liquidation_equity": str(equity),
        "net_pnl": str(net),
        "round_trips": sum(fill["side"] == "sell" for fill in fills),
        "fees": str(fees),
        "slippage": str(slippage),
        "gain_before_costs": str(gross),
        "fee_share_of_gain": str(fees / gross) if gross > 0 else None,
        "slippage_share_of_gain": str(slippage / gross) if gross > 0 else None,
        "comparators": {"cash_net_pnl": "0", "hold_the_coin": hold_the_coin(source, last)},
    }
