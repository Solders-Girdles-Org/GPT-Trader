"""End valuation and passive comparators for one recorded account.

Anything still held when the processed data ends, whether the rule's open
position or the hold-the-coin comparator, is valued the same way: the last
processed close less the case's exit slippage and fee. Nothing is sold in the
journal; open positions are still reported as open. Hold-the-coin is the same
starting account buying at the first scored bar's open under the exposure cap,
paying entry slippage and fee. Cash is the zero comparator.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from gpt_trader.features.experiment.inputs import ExperimentInput, Settings

_BPS = Decimal(10000)


def _liquidation(quantity: Decimal, close: Decimal, settings: Settings) -> tuple[Decimal, Decimal]:
    """Exit price after slippage and the fee charged on that exit notional."""
    exit_price = close * (1 - settings.slippage_bps / _BPS)
    return exit_price, quantity * exit_price * settings.fee_bps / _BPS


def window(source: ExperimentInput) -> dict[str, Any]:
    scored = source.scored_index
    return {
        "first_bar": source.payload["candles"][0]["ts"],
        "last_bar": source.payload["candles"][-1]["ts"],
        "scored_from": source.payload["scored_from"],
        "recorded_at": source.payload["recorded_at"],
        "warmup_bars": scored,
        "scored_bars": len(source.candles) - scored,
    }


def end_valuation(
    source: ExperimentInput, processed: int, account: dict[str, Any]
) -> dict[str, Any] | None:
    """Liquidation value of the reconciled account at the last processed close."""
    if processed == 0:
        return None
    last = source.candles[processed - 1]
    quantity = Decimal(account["quantity"])
    exit_price, exit_fee = _liquidation(quantity, last.close, source.settings)
    value = Decimal(account["cash"]) + quantity * exit_price - exit_fee
    return {
        "as_of": (last.ts + timedelta(hours=1)).isoformat(),
        "position_open": quantity > 0,
        "mark": str(last.close),
        "exit_price": str(exit_price),
        "exit_fee": str(exit_fee),
        "value": str(value),
        "net_pnl": str(value - source.settings.initial_cash),
    }


def hold_the_coin(source: ExperimentInput, processed: int) -> dict[str, Any] | None:
    """Buy at the first scored open under the exposure cap; value like a rule position."""
    scored = source.scored_index
    if processed <= scored:
        return None
    settings = source.settings
    first = source.candles[scored]
    entry_price = first.open * (1 + settings.slippage_bps / _BPS)
    fee = settings.fee_bps / _BPS
    cash = settings.initial_cash
    quantity = min(
        cash / (entry_price * (1 + fee)),
        cash * settings.exposure_fraction / entry_price,
    )
    quantity = (quantity / settings.quantity_increment).to_integral_value(
        rounding=ROUND_DOWN
    ) * settings.quantity_increment
    entry_fee = quantity * entry_price * fee
    last = source.candles[processed - 1]
    exit_price, exit_fee = _liquidation(quantity, last.close, settings)
    net = quantity * exit_price - exit_fee - quantity * entry_price - entry_fee
    return {
        "entry_bar": source.payload["candles"][scored]["ts"],
        "quantity": str(quantity),
        "entry_price": str(entry_price),
        "entry_fee": str(entry_fee),
        "exit_price": str(exit_price),
        "exit_fee": str(exit_fee),
        "net_pnl": str(net),
        # Context only: the unslipped full-size move, not an account result.
        "price_move_fraction": str(last.close / first.open - 1),
    }
