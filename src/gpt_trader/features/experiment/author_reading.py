"""The predeclared rulebook-author reading, committed with its protocol before the window.

Thresholds come from the committed protocol file; the rules here mirror
``config/experiments/rulebook-author-2026-10-22/PROTOCOL.md`` and the
[cheap-proofs decision](docs/decisions/cheap-proofs-before-paid-ai-test.md),
which win if they ever differ. Every supplied run is reported; none is dropped.

Each cost case is one author run over the portfolio (``experiment
author-inspect``). Its coins pool their net P&L at liquidation value over the
symbols scored for the author and for every rule in the bar, against cash,
hold-the-coin and each such rule on exactly those symbols, by round two's
margin. A case is scored only when its run is complete and full length, ran
under the frozen author settings and starting rulebook, was answered only by
the pinned model, and lost no more turns to failed calls than the protocol
allows. There is no round-trip minimum for the author.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from gpt_trader.features.experiment.manager_reading import (
    BAR_RULES,
    _rule_outcome,
    manager_verdict,
    not_run_row,
    pooled_case,
)
from gpt_trader.features.experiment.model_transport import same_model
from gpt_trader.features.experiment.reading import _scored, rule_verdict
from gpt_trader.features.experiment.rulebook import RULEBOOK_CHANNEL
from gpt_trader.features.experiment.rulebook_author import protocol_author

AUTHOR_READING = "ai-rulebook-author"
# The proposed reading of RJ's pause condition, for RJ to confirm on the card.
ACTIONS = {
    "passes": "confirm-on-a-fresh-window",
    "cost-dependent": "one-fresh-window-then-pause-if-not-passed",
    "fails": "one-fresh-window-then-pause-if-not-passed",
    "inconclusive": "no-reading",
}


def author_checks(report: dict[str, Any], protocol: dict[str, Any]) -> dict[str, bool]:
    """Whether one cost case's author run counts, by the protocol's coverage rules."""
    settings, manager, _ = protocol_author(
        protocol["author"], Decimal(protocol["settings"]["exposure_fraction"])
    )
    frozen = {"model": manager.model, "effort": manager.effort, "max_tokens": manager.max_tokens}
    author = report["author"]
    return {
        "complete": bool(
            report["complete"] and report["total_bars"] == protocol["bars_per_symbol"]
        ),
        "frozen_settings": {k: report["manager"][k] for k in frozen} == frozen
        and report["settings"]
        == {
            "cadence_bars": settings.cadence_bars,
            "history_days": settings.history_days,
        },
        "pinned_model_only": all(same_model(manager.model, model) for model in author["models"]),
        "failed_turns_within_limit": author["outcomes"]["failed"] <= protocol["max_failed_turns"],
    }


def author_case_outcome(
    entry: dict[str, Any],
    rules: dict[str, dict[str, Any]],
    protocol: dict[str, Any],
    *,
    case: str,
    bar_rules: tuple[str, ...],
    source_sha256: str,
) -> dict[str, Any]:
    """Read one cost case's author run against cash, hold and the bar's rules."""
    costs = protocol["cost_cases"][case]
    _, _, starting = protocol_author(
        protocol["author"], Decimal(protocol["settings"]["exposure_fraction"])
    )
    if "not_run" in entry:
        rows = [
            not_run_row({"symbol": s, "not_run": entry["not_run"]}) for s in protocol["symbols"]
        ]
        outcome = pooled_case(rows, rules, protocol, case=case, bar_rules=bar_rules)
        return outcome | {"author": {"not_run": entry["not_run"]}}
    if entry["implementation_sha256"] != source_sha256:
        raise ValueError(
            f"{RULEBOOK_CHANNEL} {case} run is bound to source "
            f"{entry['implementation_sha256']}, not the frozen {source_sha256}"
        )
    checks = author_checks(entry, protocol)
    counts = all(checks.values())
    present = {row["symbol"] for row in entry["symbols"]}
    if not present <= set(protocol["symbols"]):
        raise ValueError(f"{RULEBOOK_CHANNEL} {case} run covers a symbol outside the protocol")
    rows = []
    for report in entry["symbols"]:
        settings = report["settings"]
        if any(
            Decimal(settings[name]) != Decimal(value)
            for name, value in {**protocol["settings"], **costs}.items()
        ):
            raise ValueError(f"{RULEBOOK_CHANNEL} run does not match {case} costs")
        if report["rulebook"]["starting"] != starting:
            raise ValueError(f"{report['symbol']} did not start from the frozen rulebook")
        valuation = report["end_valuation"]
        hold = valuation["comparators"]["hold_the_coin"] if valuation else None
        rows.append(
            {
                "symbol": report["symbol"],
                "scored": bool(
                    counts
                    and valuation is not None
                    and hold is not None
                    and _scored(report, protocol)
                ),
                "halted": report["halted"],
                "position_open_at_end": bool(valuation and valuation["position_open_at_end"]),
                "net_pnl": valuation["net_pnl"] if valuation else None,
                "hold_net_pnl": hold["net_pnl"] if hold else None,
                "round_trips": valuation["round_trips"] if valuation else 0,
                "fee_share_of_gain": valuation["fee_share_of_gain"] if valuation else None,
                "rulebook_version": report["rulebook"]["version"],
            }
        )
    rows += [
        not_run_row({"symbol": s, "not_run": "not in the author portfolio (fetch refused)"})
        for s in sorted(set(protocol["symbols"]) - present)
    ]
    outcome = pooled_case(rows, rules, protocol, case=case, bar_rules=bar_rules)
    author = entry["author"]
    return outcome | {
        "author": {
            "checks": checks,
            "turns": author["turns"],
            "outcomes": author["outcomes"],
            "models": author["models"],
            "journal_cost_usd": author["cost_usd"],
        }
    }


def read_rulebook_author(
    runs: dict[str, dict[str, Any]],
    protocol: dict[str, Any],
    *,
    source_sha256: str,
) -> dict[str, Any]:
    """The whole reading: each rule's per-symbol reports and each case's author report.

    ``runs[RULEBOOK_CHANNEL][case]`` is one ``author-inspect`` report or
    ``{"not_run": "<why>"}``; each rule case is a list over every protocol
    symbol, as in round two. Every run must be bound to ``source_sha256``.
    """
    for rule in BAR_RULES:
        for entries in runs[rule].values():
            for entry in entries:
                if "not_run" not in entry and entry["implementation_sha256"] != source_sha256:
                    raise ValueError(
                        f"{entry['symbol']} {rule} run is bound to source "
                        f"{entry['implementation_sha256']}, not the frozen {source_sha256}"
                    )
    covered = {
        row["symbol"]
        for entry in runs[RULEBOOK_CHANNEL].values()
        if "not_run" not in entry
        for row in entry["symbols"]
    }
    # A rule needs no model, so only a refused fetch leaves it without a run,
    # and then that symbol has no run anywhere.
    for rule in BAR_RULES:
        for entries in runs[rule].values():
            for entry in entries:
                if "not_run" in entry and (
                    entry["symbol"] in covered
                    or any(
                        "not_run" not in other and other["symbol"] == entry["symbol"]
                        for name in BAR_RULES
                        for others in runs[name].values()
                        for other in others
                    )
                ):
                    raise ValueError(
                        f"{entry['symbol']} {rule} is not run while another channel or case "
                        "has a run; a rule is not run only when its symbol's fetch was refused"
                    )
    # Every coin with a rule run is in each author portfolio: an author run that
    # leaves one out would narrow the comparison, so it is refused.
    fetched = {
        entry["symbol"]
        for rule in BAR_RULES
        for entries in runs[rule].values()
        for entry in entries
        if "not_run" not in entry
    }
    for case, entry in runs[RULEBOOK_CHANNEL].items():
        if "not_run" not in entry and {row["symbol"] for row in entry["symbols"]} != fetched:
            raise ValueError(
                f"{RULEBOOK_CHANNEL} {case} portfolio must be exactly the coins with rule runs"
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
        case: author_case_outcome(
            runs[RULEBOOK_CHANNEL][case],
            {rule: rules[rule][case] for rule in BAR_RULES},
            protocol,
            case=case,
            bar_rules=bar_rules,
            source_sha256=source_sha256,
        )
        for case in protocol["cost_cases"]
    }
    verdict = manager_verdict(cases["base"]["outcome"], cases["stress"]["outcome"])
    return {
        "protocol": protocol["protocol"],
        "source_sha256": source_sha256,
        "models_observed": sorted(
            {
                model
                for entry in runs[RULEBOOK_CHANNEL].values()
                if "not_run" not in entry
                for model in entry["author"]["models"]
            }
        ),
        "bar": ["cash", "hold_the_coin", *bar_rules],
        "author": cases,
        "verdict": verdict,
        "action": ACTIONS[verdict],
        "rules": rules,
        "rule_verdicts": rule_verdicts,
    }
