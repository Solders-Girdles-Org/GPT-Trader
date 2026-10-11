"""Bounded spot simulation: observe a closed bar, decide, fill on the next bar.

No broker, network, scheduler, environment profile or live authority is accepted.
Every bar and all its effects commit together. Interruptions replay the uncommitted
bar; committed bars are never submitted again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Protocol

from gpt_trader.core.fill_accounting import FillFact, aware_time, semantic_checksum
from gpt_trader.features.experiment.inputs import BENCHMARK_HISTORY_BARS, ExperimentInput
from gpt_trader.features.experiment.ledger import (
    encode,
    initial_state,
    inspect_run,
    open_run,
    read_events,
    read_source,
    reconcile,
)
from gpt_trader.features.trade_ideas.baseline import BaselineProposer
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot, SymbolSeries


class BenchmarkProposer(Protocol):
    """A deterministic rule over a point-in-time snapshot (``Proposer`` contract)."""

    def propose(self, snapshot: MarketSnapshot) -> Sequence[Any]: ...


# Rule name -> fresh proposer. The slice composes only the baseline it owns;
# rules from other slices (mean reversion through the snapshot-strategy
# wrapper) are injected by the composition root, so this slice never imports
# runtime strategy code. A run whose bound rule is not composed fails closed.
BenchmarkCatalog = Mapping[str, Callable[[], BenchmarkProposer]]
DEFAULT_BENCHMARKS: BenchmarkCatalog = {"baseline": BaselineProposer}
WARM_UP_DECISION = "warm-up: indicator history only"
_DECISION_LABELS = {
    "baseline": ("rule-based crossover: consider entry at next open", "no crossover signal"),
    "mean-reversion": ("mean reversion: consider entry at next open", "no mean-reversion signal"),
}


def _proposer(source: ExperimentInput, proposers: BenchmarkCatalog | None) -> BenchmarkProposer:
    factory = (proposers or DEFAULT_BENCHMARKS).get(source.benchmark)
    if factory is None:
        raise ValueError(
            f"Benchmark {source.benchmark} is not composed here; "
            "run it through `gpt-trader experiment`"
        )
    return factory()


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
    proposers: BenchmarkCatalog | None = None,
) -> dict[str, Any]:
    """Pure observation transition. Strategy sees only bars closed at this cutoff."""
    state = deepcopy(previous_state)
    candle = source.candles[sequence]
    if sequence < source.scored_index:
        # Warm-up bars are indicator history only: no decision, fill or mark.
        return {
            "sequence": sequence,
            "bar": source.payload["candles"][sequence],
            "decision": WARM_UP_DECISION,
            "proposal": None,
            "actions": [],
            "fills": [],
            "state": state,
        }
    settings = source.settings
    slip = settings.slippage_bps / 10000
    fee = settings.fee_bps / 10000
    fills = []
    actions = []
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
                cash * settings.exposure_fraction / price,
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
    if state["halted"]:
        decision = "new entries halted by drawdown limit"
    elif state["position"] is not None:
        decision = "hold existing position"
    else:
        cutoff = candle.ts + timedelta(hours=1)
        history = BENCHMARK_HISTORY_BARS[source.benchmark]
        snapshot = MarketSnapshot(
            cutoff,
            source.payload["source"],
            (
                SymbolSeries(
                    source.symbol,
                    "1h",
                    source.candles[max(0, sequence - history + 1) : sequence + 1],
                ),
            ),
        )
        ideas = _proposer(source, proposers).propose(snapshot)
        entry_label, idle_label = _DECISION_LABELS[source.benchmark]
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
            decision = entry_label
        else:
            decision = idle_label if sequence >= history - 1 else "collecting benchmark history"
    return {
        "sequence": sequence,
        "bar": source.payload["candles"][sequence],
        "decision": decision,
        "proposal": proposal,
        "actions": actions,
        "fills": fills,
        "state": state,
    }


def run_experiment(
    root: Path,
    source: ExperimentInput | None = None,
    *,
    max_bars: int | None = None,
    proposers: BenchmarkCatalog | None = None,
) -> dict[str, Any]:
    """Create/resume one bound run, committing no more than max_bars new observations."""
    if max_bars is not None and max_bars < 1:
        raise ValueError("max_bars must be positive")
    connection = open_run(root, source)
    try:
        processed = 0
        while max_bars is None or processed < max_bars:
            connection.execute("BEGIN IMMEDIATE")
            try:
                saved = read_source(connection)
                events = read_events(connection, saved, proposers)
                reconcile(saved, events)
                sequence = len(events)
                if sequence == len(saved.candles):
                    connection.commit()
                    break
                state = events[-1]["state"] if events else initial_state(saved)
                event = advance(saved, sequence, state, proposers)
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
    return inspect_run(root, proposers)
