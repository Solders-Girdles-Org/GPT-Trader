"""Versioned, declarative rules evaluated on closed bars without a model.

Versions are frozen in the input, activate at a named decision bar, and never
change an existing position's protective plan. No code or transport is accepted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from gpt_trader.core.fill_accounting import aware_time

if TYPE_CHECKING:
    from gpt_trader.features.experiment.inputs import ExperimentInput

RULEBOOK_CHANNEL = "ai-rulebook"


@dataclass(frozen=True)
class Rulebook:
    schema_version: int = 1
    version: int = 1
    entry: str = "trend"
    fast_bars: int = 10
    slow_bars: int = 50
    reversion_bars: int = 20
    entry_z: str = "2"
    # Zero disables the extra close-above-average entry filter.
    trend_filter_bars: int = 0
    exposure_fraction: str = "0.20"
    stop_fraction: str = "0.04"
    target_fraction: str = "0.08"
    expiry_hours: int = 48
    exit_on_signal: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Agent-written mechanics starting point, not a tuned or measured strategy.
STARTING_RULEBOOK = Rulebook()


def parse_rulebook(raw: Any, cap: Decimal) -> Rulebook:
    if not isinstance(raw, dict) or set(raw) != set(Rulebook.__dataclass_fields__):
        raise ValueError("Rulebook requires exactly the schema fields")
    rule = Rulebook(**raw)
    for name, lower, upper in (
        ("schema_version", 1, 1),
        ("version", 1, 10000),
        ("fast_bars", 1, 499),
        ("slow_bars", 2, 500),
        ("reversion_bars", 2, 500),
        ("trend_filter_bars", 0, 500),
        ("expiry_hours", 1, 720),
    ):
        value = getattr(rule, name)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"Rulebook {name} must be an integer from {lower} to {upper}")
    if rule.fast_bars >= rule.slow_bars:
        raise ValueError("Rulebook fast_bars must be below slow_bars")
    if rule.entry not in ("trend", "mean-reversion", "hold", "cash"):
        raise ValueError("Unknown rulebook entry rule")
    if type(rule.exit_on_signal) is not bool:
        raise ValueError("Rulebook exit_on_signal must be boolean")
    normalized = rule.to_dict()
    for name, ceiling in (
        ("entry_z", Decimal(10)),
        ("exposure_fraction", cap),
        ("stop_fraction", Decimal("0.95")),
        ("target_fraction", Decimal(100)),
    ):
        value = getattr(rule, name)
        if not isinstance(value, str):
            raise ValueError(f"Rulebook {name} must be a decimal string")
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"Invalid rulebook {name}") from None
        if not number.is_finite() or not 0 < number <= ceiling:
            raise ValueError(f"Rulebook {name} must be in (0, {ceiling}]")
        normalized[name] = str(number)
    return Rulebook(**normalized)


def parse_versions(
    raw: Any, bars: list[dict[str, Any]], scored_index: int, cap: Decimal
) -> list[dict[str, Any]]:
    """Freeze an ordered schedule; activation is at a supplied scored bar's close."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2000:
        raise ValueError("Supply 1 to 2000 rulebook versions")
    times = {aware_time(bar["ts"]): index for index, bar in enumerate(bars)}
    result = []
    previous_index = scored_index - 1
    for version, item in enumerate(raw, 1):
        if not isinstance(item, dict) or set(item) != {"effective_from", "rules"}:
            raise ValueError("Rulebook version requires effective_from and rules")
        index = times.get(aware_time(item["effective_from"]))
        if index is None or index <= previous_index or (version == 1 and index != scored_index):
            raise ValueError(
                "Rulebook versions must start at scored_from and increase on scored bars"
            )
        rule = parse_rulebook(item["rules"], cap)
        if rule.version != version:
            raise ValueError("Rulebook versions must be consecutive starting at 1")
        result.append({"effective_from": bars[index]["ts"], "rules": rule.to_dict()})
        previous_index = index
    return result


def evaluate(
    source: ExperimentInput, sequence: int, state: dict[str, Any]
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Apply the active rule to state; return label, entry and activation journal."""
    candle = source.candles[sequence]
    active = source.payload["rulebooks"][0]
    for item in source.payload["rulebooks"][1:]:
        if aware_time(item["effective_from"]) > candle.ts:
            break
        active = item
    rule = Rulebook(**active["rules"])
    control = state["rulebook"]
    activation = active if control["version"] != rule.version else None
    control["version"] = rule.version
    closes = [bar.close for bar in source.candles[max(0, sequence - 500) : sequence + 1]]
    close = closes[-1]
    holding = state["position"] is not None
    needed = max(
        rule.slow_bars + 1 if rule.entry == "trend" else 1,
        rule.reversion_bars if rule.entry == "mean-reversion" else 1,
        rule.trend_filter_bars if not holding and rule.entry != "cash" else 0,
    )
    if len(closes) < needed:
        return "ai-rulebook: collecting history", None, activation

    def mean(values: list[Decimal]) -> Decimal:
        return sum(values, Decimal(0)) / len(values)

    enter, exit_ = False, False
    if rule.entry == "cash":
        exit_ = True
    elif rule.entry == "hold":
        enter = True
    elif rule.entry == "trend":
        fast, slow = mean(closes[-rule.fast_bars :]), mean(closes[-rule.slow_bars :])
        prior_fast = mean(closes[-rule.fast_bars - 1 : -1])
        prior_slow = mean(closes[-rule.slow_bars - 1 : -1])
        enter = fast > slow and prior_fast <= prior_slow
        exit_ = fast <= slow
    else:
        window = closes[-rule.reversion_bars :]
        average = mean(window)
        deviation = mean([(value - average) ** 2 for value in window]).sqrt()
        enter = deviation > 0 and close <= average - Decimal(rule.entry_z) * deviation
        exit_ = close >= average
    if holding:
        if exit_ and (rule.entry == "cash" or rule.exit_on_signal):
            control["exit_pending"] = True
            return "ai-rulebook: exit at next open", None, activation
        return "ai-rulebook: keep position", None, activation
    if state["halted"]:
        return "new entries halted by drawdown limit", None, activation
    if rule.trend_filter_bars and close <= mean(closes[-rule.trend_filter_bars :]):
        enter = False
    if not enter:
        return "ai-rulebook: stay in cash", None, activation
    stop = close * (1 - Decimal(rule.stop_fraction))
    target = close * (1 + Decimal(rule.target_fraction))
    proposal = {
        "decision_id": f"{source.identity[:16]}-{sequence}-rulebook-{rule.version}",
        "lower": str(stop),
        "upper": str(target),
        "stop": str(stop),
        "target": str(target),
        "expires_at": (candle.ts + timedelta(hours=1 + rule.expiry_hours)).isoformat(),
        "size_fraction": rule.exposure_fraction,
        "thesis": f"ai-rulebook v{rule.version}: {rule.entry}",
    }
    state["pending"] = dict(proposal)
    return "ai-rulebook: consider entry at next open", proposal, activation
