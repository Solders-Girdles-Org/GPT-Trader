"""Committed held-out benchmark protocol and its predeclared reading (#1315).

Authority: ``docs/decisions/held-out-benchmark-before-reasoning-proposer.md``,
section "Held-out protocol and predeclared reading". If this module and the
record ever differ, the record wins and this module is the bug. Committed
before any held-out data is fetched; later changes may add mechanics but must
not loosen a threshold. Because every ``.py`` file under ``src/gpt_trader`` is
part of each run's bound implementation digest, a run's identity pins the
protocol it was read under.

This module only reads results. It fetches nothing and starts no run; the
held-out run needs RJ's explicit go.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.inputs import BENCHMARK_HISTORY_BARS, Settings

PROTOCOL_ID = "held-out-benchmark-v1"

# Window and data: the old job's symbol set, chosen before any return is seen.
SYMBOLS = tuple(
    f"{coin}-USD" for coin in ("BTC", "ETH", "LTC", "SOL", "XRP", "LINK", "AVAX", "DOT")
)
WARMUP_START = datetime(2026, 9, 3, tzinfo=UTC)
SCORED_FROM = datetime(2026, 9, 6, tzinfo=UTC)
CUTOFF = datetime(2026, 10, 11, tzinfo=UTC)
WARMUP_BARS = 72
SCORED_BARS = 840

# Rules (default settings, no tuning) and the two cost assumptions.
RULES = ("baseline", "mean-reversion")
COST_CASES: dict[str, tuple[Decimal, Decimal]] = {
    "base": (Decimal(10), Decimal(5)),
    "stress": (Decimal(60), Decimal(10)),
}
EXACT_FEE_SLIPPAGE_BPS = Decimal(5)

# Coverage floor: a minimum, not a significance test.
MIN_SCORED_SYMBOLS = 7
MIN_ROUND_TRIPS = 30


def case_settings(cost_case: str | None = None, *, fee_bps: Decimal | None = None) -> Settings:
    """Account for one case: $1,000, 1% stop risk, 20% exposure cap, 10% drawdown halt.

    Pass ``cost_case`` for base or stress, or ``fee_bps`` alone for a separately
    approved exact-fee run (5 bps slippage, otherwise the same protocol).
    """
    if (cost_case is None) == (fee_bps is None):
        raise ValueError("Choose a cost case or an exact fee")
    if cost_case is not None:
        if cost_case not in COST_CASES:
            raise ValueError(f"Unknown cost case {cost_case}; choose one of {sorted(COST_CASES)}")
        fee, slippage = COST_CASES[cost_case]
    else:
        assert fee_bps is not None
        fee, slippage = fee_bps, EXACT_FEE_SLIPPAGE_BPS
    return Settings(
        initial_cash=Decimal(1000),
        risk_fraction=Decimal("0.01"),
        exposure_fraction=Decimal("0.20"),
        drawdown_fraction=Decimal("0.10"),
        fee_bps=fee,
        slippage_bps=slippage,
    )


def case_input(market: dict[str, Any], rule: str, cost_case: str) -> dict[str, Any]:
    """Bind one rule and cost case to shared fingerprinted market data."""
    if rule not in RULES:
        raise ValueError(f"Unknown rule {rule}; choose one of {RULES}")
    return {**market, "benchmark": rule, "settings": case_settings(cost_case).to_dict()}


def is_scored(report: dict[str, Any]) -> bool:
    """Complete 912-bar fingerprinted protocol input, and every bar processed.

    Runs that halt or never trade are still scored.
    """
    window = report["window"]
    return (
        report["complete"]
        and report["symbol"] in SYMBOLS
        and report["benchmark"] in RULES
        and report["provenance"] is not None
        and aware_time(window["first_bar"]) == WARMUP_START
        and aware_time(window["scored_from"]) == SCORED_FROM
        and aware_time(window["recorded_at"]) == CUTOFF
        and window["warmup_bars"] == WARMUP_BARS
        and window["scored_bars"] == SCORED_BARS
    )


class Outcome(StrEnum):
    INCONCLUSIVE = "inconclusive"
    PASS = "pass"
    POSITIVE_TRAILS_HOLD = "positive but trails hold-the-coin"
    LOSES_LESS_THAN_HOLD = "loses less than hold-the-coin"
    FAIL = "fail"


class Verdict(StrEnum):
    INCONCLUSIVE = "inconclusive"
    SURVIVES = "survives costs"
    COST_DEPENDENT = "cost-dependent"
    DOES_NOT_SURVIVE = "does not survive"


class Action(StrEnum):
    A = "A: propose a forward test"
    B = "B: exact-fee run"
    C = "C: extension"
    D = "D: fund-or-stop choice"


def case_outcome(
    *, scored_symbols: int, round_trips: int, net_pnl: Decimal, hold_pnl: Decimal
) -> Outcome:
    """One rule under one cost case, on pooled net P&L at end valuation.

    ``round_trips`` counts entries closed by stop, target or expiry before the
    cutoff, pooled over scored symbols; ``hold_pnl`` pools hold-the-coin over
    exactly the same scored symbols.
    """
    if scored_symbols < MIN_SCORED_SYMBOLS or round_trips < MIN_ROUND_TRIPS:
        return Outcome.INCONCLUSIVE
    if net_pnl > 0:
        return Outcome.PASS if net_pnl > hold_pnl else Outcome.POSITIVE_TRAILS_HOLD
    return Outcome.LOSES_LESS_THAN_HOLD if net_pnl > hold_pnl else Outcome.FAIL


def rule_verdict(base: Outcome, stress: Outcome) -> Verdict:
    """Combine a rule's two cost cases, checked in the record's order."""
    if Outcome.INCONCLUSIVE in (base, stress):
        return Verdict.INCONCLUSIVE
    passes = (base, stress).count(Outcome.PASS)
    if passes == 2:
        return Verdict.SURVIVES
    if passes == 1:
        # Never counts as surviving; only an exact-fee run can change it.
        return Verdict.COST_DEPENDENT
    return Verdict.DOES_NOT_SURVIVE


def exact_fee_verdict(outcome: Outcome) -> Verdict:
    """An exact-fee run is one case: Pass survives at RJ's fee, other conclusive fails."""
    if outcome is Outcome.INCONCLUSIVE:
        return Verdict.INCONCLUSIVE
    return Verdict.SURVIVES if outcome is Outcome.PASS else Verdict.DOES_NOT_SURVIVE


FollowUp = Verdict | Literal["declined"] | None


@dataclass(frozen=True)
class ReadingVerdict:
    verdict: Verdict
    wording: str
    follow_up_used: bool


def reading_verdict(original: Verdict, follow_up: FollowUp = None) -> ReadingVerdict:
    """A rule's verdict for the combined reading, after at most one follow-up.

    ``follow_up`` is the follow-up run's verdict (``exact_fee_verdict`` for an
    exact-fee run, ``rule_verdict`` for an extension), ``"declined"`` when RJ
    declines it, or ``None`` before any follow-up. A declined follow-up counts
    as the rule's one follow-up. "Survives at RJ's fee" counts as survives for
    action A and the forward-test bar. A rule still inconclusive or
    cost-dependent after its follow-up is treated as does-not-survive; if it
    stayed inconclusive it is reported as "was not shown to survive" (with its
    coverage), not "does not survive".
    """
    if follow_up is None:
        return ReadingVerdict(original, original.value, False)
    if original in (Verdict.SURVIVES, Verdict.DOES_NOT_SURVIVE):
        raise ValueError("A conclusive verdict gets no follow-up run")
    last = original if follow_up == "declined" else follow_up
    if last is Verdict.SURVIVES:
        wording = (
            "survives at RJ's fee" if original is Verdict.COST_DEPENDENT else Verdict.SURVIVES.value
        )
        return ReadingVerdict(Verdict.SURVIVES, wording, True)
    wording = (
        "was not shown to survive"
        if last is Verdict.INCONCLUSIVE
        else Verdict.DOES_NOT_SURVIVE.value
    )
    return ReadingVerdict(Verdict.DOES_NOT_SURVIVE, wording, True)


def combined_action(mean_reversion: Verdict, baseline: Verdict) -> Action:
    """The first matching action; claims stay limited to these rules, costs and window."""
    verdicts = (mean_reversion, baseline)
    if Verdict.SURVIVES in verdicts:
        return Action.A
    if Verdict.COST_DEPENDENT in verdicts:
        return Action.B
    if Verdict.INCONCLUSIVE in verdicts:
        return Action.C
    return Action.D


def follow_up_for(action: Action, verdict: Verdict, *, follow_up_used: bool) -> str | None:
    """The one follow-up a rule is offered under an action, if any."""
    if follow_up_used or action in (Action.A, Action.D):
        return None
    if action is Action.B and verdict is Verdict.COST_DEPENDENT:
        return "exact-fee"
    if verdict is Verdict.INCONCLUSIVE:
        return "extension"
    return None


def forward_test_bar(baseline: Verdict) -> tuple[str, ...]:
    """What a forward reasoning test must beat; baseline joins when it survives costs."""
    bar = ("mean-reversion", "cash", "hold-the-coin")
    return (*bar, "baseline") if baseline is Verdict.SURVIVES else bar


# The protocol's warm-up must cover every rule's declared history.
assert all(WARMUP_BARS >= BENCHMARK_HISTORY_BARS[rule] - 1 for rule in RULES)
