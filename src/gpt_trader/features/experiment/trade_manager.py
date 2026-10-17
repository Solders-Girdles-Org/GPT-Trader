"""AI trade manager channel: one structured decision per turn, through an injected transport.

A decision turn sends a deterministic, versioned request (closed bars up to the
decision bar, account, costs, limits and the manager's own rulebook) to a
transport and reads one decision back: hold (stay out, or keep the position),
enter at the next open with a size inside the exposure cap and a stop, target
and expiry, or exit at the next open. A reply may also rewrite the rulebook.
The engine's rails are unchanged: next-open fills, fees and slippage, the
exposure cap, stop-risk sizing, the drawdown halt and the stop placed at entry.

Each turn's evidence (model id, exact request, raw response, usage, latency,
pricing, computed cost, outcome and rulebook version) is journaled in its
event. Replay and resume rebuild every request and re-derive every decision
from the recorded reply through ``RecordedTransport``; a committed turn is never
sent again. A failed, refused or malformed reply makes no trade and records why.

No order is ever pending at a decision turn: an entry or exit decided on one bar
fills or lapses at the next bar's open, before that bar's turn. The request
therefore carries no pending-order field.

This module holds no model client; the transport is injected. The real one,
with its spend ceiling, is ``model_transport.AnthropicTransport``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from gpt_trader.features.experiment.inputs import (
    MANAGER_CHANNEL,
    MAX_RULEBOOK_CHARS,
    ExperimentInput,
)
from gpt_trader.features.experiment.ledger import ExperimentIntegrityError, encode

CONTEXT_VERSION = "ai-trade-manager-context/1"
MAX_REASON_CHARS = 500
MAX_EXPIRY_HOURS = 720
# Transport stop reasons that mean the model declined to decide.
REFUSAL_STOP_REASONS = frozenset({"refusal"})

INSTRUCTIONS = f"""You manage one simulated USD spot account trading {{symbol}} on hourly bars.
You decide alone; no person approves your trades. The simulator fills any entry or
exit at the next bar's open with the fees and slippage in the context, sizes an
entry no larger than the exposure cap and the stop risk allow, halts new entries
after the drawdown limit, and keeps the stop you set at entry as a hard exit.
You see only bars that have closed. Holding cash and keeping a position are
normal choices; every round trip pays the costs twice.

Reply with one JSON object and nothing else:
{{{{
  "action": "hold" | "enter" | "exit",
  "reason": "<at most {MAX_REASON_CHARS} characters>",
  "rulebook": "<optional: your rewritten rulebook, at most {MAX_RULEBOOK_CHARS} characters>",
  "size_fraction": "<enter only: fraction of equity to commit, above 0>",
  "stop": "<enter only: price below the last close>",
  "target": "<enter only: price above the last close>",
  "expires_in_hours": <enter only: whole hours from 1 to {MAX_EXPIRY_HOURS}>
}}}}
"hold" stays out while flat and keeps the position while holding. "enter" is
valid only while flat and "exit" only while holding. Your rulebook is yours to
keep: it is sent back to you on every turn, and you may rewrite it on any turn."""


class TransportError(Exception):
    """The transport could not produce a reply; the message is recorded as the reason."""


class TransportUnavailableError(ValueError):
    """A decision turn is due but the run was given no transport."""


class MalformedReplyError(ValueError):
    """A reply that is not one valid decision for the current account."""


@dataclass(frozen=True)
class TransportReply:
    model: str
    text: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ValueError("Transport reply requires a model id")
        if not isinstance(self.text, str) or not isinstance(self.stop_reason, str):
            raise ValueError("Transport reply text and stop reason must be strings")
        for name in ("input_tokens", "output_tokens", "latency_ms"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"Transport reply {name} must be a nonnegative integer")
        for name in ("input_usd_per_million", "output_usd_per_million"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"Transport reply {name} must be a nonnegative Decimal")

    @property
    def cost_usd(self) -> Decimal:
        return (
            self.input_tokens * self.input_usd_per_million
            + self.output_tokens * self.output_usd_per_million
        ) / 1_000_000


class DecisionTransportProtocol(Protocol):
    """Sends one request to a model. Raise ``TransportError`` for a failed call."""

    model: str

    def decide(self, request: dict[str, Any]) -> TransportReply: ...


class RecordedTransport:
    """Replays a committed turn's reply; refuses a request that differs from the record."""

    def __init__(self, turn: dict[str, Any] | None) -> None:
        self.turn = turn
        self.model = "" if turn is None else str(turn["model"])

    def decide(self, request: dict[str, Any]) -> TransportReply:
        if self.turn is None:
            raise ExperimentIntegrityError("Replay requires a model turn the journal lacks")
        if json.loads(encode(request)) != self.turn["request"]:
            raise ExperimentIntegrityError("Rebuilt model request differs from the journal")
        if self.turn["error"] is not None:
            raise TransportError(self.turn["error"])
        pricing = self.turn["pricing"]
        return TransportReply(
            model=self.model,
            text=self.turn["response"]["text"],
            stop_reason=self.turn["response"]["stop_reason"],
            input_tokens=self.turn["usage"]["input_tokens"],
            output_tokens=self.turn["usage"]["output_tokens"],
            latency_ms=self.turn["latency_ms"],
            input_usd_per_million=Decimal(pricing["input_usd_per_million"]),
            output_usd_per_million=Decimal(pricing["output_usd_per_million"]),
        )


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    rulebook: str | None = None
    size_fraction: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    expires_in_hours: int | None = None


def build_request(source: ExperimentInput, sequence: int, state: dict[str, Any]) -> dict[str, Any]:
    """The full model request; a pure function of the bound input and pre-decision state."""
    assert source.manager is not None
    settings = source.settings
    candle = source.candles[sequence]
    quantity = Decimal(state["quantity"])
    position = state["position"]
    first = max(0, sequence - source.manager.history_bars + 1)
    context = {
        "symbol": source.symbol,
        "decision_time": (candle.ts + timedelta(hours=1)).isoformat(),
        "bars": source.payload["candles"][first : sequence + 1],
        "account": {
            "cash": state["cash"],
            "quantity": state["quantity"],
            "equity_at_last_close": str(Decimal(state["cash"]) + quantity * candle.close),
            "position": None
            if position is None
            else {
                "cost_basis_with_fee": state["cost_basis"],
                "unrealized_net_at_last_close": str(
                    quantity * candle.close - Decimal(state["cost_basis"])
                ),
                "stop": position["stop"],
                "target": position["target"],
                "expires_at": position["expires_at"],
            },
        },
        "costs": {
            "fee_bps": str(settings.fee_bps),
            "slippage_bps": str(settings.slippage_bps),
            "round_trip_bps": str(2 * (settings.fee_bps + settings.slippage_bps)),
        },
        "limits": {
            "exposure_fraction": str(settings.exposure_fraction),
            "stop_risk_fraction": str(settings.risk_fraction),
            "drawdown_halt_fraction": str(settings.drawdown_fraction),
            "peak_equity": state["peak_equity"],
        },
        "rulebook": {
            "version": state["manager"]["rulebook_version"],
            "text": state["manager"]["rulebook"],
        },
    }
    return {
        "context_version": CONTEXT_VERSION,
        "instructions": INSTRUCTIONS.format(symbol=source.symbol),
        "context": context,
    }


def _price(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | str | Decimal):
        raise MalformedReplyError(f"{name} must be a number")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise MalformedReplyError(f"{name} must be a number") from None
    if not number.is_finite() or number <= 0:
        raise MalformedReplyError(f"{name} must be above 0")
    return number


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise MalformedReplyError("reply repeats a field")
    return dict(pairs)


def parse_reply(text: str, *, holding: bool, last_close: Decimal) -> Decision:
    """Accept exactly one valid decision for the current account, or raise why not."""
    try:
        reply = json.loads(text, parse_float=Decimal, object_pairs_hook=_unique_keys)
    except MalformedReplyError:
        raise
    except ValueError:
        raise MalformedReplyError("reply is not one JSON object") from None
    if not isinstance(reply, dict):
        raise MalformedReplyError("reply is not one JSON object")
    entry_keys = {"size_fraction", "stop", "target", "expires_in_hours"}
    unknown = set(reply) - {"action", "reason", "rulebook"} - entry_keys
    if unknown:
        raise MalformedReplyError(f"unknown reply fields: {', '.join(sorted(unknown))}")
    action, reason = reply.get("action"), reply.get("reason")
    if action not in ("hold", "enter", "exit"):
        raise MalformedReplyError("action must be hold, enter or exit")
    if not isinstance(reason, str) or not 0 < len(reason) <= MAX_REASON_CHARS:
        raise MalformedReplyError(f"reason must be 1 to {MAX_REASON_CHARS} characters")
    rulebook = reply.get("rulebook")
    if rulebook is not None and (
        not isinstance(rulebook, str) or not 0 < len(rulebook) <= MAX_RULEBOOK_CHARS
    ):
        raise MalformedReplyError(f"rulebook must be 1 to {MAX_RULEBOOK_CHARS} characters")
    if action != "enter":
        if entry_keys & set(reply):
            raise MalformedReplyError("entry fields are valid only with enter")
        if action == "exit" and not holding:
            raise MalformedReplyError("exit is valid only while holding a position")
        return Decision(action, reason, rulebook)
    if holding:
        raise MalformedReplyError("enter is valid only while flat")
    if not entry_keys <= set(reply):
        raise MalformedReplyError("enter requires size_fraction, stop, target and expires_in_hours")
    size_fraction = _price(reply["size_fraction"], "size_fraction")
    stop, target = _price(reply["stop"], "stop"), _price(reply["target"], "target")
    if not stop < last_close < target:
        raise MalformedReplyError("enter requires stop < last close < target")
    hours = reply["expires_in_hours"]
    if type(hours) is not int or not 1 <= hours <= MAX_EXPIRY_HOURS:
        raise MalformedReplyError(
            f"expires_in_hours must be a whole number from 1 to {MAX_EXPIRY_HOURS}"
        )
    return Decision(action, reason, rulebook, size_fraction, stop, target, hours)


def manage(
    source: ExperimentInput,
    sequence: int,
    state: dict[str, Any],
    transport: DecisionTransportProtocol | None,
    *,
    filled: bool,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Run a due decision turn and apply it to ``state``.

    Returns the decision label, the entry proposal (or None), the turn evidence
    (None between turns) and any actions to record.
    """
    assert source.manager is not None
    holding = state["position"] is not None
    due = filled or (sequence - source.scored_index) % source.manager.cadence_bars == 0
    if not due:
        return (
            "hold existing position" if holding else f"{MANAGER_CHANNEL}: between decision turns",
            None,
            None,
            [],
        )
    if transport is None:
        raise TransportUnavailableError(
            f"A {MANAGER_CHANNEL} decision turn is due at bar {sequence} but no model transport "
            "was supplied"
        )
    request = build_request(source, sequence, state)
    reply: TransportReply | None = None
    error: str | None = None
    decision: Decision | None = None
    try:
        reply = transport.decide(request)
    except TransportError as failure:
        error = str(failure) or type(failure).__name__
    if reply is None:
        outcome, why = "failed", f"transport failed: {error}"
    elif reply.stop_reason in REFUSAL_STOP_REASONS:
        outcome, why = "refused", f"model refused (stop reason {reply.stop_reason})"
    else:
        try:
            decision = parse_reply(
                reply.text, holding=holding, last_close=source.candles[sequence].close
            )
            outcome, why = "accepted", decision.reason
        except MalformedReplyError as malformed:
            outcome, why = "malformed", f"malformed reply: {malformed}"

    manager_state = state["manager"]
    actions: list[str] = []
    proposal = None
    rewritten = None
    if decision is None:
        label = f"{MANAGER_CHANNEL} {outcome} reply: no trade"
    else:
        if decision.rulebook is not None and decision.rulebook != manager_state["rulebook"]:
            rewritten = decision.rulebook
            manager_state["rulebook"] = rewritten
            manager_state["rulebook_version"] += 1
            actions.append(f"rulebook rewritten to version {manager_state['rulebook_version']}")
        if decision.action == "hold":
            label = f"{MANAGER_CHANNEL}: {'keep position' if holding else 'stay out'}"
        elif decision.action == "exit":
            manager_state["exit_pending"] = True
            label = f"{MANAGER_CHANNEL}: exit at next open"
        else:
            assert decision.size_fraction is not None and decision.expires_in_hours is not None
            cap = source.settings.exposure_fraction
            size = min(decision.size_fraction, cap)
            if decision.size_fraction > cap:
                actions.append(f"entry size clamped to the {cap} exposure cap")
            expires_at = source.candles[sequence].ts + timedelta(
                hours=1 + decision.expires_in_hours
            )
            proposal = {
                "decision_id": f"{source.identity[:16]}-{sequence}-manager",
                "lower": str(decision.stop),
                "upper": str(decision.target),
                "stop": str(decision.stop),
                "target": str(decision.target),
                "expires_at": expires_at.isoformat(),
                "size_fraction": str(size),
                "thesis": decision.reason,
            }
            state["pending"] = dict(proposal)
            label = f"{MANAGER_CHANNEL}: consider entry at next open"

    turn = {
        "model": reply.model if reply is not None else transport.model,
        "request": request,
        "response": None
        if reply is None
        else {"text": reply.text, "stop_reason": reply.stop_reason},
        "usage": None
        if reply is None
        else {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens},
        "latency_ms": None if reply is None else reply.latency_ms,
        "pricing": None
        if reply is None
        else {
            "input_usd_per_million": str(reply.input_usd_per_million),
            "output_usd_per_million": str(reply.output_usd_per_million),
        },
        "cost_usd": "0" if reply is None else str(reply.cost_usd),
        "error": error,
        "outcome": outcome,
        "reason": why,
        "rulebook_version": manager_state["rulebook_version"],
        "rulebook": rewritten,
    }
    return label, proposal, turn, actions
