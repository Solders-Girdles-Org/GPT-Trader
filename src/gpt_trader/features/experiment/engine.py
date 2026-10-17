"""Bounded spot simulation: observe a closed bar, decide, fill on the next bar.

No broker, network, scheduler, environment profile or live authority is accepted.
Every bar and all its effects commit together. Interruptions replay the uncommitted
bar; committed bars are never submitted again.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from gpt_trader.core.fill_accounting import FillFact, aware_time, semantic_checksum
from gpt_trader.features.experiment.benchmarks import HISTORY_BARS, propose
from gpt_trader.features.experiment.inputs import MANAGER_CHANNEL, ExperimentInput
from gpt_trader.features.experiment.ledger import (
    ExperimentIntegrityError,
    encode,
    initial_state,
    inspect_run,
    open_run,
    read_events,
    read_source,
    reconcile,
)
from gpt_trader.features.experiment.trade_manager import DecisionTransportProtocol, manage
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot, SymbolSeries


def _fill(
    source: ExperimentInput,
    sequence: int,
    state: dict[str, Any],
    *,
    side: str,
    quantity: Decimal,
    price: Decimal,
    reason: str,
) -> dict[str, Any]:
    fee = quantity * price * source.settings.fee_bps / 10000
    notional = quantity * price
    cash = Decimal(state["cash"])
    if side == "buy":
        state["cash"] = str(cash - notional - fee)
        state["quantity"] = str(quantity)
        state["cost_basis"] = str(notional + fee)
        moment = source.candles[sequence].ts + timedelta(microseconds=1)
    else:
        state["cash"] = str(cash + notional - fee)
        state["realized_net"] = str(
            Decimal(state["realized_net"]) + notional - fee - Decimal(state["cost_basis"])
        )
        state["quantity"] = "0"
        state["cost_basis"] = "0"
        state["position"] = None
        moment = source.candles[sequence].ts + timedelta(hours=1, microseconds=-1)
    state["fees"] = str(Decimal(state["fees"]) + fee)
    identity = f"{source.identity[:16]}-{sequence}-{side}"
    return FillFact(
        identity,
        identity,
        identity,
        source.symbol,
        side,
        str(quantity),
        str(price),
        moment.isoformat(),
        str(fee),
        "USD",
    ).to_dict()


def advance(
    source: ExperimentInput,
    sequence: int,
    previous_state: dict[str, Any],
    transport: DecisionTransportProtocol | None = None,
) -> dict[str, Any]:
    """Observation transition. Strategy sees only bars closed at this cutoff.

    Pure for the rule channels. The AI trade manager's due turns go through
    ``transport``; replay passes the journaled reply instead.
    """
    managed = source.benchmark == MANAGER_CHANNEL
    state = deepcopy(previous_state)
    candle = source.candles[sequence]
    settings = source.settings
    slip = settings.slippage_bps / 10000
    fee = settings.fee_bps / 10000
    fills = []
    actions = []
    if managed and state["manager"]["exit_pending"]:
        # Only set while holding; nothing can close the position before this open.
        state["manager"]["exit_pending"] = False
        fills.append(
            _fill(
                source,
                sequence,
                state,
                side="sell",
                quantity=Decimal(state["quantity"]),
                price=candle.open * (1 - slip),
                reason="manager exit",
            )
        )
        actions.append("position settled: manager exit at next open")
    pending = state["pending"]
    state["pending"] = None
    if pending and not state["halted"] and state["position"] is None:
        price = candle.open * (1 + slip)
        lower, upper = Decimal(pending["lower"]), Decimal(pending["upper"])
        stop, target = Decimal(pending["stop"]), Decimal(pending["target"])
        if not lower <= price <= upper or not stop < price < target:
            actions.append("entry refused: next opening fill is outside the recorded plan")
        else:
            # Current cash is equity while flat. Include both sides' fees and
            # adverse exit slippage; stop gaps can still exceed this estimate.
            cash = Decimal(state["cash"])
            stop_fill = stop * (1 - slip)
            loss_per_unit = price - stop_fill + fee * (price + stop_fill)
            quantity = min(
                cash / (price * (1 + fee)),
                # A manager entry names its own size; it was clamped to the cap.
                cash * Decimal(pending.get("size_fraction", settings.exposure_fraction)) / price,
                cash * settings.risk_fraction / loss_per_unit,
            )
            quantity = (quantity / settings.quantity_increment).to_integral_value(
                rounding=ROUND_DOWN
            ) * settings.quantity_increment
            if quantity <= 0:
                actions.append("entry refused: size rounds to zero")
            else:
                fills.append(
                    _fill(
                        source,
                        sequence,
                        state,
                        side="buy",
                        quantity=quantity,
                        price=price,
                        reason="entry",
                    )
                )
                state["position"] = pending
                actions.append("entry filled at next open with costs and current cash limits")

    position = state["position"]
    if position is not None:
        stop, target = Decimal(position["stop"]), Decimal(position["target"])
        exit_price = None
        reason = ""
        # Explicit conservative ordering for unknown intrabar path. Stops gap
        # through at the open; favorable target gaps get no extra improvement.
        if candle.low <= stop:
            exit_price, reason = min(candle.open, stop), "stop (stop first if both touched)"
        elif candle.high >= target:
            exit_price, reason = target, "target"
        elif candle.ts + timedelta(hours=1) >= aware_time(position["expires_at"]):
            exit_price, reason = candle.close, "expiry"
        if exit_price is not None:
            fills.append(
                _fill(
                    source,
                    sequence,
                    state,
                    side="sell",
                    quantity=Decimal(state["quantity"]),
                    price=exit_price * (1 - slip),
                    reason=reason,
                )
            )
            actions.append(f"position settled: {reason}")

    equity = Decimal(state["cash"]) + Decimal(state["quantity"]) * candle.close
    peak = max(Decimal(state["peak_equity"]), equity)
    state["peak_equity"] = str(peak)
    if equity <= peak * (1 - settings.drawdown_fraction):
        state["halted"] = True
    proposal = None
    turn = None
    if state["halted"]:
        decision = "new entries halted by drawdown limit"
    elif managed and sequence >= source.scored_index:
        decision, proposal, turn, decided = manage(
            source, sequence, state, transport, filled=bool(fills)
        )
        actions.extend(decided)
    elif state["position"] is not None:
        decision = "hold existing position"
    elif sequence < source.scored_index:
        decision = "warm-up bar: indicator history only"
    else:
        cutoff = candle.ts + timedelta(hours=1)
        snapshot = MarketSnapshot(
            cutoff,
            source.payload["source"],
            (
                SymbolSeries(
                    source.symbol,
                    "1h",
                    source.candles[max(0, sequence - HISTORY_BARS + 1) : sequence + 1],
                ),
            ),
        )
        ideas, refusal = propose(source.benchmark, snapshot)
        if ideas:
            idea = ideas[0]
            assert idea.exit_plan is not None
            assert idea.time_horizon.expires_at is not None
            proposal = idea.to_dict()
            state["pending"] = {
                "decision_id": idea.decision_id,
                "lower": str(idea.entry_zone.lower),
                "upper": str(idea.entry_zone.upper),
                "stop": str(idea.exit_plan.stop),
                "target": str(idea.exit_plan.target),
                "expires_at": idea.time_horizon.expires_at.isoformat(),
                "thesis": idea.thesis,
            }
            decision = f"{source.benchmark} rule: consider entry at next open"
        elif refusal is not None:
            decision = f"{source.benchmark} {refusal}"
        elif sequence < HISTORY_BARS - 1:
            decision = "collecting benchmark history"
        else:
            decision = f"no {source.benchmark} signal"
    event = {
        "sequence": sequence,
        "bar": source.payload["candles"][sequence],
        "decision": decision,
        "proposal": proposal,
        "actions": actions,
        "fills": fills,
        "state": state,
    }
    if managed:
        event["turn"] = turn
    return event


def run_experiment(
    root: Path,
    source: ExperimentInput | None = None,
    *,
    max_bars: int | None = None,
    benchmark: str | None = None,
    transport: DecisionTransportProtocol | None = None,
) -> dict[str, Any]:
    """Create/resume one bound run, committing no more than max_bars new observations.

    ``transport`` serves the AI trade manager's new turns only; committed turns
    replay from the journal.
    """
    if max_bars is not None and max_bars < 1:
        raise ValueError("max_bars must be positive")
    connection = open_run(root, source)
    try:
        if benchmark is not None and read_source(connection).benchmark != benchmark:
            raise ExperimentIntegrityError(
                "Experiment is bound to a different benchmark; use a new experiment directory"
            )
        processed = 0
        while max_bars is None or processed < max_bars:
            connection.execute("BEGIN IMMEDIATE")
            try:
                saved = read_source(connection)
                events = read_events(connection, saved)
                reconcile(saved, events)
                sequence = len(events)
                if sequence == len(saved.candles):
                    connection.commit()
                    break
                state = events[-1]["state"] if events else initial_state(saved)
                event = advance(saved, sequence, state, transport)
                event["previous"] = semantic_checksum(events[-1]) if events else saved.identity
                reconcile(saved, [*events, event])
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?)",
                    (sequence, encode(event), semantic_checksum(event)),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            processed += 1
    finally:
        connection.close()
    return inspect_run(root)
