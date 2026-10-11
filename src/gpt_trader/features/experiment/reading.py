"""The predeclared held-out reading, as code committed before any data is seen.

Thresholds come from the committed protocol file; the rules here mirror the
"Held-out protocol and predeclared reading" section of
``docs/decisions/held-out-benchmark-before-reasoning-proposer.md``, which wins
if they ever differ. Every supplied run is reported; none is dropped.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from gpt_trader.core.fill_accounting import aware_time

SURVIVING = {"survives", "survives-at-rj-fee"}


def _scored(report: dict[str, Any], protocol: dict[str, Any]) -> bool:
    # Complete, full-length, and ending at the cutoff: contiguity fixes the start.
    return bool(
        report["complete"]
        and report["total_bars"] == protocol["bars_per_symbol"]
        and aware_time(report["scored_from"]) == aware_time(protocol["scored_from"])
        and aware_time(report["end_valuation"]["mark_bar"]) + timedelta(hours=1)
        == aware_time(protocol["cutoff"])
    )


def case_outcome(
    reports: list[dict[str, Any]], protocol: dict[str, Any], *, rule: str, case: str
) -> dict[str, Any]:
    """Pool one rule under one cost case over its scored symbols."""
    costs = protocol["cost_cases"][case]
    symbols = [report["symbol"] for report in reports]
    if len(set(symbols)) != len(symbols) or not set(symbols) <= set(protocol["symbols"]):
        raise ValueError("Each protocol symbol may appear at most once per case")
    rows = []
    for report in reports:
        settings = report["settings"]
        if report["benchmark"] != rule or any(
            Decimal(settings[name]) != Decimal(value)
            for name, value in {**protocol["settings"], **costs}.items()
        ):
            raise ValueError(f"{report['symbol']} run does not match {rule} under {case} costs")
        valuation = report["end_valuation"]
        hold = valuation["comparators"]["hold_the_coin"] if valuation else None
        rows.append(
            {
                "symbol": report["symbol"],
                "scored": valuation is not None and hold is not None and _scored(report, protocol),
                "halted": report["halted"],
                "position_open_at_end": bool(valuation and valuation["position_open_at_end"]),
                "net_pnl": valuation["net_pnl"] if valuation else None,
                "hold_net_pnl": hold["net_pnl"] if hold else None,
                "round_trips": valuation["round_trips"] if valuation else 0,
                "fee_share_of_gain": valuation["fee_share_of_gain"] if valuation else None,
                "slippage_share_of_gain": valuation["slippage_share_of_gain"]
                if valuation
                else None,
            }
        )
    scored = [row for row in rows if row["scored"]]
    net = sum((Decimal(row["net_pnl"]) for row in scored), Decimal(0))
    hold = sum((Decimal(row["hold_net_pnl"]) for row in scored), Decimal(0))
    round_trips = sum(row["round_trips"] for row in scored)
    if len(scored) < protocol["min_scored_symbols"] or round_trips < protocol["min_round_trips"]:
        outcome = "inconclusive"
    elif net > 0:
        outcome = "pass" if net > hold else "positive-but-trails-hold"
    else:
        outcome = "loses-less-than-hold" if net > hold else "fail"
    return {
        "rule": rule,
        "case": case,
        "costs": costs,
        "outcome": outcome,
        "scored_symbols": len(scored),
        "round_trips": round_trips,
        "net_pnl": str(net),
        "hold_net_pnl": str(hold),
        "cash_net_pnl": "0",
        "symbols": rows,
    }


def rule_verdict(base: str, stress: str) -> str:
    """Combine a rule's two cost-case outcomes, in the predeclared order."""
    if "inconclusive" in (base, stress):
        return "inconclusive"
    passes = (base, stress).count("pass")
    return {2: "survives", 1: "cost-dependent"}.get(passes, "does-not-survive")


def exact_fee_verdict(outcome: str) -> str:
    """Read a single exact-fee follow-up case; Pass survives at RJ's fee."""
    if outcome == "inconclusive":
        return "inconclusive"
    return "survives-at-rj-fee" if outcome == "pass" else "does-not-survive"


def after_follow_up(verdict: str) -> str:
    """A rule still unresolved after its one follow-up (run or declined) does not count.

    It was not shown to survive; that is reported as such, not as a failure.
    """
    return "not-shown-to-survive" if verdict in {"inconclusive", "cost-dependent"} else verdict


def combined_action(mean_reversion: str, baseline: str) -> str:
    """First matching action A-D for the pair of rule verdicts."""
    verdicts = (mean_reversion, baseline)
    if any(verdict in SURVIVING for verdict in verdicts):
        return "A"
    if "cost-dependent" in verdicts:
        return "B"
    if "inconclusive" in verdicts:
        return "C"
    return "D"
