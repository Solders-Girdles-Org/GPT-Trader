"""Portfolio-level rulebook author, with deterministic contexts and strict replies.

No client is created here. One turn covers all symbols in one cost case. Daily
bars are aggregated only from closed, complete 24-hour UTC days; hourly engine
rules continue to use hourly bars. Transport settings are explicit inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from gpt_trader.features.experiment.inputs import ExperimentInput, ManagerSettings
from gpt_trader.features.experiment.ledger import encode
from gpt_trader.features.experiment.model_transport import (
    input_token_bound,
    wire_params,
    worst_case_usd,
)
from gpt_trader.features.experiment.rulebook import Rulebook, parse_rulebook
from gpt_trader.features.experiment.trade_manager import (
    DecisionTransportProtocol,
    TransportError,
    TransportReply,
)

INSTRUCTIONS = """You author hourly trading rules for simulated USD spot accounts.
One call covers all symbols of one cost case. Use only the closed daily bars,
accounts, costs and current rules supplied. Choose trend, mean-reversion, hold
or cash; return a complete rulebook for EACH symbol, next version exactly.
Rules execute at this closed hourly bar; fills are at the next open. Existing
positions keep their protective stop, target and expiry. Risk, exposure and drawdown follow the supplied limits (normally 1%, 20%
and 10% respectively); expiry is at most 720 hours. Holding at the cap needs a sufficiently tight
stop; it is not equivalent to passive holding. Costs apply on both sides.
You may keep parameters or rewrite them. Never write code or call tools.
Reply only with JSON: {"rulebooks": {"SYMBOL-USD": <complete next rulebook>}}.
The current rulebooks show every required field. Decimal fields stay strings.
How you are judged, after the window in the context closes, for each cost case:
the sum over all symbols of each account's net P&L, with anything still held
valued at its sale value after costs. It must beat each of these, summed over
the same symbols, by more than one round trip's costs at the exposure cap per
symbol: staying in cash; buying each coin at the window's first open at the
exposure cap and holding it; and the fixed mean-reversion rule (and the
moving-average crossover rule, if it survives costs on this window).
"""


@dataclass(frozen=True)
class AuthorSettings:
    cadence_bars: int = 168
    history_days: int = 30

    def __post_init__(self) -> None:
        for name, upper in (("cadence_bars", 720), ("history_days", 30)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f"Author {name} must be an integer from 1 to {upper}")


AUTHOR_PROTOCOL_KEYS = frozenset(
    {"starting_rulebook", "cadence_bars", "history_days", "model", "effort", "max_tokens"}
)


def protocol_author(
    raw: Any, cap: Decimal
) -> tuple[AuthorSettings, ManagerSettings, dict[str, Any]]:
    """A protocol's frozen ``author`` block: cadence, transport settings, starting rules."""
    if not isinstance(raw, dict) or set(raw) != AUTHOR_PROTOCOL_KEYS:
        raise ValueError(
            "Protocol author settings require exactly " + ", ".join(sorted(AUTHOR_PROTOCOL_KEYS))
        )
    rules = parse_rulebook(raw["starting_rulebook"], cap)
    if rules.version != 1:
        raise ValueError("The protocol's starting rulebook must be version 1")
    return (
        AuthorSettings(raw["cadence_bars"], raw["history_days"]),
        ManagerSettings(model=raw["model"], effort=raw["effort"], max_tokens=raw["max_tokens"]),
        rules.to_dict(),
    )


def validate_portfolio(sources: list[ExperimentInput]) -> None:
    if not sources or len(sources) > 8 or len({s.symbol for s in sources}) != len(sources):
        raise ValueError("Supply one to eight distinct symbols in one cost case")
    first = sources[0]
    for source in sources:
        if source.benchmark != "ai-rulebook" or len(source.payload["rulebooks"]) != 1:
            raise ValueError("Author inputs require ai-rulebook with one starting version")
        if (
            source.scored_index != first.scored_index
            or source.settings != first.settings
            or [c.ts for c in source.candles] != [c.ts for c in first.candles]
        ):
            raise ValueError("Portfolio must share bar times, scored window and settings")


def reply_schema(symbols: list[str]) -> dict[str, Any]:
    fields = {
        name: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string"
        }
        for name, value in Rulebook().to_dict().items()
    }
    rule = {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }
    # One shared definition: the rule inlined once per coin compiles to a grammar
    # past the API's size limit (HTTP 400), found by the #1367 smoke.
    return {
        "type": "object",
        "$defs": {"rulebook": rule},
        "properties": {
            "rulebooks": {
                "type": "object",
                "properties": {s: {"$ref": "#/$defs/rulebook"} for s in sorted(symbols)},
                "required": sorted(symbols),
                "additionalProperties": False,
            }
        },
        "required": ["rulebooks"],
        "additionalProperties": False,
    }


def daily_bars(source: ExperimentInput, sequence: int, days: int) -> list[dict[str, str]]:
    groups: dict[str, list[Any]] = {}
    for bar in source.candles[: sequence + 1]:
        groups.setdefault(bar.ts.date().isoformat(), []).append(bar)
    result = []
    for bars in groups.values():
        if len(bars) != 24 or bars[0].ts.hour != 0:
            continue
        result.append(
            {
                "ts": bars[0].ts.isoformat(),
                "open": str(bars[0].open),
                "high": str(max(b.high for b in bars)),
                "low": str(min(b.low for b in bars)),
                "close": str(bars[-1].close),
                "volume": str(sum((b.volume for b in bars), Decimal(0))),
            }
        )
    return result[-days:]


def build_author_request(
    sources: list[ExperimentInput],
    sequence: int,
    accounts: dict[str, Any],
    rules: dict[str, dict[str, Any]],
    settings: AuthorSettings,
) -> dict[str, Any]:
    first = sources[0]
    return {
        "instructions": INSTRUCTIONS,
        "context": {
            "schema": "rulebook-author/2",
            "symbol": "portfolio:" + ",".join(sorted(rules)),
            "window": {
                "scored_from": first.candles[first.scored_index].ts.isoformat(),
                "cutoff": (first.candles[-1].ts + timedelta(hours=1)).isoformat(),
            },
            "decision_time": (first.candles[sequence].ts + timedelta(hours=1)).isoformat(),
            "costs": {k: str(getattr(first.settings, k)) for k in ("fee_bps", "slippage_bps")},
            "limits": first.settings.to_dict(),
            "accounts": accounts,
            "rulebooks": rules,
            "next_versions": {symbol: raw["version"] + 1 for symbol, raw in rules.items()},
            "daily_bars": {
                s.symbol: daily_bars(s, sequence, settings.history_days) for s in sources
            },
        },
    }


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate reply key")
        result[key] = value
    return result


def parse_author_reply(
    reply: TransportReply, current: dict[str, Any], cap: Decimal
) -> dict[str, Any]:
    if reply.stop_reason != "end_turn":
        raise ValueError(f"Author reply stopped: {reply.stop_reason}")
    if len(reply.text.encode()) > 100_000:
        raise ValueError("Author reply too large")
    raw = json.loads(reply.text, object_pairs_hook=_unique)
    if not isinstance(raw, dict) or set(raw) != {"rulebooks"}:
        raise ValueError("Author reply requires only rulebooks")
    proposed = raw["rulebooks"]
    if not isinstance(proposed, dict) or set(proposed) != set(current):
        raise ValueError("Author reply must cover every symbol exactly")
    result = {}
    for symbol, fields in proposed.items():
        rule = parse_rulebook(fields, cap)
        if rule.version != current[symbol]["version"] + 1:
            raise ValueError("Author version must increment by one")
        result[symbol] = rule.to_dict()
    return result


def author_turn(request: dict[str, Any], transport: DecisionTransportProtocol) -> dict[str, Any]:
    """Keep all previous rules on any failed/invalid reply; spend halts propagate."""
    current = request["context"]["rulebooks"]
    record: dict[str, Any] = {
        "request": json.loads(encode(request)),
        "model": transport.model,
        "reply": None,
        "error": None,
        "rulebooks": current,
        "accepted": False,
    }
    try:
        reply = transport.decide(request)
    except TransportError as error:
        record["error"] = str(error)
        return record
    record["reply"] = {
        "model": reply.model,
        "text": reply.text,
        "stop_reason": reply.stop_reason,
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "latency_ms": reply.latency_ms,
        "input_usd_per_million": str(reply.input_usd_per_million),
        "output_usd_per_million": str(reply.output_usd_per_million),
        "cost_usd": str(reply.cost_usd),
    }
    try:
        record["rulebooks"] = parse_author_reply(
            reply, current, Decimal(request["context"]["limits"]["exposure_fraction"])
        )
        record["accepted"] = True
    except (ValueError, TypeError, RecursionError) as error:
        record["error"] = str(error)
    return record


def estimate_author_spend(
    sources: list[ExperimentInput],
    manager: ManagerSettings,
    settings: AuthorSettings = AuthorSettings(),
) -> dict[str, Any]:
    """Measure closed-bar contexts offline. Bound accounts/rules at explicit byte caps.

    Unlike real author turns this never simulates trades or invokes a transport.
    Unknown future accounts/rules are reserved at the runner's enforced byte cap.
    """
    validate_portfolio(sources)
    rules = {s.symbol: s.payload["rulebooks"][0]["rules"] for s in sources}
    bounds, contexts, costs = [], [], []
    for sequence in range(sources[0].scored_index, len(sources[0].candles), settings.cadence_bars):
        request = build_author_request(sources, sequence, {}, rules, settings)
        # Enforced in the runner before transport. Includes account state and rules.
        request["context"]["accounts"] = "X" * MAX_DYNAMIC_BYTES
        request["context"]["rulebooks"] = {}
        request["context"]["next_versions"] = {symbol: 10000 for symbol in rules}
        params = wire_params(request, manager, reply_schema(list(rules)))
        contexts.append(len(encode(request["context"]).encode()))
        bounds.append(input_token_bound(params))
        costs.append(worst_case_usd(params))
    return {
        "kind": "offline-rulebook-author-bound",
        "symbols": sorted(rules),
        "cost_cases": 1,
        "turns": len(bounds),
        "cadence_bars": settings.cadence_bars,
        "history_days": settings.history_days,
        "model": manager.model,
        "effort": manager.effort,
        "max_tokens": manager.max_tokens,
        "dynamic_bytes_cap": MAX_DYNAMIC_BYTES,
        "context_bytes_max": max(contexts),
        "input_token_bound_max": max(bounds),
        "worst_case_usd": str(sum(costs, Decimal(0))),
        "includes_retries": False,
        "real_tokens_measured": False,
    }


# Shared byte limit on accounts and rulebooks, measured before sending. No
# truncation: exceeding it halts and requires design reconsideration.
MAX_DYNAMIC_BYTES = 16000
