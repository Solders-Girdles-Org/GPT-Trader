"""The predeclared round-two reading of the AI trade manager, committed before its window.

Thresholds come from the committed protocol file; the rules here mirror
``config/experiments/round-two-2026-10-20/PROTOCOL.md`` and the "Round two"
section of ``docs/decisions/next-round-after-held-out-benchmark.md``, which win
if they ever differ. Every supplied run is reported; none is dropped.

Each cost case pools the manager's net P&L at liquidation value over the
symbols scored for the manager and for every rule in the bar, and compares it
with cash, hold-the-coin and each such rule on exactly those symbols. A
manager symbol is scored when its run is complete, full length, ends at the
cutoff, was decided only by the pinned model under the frozen settings, and
lost no more than the protocol's share of its turns to failed calls. There is
no round-trip minimum for the manager. A pass must beat the best comparator by
more than one round trip's costs at the exposure cap on every compared symbol,
so a channel that never trades, or trades once for a few cents, cannot pass.

Every protocol symbol must appear once per channel and cost case: as a run's
report, or as ``{"symbol": ..., "not_run": "<why>"}`` when no run exists (a
refused fetch, or an account the spend stop never started). Every run must be
bound to the frozen source digest named on the card.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from gpt_trader.features.experiment.inputs import MANAGER_CHANNEL, ManagerSettings
from gpt_trader.features.experiment.model_transport import same_model
from gpt_trader.features.experiment.reading import _scored, case_outcome, rule_verdict

BAR_RULES = ("mean-reversion", "baseline")


def not_run_row(entry: dict[str, Any]) -> dict[str, Any]:
    """An unscored row for a protocol symbol that has no run."""
    return {"symbol": entry["symbol"], "scored": False, "not_run": entry["not_run"]}


def split_entries(
    entries: list[dict[str, Any]], protocol: dict[str, Any], *, label: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate reports from not-run entries; refuse any symbol missing or repeated."""
    symbols = sorted(entry["symbol"] for entry in entries)
    if symbols != sorted(protocol["symbols"]):
        raise ValueError(
            f"{label} must name every protocol symbol exactly once, as a run or as not run"
        )
    not_run = [entry for entry in entries if "not_run" in entry]
    for entry in not_run:
        if set(entry) != {"symbol", "not_run"} or not str(entry["not_run"]).strip():
            raise ValueError(f"{label}: a not-run entry needs only its symbol and why")
    return [entry for entry in entries if "not_run" not in entry], not_run


def round_trip_margin(protocol: dict[str, Any], case: str, symbols: int) -> Decimal:
    """One round trip's costs at the exposure cap, summed over the compared symbols."""
    settings, costs = protocol["settings"], protocol["cost_cases"][case]
    per_side = (Decimal(costs["fee_bps"]) + Decimal(costs["slippage_bps"])) / 10000
    position = Decimal(settings["initial_cash"]) * Decimal(settings["exposure_fraction"])
    return symbols * position * 2 * per_side


def frozen_manager(protocol: dict[str, Any]) -> dict[str, Any]:
    """The protocol's manager settings, normalized as every input binds them."""
    return ManagerSettings(**protocol["manager"]).to_dict()


def manager_rows(
    reports: list[dict[str, Any]], protocol: dict[str, Any], *, case: str
) -> list[dict[str, Any]]:
    """One row per manager run under one cost case, with its turn record."""
    costs = protocol["cost_cases"][case]
    frozen = frozen_manager(protocol)
    limit = Decimal(protocol["max_failed_turn_fraction"])
    symbols = [report["symbol"] for report in reports]
    if len(set(symbols)) != len(symbols) or not set(symbols) <= set(protocol["symbols"]):
        raise ValueError("Each protocol symbol may appear at most once per case")
    rows = []
    for report in reports:
        settings = report["settings"]
        if (
            report["benchmark"] != MANAGER_CHANNEL
            or any(
                Decimal(settings[name]) != Decimal(value)
                for name, value in {**protocol["settings"], **costs}.items()
            )
            or report["manager"]["settings"] != frozen
        ):
            raise ValueError(
                f"{report['symbol']} run does not match the frozen {MANAGER_CHANNEL} "
                f"settings under {case} costs"
            )
        summary = report["manager"]
        valuation = report["end_valuation"]
        hold = valuation["comparators"]["hold_the_coin"] if valuation else None
        failed = summary["outcomes"].get("failed", 0)
        pinned = all(same_model(frozen["model"], model) for model in summary["models"])
        within = failed <= limit * summary["turns"]
        rows.append(
            {
                "symbol": report["symbol"],
                "scored": bool(
                    valuation is not None
                    and hold is not None
                    and _scored(report, protocol)
                    and pinned
                    and within
                ),
                "complete": report["complete"],
                "pinned_model_only": pinned,
                "failed_turns_within_limit": within,
                "halted": report["halted"],
                "position_open_at_end": bool(valuation and valuation["position_open_at_end"]),
                "net_pnl": valuation["net_pnl"] if valuation else None,
                "hold_net_pnl": hold["net_pnl"] if hold else None,
                "round_trips": valuation["round_trips"] if valuation else 0,
                "fee_share_of_gain": valuation["fee_share_of_gain"] if valuation else None,
                "turns": summary["turns"],
                "turn_outcomes": summary["outcomes"],
                "rulebook_version": summary["rulebook_version"],
                "journal_cost_usd": summary["cost_usd"],
            }
        )
    return rows


def manager_case_outcome(
    reports: list[dict[str, Any]],
    rules: dict[str, dict[str, Any]],
    protocol: dict[str, Any],
    *,
    case: str,
    bar_rules: tuple[str, ...],
) -> dict[str, Any]:
    """Read the manager under one cost case against cash, hold and the bar's rules.

    ``rules`` maps each rule to its ``case_outcome`` under this cost case.
    """
    reports, not_run = split_entries(reports, protocol, label=f"{MANAGER_CHANNEL} {case}")
    rows = manager_rows(reports, protocol, case=case) + [not_run_row(entry) for entry in not_run]
    scored = {row["symbol"]: row for row in rows if row["scored"]}
    for rule in bar_rules:
        rule_scored = {row["symbol"] for row in rules[rule]["symbols"] if row["scored"]}
        scored = {symbol: row for symbol, row in scored.items() if symbol in rule_scored}
    common = sorted(scored)
    net = sum((Decimal(scored[symbol]["net_pnl"]) for symbol in common), Decimal(0))
    comparators = {
        "cash": Decimal(0),
        "hold_the_coin": sum(
            (Decimal(scored[symbol]["hold_net_pnl"]) for symbol in common), Decimal(0)
        ),
    }
    for rule in bar_rules:
        nets = {row["symbol"]: row.get("net_pnl") for row in rules[rule]["symbols"]}
        comparators[rule] = sum((Decimal(nets[symbol]) for symbol in common), Decimal(0))
    margin = round_trip_margin(protocol, case, len(common))
    trailed = [name for name, value in comparators.items() if not net > value + margin]
    if len(common) < protocol["min_scored_symbols"]:
        outcome = "inconclusive"
    else:
        outcome = "trails" if trailed else "pass"
    return {
        "case": case,
        "costs": protocol["cost_cases"][case],
        "outcome": outcome,
        "compared_symbols": common,
        "net_pnl": str(net),
        "comparators": {name: str(value) for name, value in comparators.items()},
        "margin": str(margin),
        "trailed": trailed,
        "symbols": rows,
    }


def manager_verdict(base: str, stress: str) -> str:
    """Combine the manager's two cost-case outcomes, in the predeclared order."""
    if "inconclusive" in (base, stress):
        return "inconclusive"
    passes = (base, stress).count("pass")
    return {2: "passes", 1: "cost-dependent"}.get(passes, "fails")


def _rule_outcome(
    entries: list[dict[str, Any]], protocol: dict[str, Any], *, rule: str, case: str
) -> dict[str, Any]:
    reports, not_run = split_entries(entries, protocol, label=f"{rule} {case}")
    outcome = case_outcome(reports, protocol, rule=rule, case=case)
    outcome["symbols"].extend(not_run_row(entry) for entry in not_run)
    return outcome


def read_round_two(
    runs: dict[str, dict[str, list[dict[str, Any]]]],
    protocol: dict[str, Any],
    *,
    source_sha256: str,
) -> dict[str, Any]:
    """The whole round-two reading from every run's report, grouped by channel and case.

    ``source_sha256`` is the frozen commit's source digest; a run bound to any
    other source is refused.
    """
    for channel in runs.values():
        for entries in channel.values():
            for entry in entries:
                if "not_run" not in entry and entry["implementation_sha256"] != source_sha256:
                    raise ValueError(
                        f"{entry['symbol']} {entry['benchmark']} run is bound to source "
                        f"{entry['implementation_sha256']}, not the frozen {source_sha256}"
                    )
    rules = {
        rule: {
            case: _rule_outcome(runs[rule][case], protocol, rule=rule, case=case)
            for case in protocol["cost_cases"]
        }
        for rule in BAR_RULES
    }
    rule_verdicts = {
        rule: rule_verdict(rules[rule]["base"]["outcome"], rules[rule]["stress"]["outcome"])
        for rule in BAR_RULES
    }
    # Baseline joins the bar only if it survives costs on this window itself.
    bar_rules = tuple(
        rule for rule in BAR_RULES if rule == "mean-reversion" or rule_verdicts[rule] == "survives"
    )
    cases = {
        case: manager_case_outcome(
            runs[MANAGER_CHANNEL][case],
            {rule: rules[rule][case] for rule in BAR_RULES},
            protocol,
            case=case,
            bar_rules=bar_rules,
        )
        for case in protocol["cost_cases"]
    }
    verdict = manager_verdict(cases["base"]["outcome"], cases["stress"]["outcome"])
    return {
        "protocol": protocol["protocol"],
        "source_sha256": source_sha256,
        "bar": ["cash", "hold_the_coin", *bar_rules],
        "manager": cases,
        "verdict": verdict,
        "action": "propose-round-three" if verdict == "passes" else "pause",
        "rules": rules,
        "rule_verdicts": rule_verdicts,
    }
