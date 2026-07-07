"""Stage 1 → 2 promotion scorecard over the idea-level closeout/audit trail.

Scores the measured-outcome rubric gates
(docs/decisions/adopt-measured-outcome-rubric.md) from stored trade-idea
records, audit events, and closeout attributions — never from batch-cycle
run artifacts, which are launchd scaffolding rather than evidence
(docs/decisions/adopt-event-driven-execution-topology.md, test-enforced in
test_scorecard.py). Replay-derived evidence is reported alongside wall-clock
evidence, never blended into the gate verdicts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from gpt_trader.features.trade_ideas.accounting import max_drawdown_from_peak_percent
from gpt_trader.features.trade_ideas.artifacts import stable_artifact_id
from gpt_trader.features.trade_ideas.audit import AuditAction
from gpt_trader.features.trade_ideas.eligibility import evaluate_eligibility
from gpt_trader.features.trade_ideas.service import TradeIdeaService, TradeIdeaView
from gpt_trader.features.trade_ideas.workflow import TERMINAL_STATES

SCORECARD_SCHEMA_VERSION = "gpt-trader.trade_ideas.scorecard.v1"
WALL_CLOCK_EVIDENCE_LABEL = "wall-clock"
REPLAY_EVIDENCE_LABEL = "replay-derived"
OBSERVATION_WINDOW_RULE = "rolling 60 days and >= 200 closed ideas, whichever is larger"

_RATE_QUANT = Decimal("0.0000")
_PERCENT_QUANT = Decimal("0.01")


class GateStatus(str, Enum):
    """Verdict for one promotion gate or loop-health check."""

    PASS = "pass"
    FAIL = "fail"
    NOT_YET_MEASURABLE = "not_yet_measurable"


@dataclass(frozen=True, slots=True)
class ScorecardThresholds:
    """Owner-tunable gate thresholds.

    Defaults carry the starting values recorded in
    docs/decisions/adopt-measured-outcome-rubric.md; tuning them is an owner
    act that does not reopen the decision.
    """

    min_closed_ideas: int = 200
    min_window_days: int = 60
    min_eligibility_pass_rate: Decimal = Decimal("0.90")
    min_attribution_coverage: Decimal = Decimal("1")
    min_risk_calibration: Decimal = Decimal("0.95")
    proposal_freshness_hours: int = 24
    baseline_actor_prefix: str = "baseline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_closed_ideas": self.min_closed_ideas,
            "min_window_days": self.min_window_days,
            "min_eligibility_pass_rate": str(self.min_eligibility_pass_rate),
            "min_attribution_coverage": str(self.min_attribution_coverage),
            "min_risk_calibration": str(self.min_risk_calibration),
            "min_expectancy_r": "> 0",
            "min_benchmark_edge_r": "> 0",
            "proposal_freshness_hours": self.proposal_freshness_hours,
            "baseline_actor_prefix": self.baseline_actor_prefix,
        }


def build_stage_promotion_scorecard(
    service: TradeIdeaService,
    *,
    now: datetime | None = None,
    thresholds: ScorecardThresholds | None = None,
) -> dict[str, Any]:
    """Score the Stage 1 → 2 gates from the idea-level trail."""
    current_time = now or datetime.now(UTC)
    tuned = thresholds or ScorecardThresholds()
    views = service.list_views()

    window_start = _observation_window_start(views, now=current_time, thresholds=tuned)
    closed_views = [view for view in views if view.state in TERMINAL_STATES]
    closed_in_window = [
        view for view in closed_views if window_start <= _closed_at(view) <= current_time
    ]
    proposed_in_window = [
        view
        for view in views
        if view.events and window_start <= view.events[0].timestamp <= current_time
    ]

    gates = {
        "track_record_depth": _depth_gate(
            views,
            closed_in_window,
            now=current_time,
            thresholds=tuned,
        ),
        "eligibility_pass_rate": _eligibility_gate(proposed_in_window, thresholds=tuned),
        "attribution_coverage": _attribution_gate(closed_in_window, thresholds=tuned),
        "risk_calibration": _risk_calibration_gate(closed_in_window, thresholds=tuned),
        "expectancy": _expectancy_gate(closed_in_window),
        "benchmark_edge": _benchmark_edge_gate(closed_in_window, thresholds=tuned),
        "max_drawdown_from_peak": _drawdown_gate(
            service,
            window_start=window_start,
            now=current_time,
        ),
    }
    loop_health = {
        "proposals_flowing": _proposals_flowing_check(views, now=current_time, thresholds=tuned),
        "attribution_coverage": _attribution_red(gates["attribution_coverage"]),
        "audit_integrity": _audit_integrity_check(service),
    }

    status_counts = {status.value: 0 for status in GateStatus}
    for verdict in gates.values():
        status_counts[verdict["status"]] += 1
    reds_failing = sorted(
        name for name, check in loop_health.items() if check["status"] != GateStatus.PASS.value
    )
    promotable = status_counts[GateStatus.PASS.value] == len(gates) and not reds_failing

    payload = {
        "evidence": WALL_CLOCK_EVIDENCE_LABEL,
        "observation_window": {
            "rule": OBSERVATION_WINDOW_RULE,
            "start": window_start.isoformat(),
            "end": current_time.isoformat(),
            "closed_idea_count": len(closed_in_window),
        },
        "thresholds": tuned.to_dict(),
        "gates": gates,
        "loop_health": loop_health,
        "overall": {
            "promotable": promotable,
            "gates_passed": status_counts[GateStatus.PASS.value],
            "gates_failed": status_counts[GateStatus.FAIL.value],
            "gates_not_yet_measurable": status_counts[GateStatus.NOT_YET_MEASURABLE.value],
            "loop_health_reds": reds_failing,
        },
    }
    scorecard_id = stable_artifact_id(
        "tis",
        {"schema_version": SCORECARD_SCHEMA_VERSION, "payload": payload},
    )
    return {
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "scorecard_id": scorecard_id,
        "generated_at": current_time.isoformat(),
        "idea_count": len(views),
        **payload,
    }


def build_replay_evidence(
    report_payload: Mapping[str, Any],
    *,
    baseline_proposer_prefix: str = "baseline",
) -> dict[str, Any]:
    """Label replay output as replay-derived calibration/edge evidence.

    Accepts the JSON payload of one ``ideas replay`` run — either a single
    ``ReplayReport`` or a ``ReplayTournamentReport`` — and reports calibration
    per proposer plus average-R edge against the deterministic baseline when
    both sides replayed the same window. The result is evidence *alongside*
    the wall-clock scorecard; it never feeds a gate verdict.
    """
    reports = _replay_reports(report_payload)
    calibration = [
        {
            "proposer_id": report["proposer_id"],
            "ideas_proposed": report.get("ideas_proposed"),
            "resolved_ideas": report.get("resolved_ideas"),
            "target_hit_rate": report.get("target_hit_rate"),
            "stop_hit_rate": report.get("stop_hit_rate"),
            "average_return_r": report.get("average_return_r"),
            "eligibility_pass_rate": report.get("eligibility_pass_rate"),
        }
        for report in reports
    ]
    return {
        "evidence": REPLAY_EVIDENCE_LABEL,
        "symbol": report_payload.get("symbol"),
        "granularity": report_payload.get("granularity"),
        "source": report_payload.get("source"),
        "snapshots_evaluated": report_payload.get("snapshots_evaluated"),
        "calibration": calibration,
        "benchmark_edge": _replay_benchmark_edge(
            calibration,
            baseline_proposer_prefix=baseline_proposer_prefix,
        ),
    }


def format_stage_promotion_scorecard(payload: Mapping[str, Any]) -> str:
    """Render the scorecard for operators (CliResponse ✓/✗ convention)."""
    overall = payload["overall"]
    window = payload["observation_window"]
    gates: Mapping[str, Mapping[str, Any]] = payload["gates"]
    loop_health: Mapping[str, Mapping[str, Any]] = payload["loop_health"]

    lines = [
        "✓ ideas scorecard OK "
        f"(gates {overall['gates_passed']}/{len(gates)} pass, "
        f"{overall['gates_failed']} fail, "
        f"{overall['gates_not_yet_measurable']} not-yet-measurable, "
        f"promotable={'yes' if overall['promotable'] else 'no'})",
        "",
        "Observation window",
        f"rule: {window['rule']}",
        f"window: {window['start']} -> {window['end']}",
        f"closed_ideas_in_window: {window['closed_idea_count']}",
        "",
        f"Stage 1 -> 2 gates ({payload['evidence']})",
        *(_gate_line(name, verdict) for name, verdict in gates.items()),
        "",
        "Loop health",
        *(_gate_line(name, check) for name, check in loop_health.items()),
    ]
    for evidence in payload.get("replay_evidence", ()):
        lines.extend(["", *(_replay_evidence_lines(evidence))])
    return "\n".join(lines)


def _replay_evidence_lines(evidence: Mapping[str, Any]) -> list[str]:
    header = (
        f"Replay evidence ({evidence['evidence']}; reported alongside wall-clock, "
        "not blended into gates)"
    )
    lines = [
        header,
        (
            f"window: {evidence.get('symbol')} {evidence.get('granularity')} "
            f"source={evidence.get('source')} "
            f"snapshots={evidence.get('snapshots_evaluated')}"
        ),
    ]
    for row in evidence["calibration"]:
        lines.append(
            f"{row['proposer_id']}: resolved={row['resolved_ideas']}, "
            f"target_hit_rate={row['target_hit_rate']}, "
            f"stop_hit_rate={row['stop_hit_rate']}, "
            f"avg_r={row['average_return_r']}, "
            f"eligibility_pass_rate={row['eligibility_pass_rate']}"
        )
    edge = evidence["benchmark_edge"]
    if edge["comparisons"]:
        for comparison in edge["comparisons"]:
            lines.append(
                f"edge vs {edge['baseline_proposer_id']}: "
                f"{comparison['proposer_id']} avg_r {comparison['average_return_r']} "
                f"- baseline {edge['baseline_average_return_r']} "
                f"= {comparison['edge_r']}"
            )
    else:
        lines.append(f"edge: {edge['detail']}")
    return lines


def _gate_line(name: str, verdict: Mapping[str, Any]) -> str:
    marker = {
        GateStatus.PASS.value: "✓",
        GateStatus.FAIL.value: "✗",
        GateStatus.NOT_YET_MEASURABLE.value: "–",
    }[verdict["status"]]
    detail = verdict.get("detail", "")
    suffix = f" ({detail})" if detail else ""
    return f"{marker} {name}: {verdict['status']}{suffix}"


def _observation_window_start(
    views: list[TradeIdeaView],
    *,
    now: datetime,
    thresholds: ScorecardThresholds,
) -> datetime:
    day_start = now - timedelta(days=thresholds.min_window_days)
    closed_at_desc = sorted(
        (
            _closed_at(view)
            for view in views
            if view.state in TERMINAL_STATES and _closed_at(view) <= now
        ),
        reverse=True,
    )
    if len(closed_at_desc) >= thresholds.min_closed_ideas:
        count_start = closed_at_desc[thresholds.min_closed_ideas - 1]
    elif closed_at_desc:
        count_start = closed_at_desc[-1]
    else:
        count_start = day_start
    return min(day_start, count_start)


def _closed_at(view: TradeIdeaView) -> datetime:
    return view.events[-1].timestamp


def _proposing_actor(view: TradeIdeaView) -> str:
    return view.events[0].actor_id


def _gate(
    status: GateStatus,
    *,
    measured: Mapping[str, Any],
    detail: str = "",
) -> dict[str, Any]:
    return {"status": status.value, "measured": dict(measured), "detail": detail}


def _depth_gate(
    views: list[TradeIdeaView],
    closed_in_window: list[TradeIdeaView],
    *,
    now: datetime,
    thresholds: ScorecardThresholds,
) -> dict[str, Any]:
    first_events = [view.events[0].timestamp for view in views if view.events]
    trail_age_days = (now - min(first_events)).days if first_events else 0
    closed_count = len(closed_in_window)
    depth_met = closed_count >= thresholds.min_closed_ideas
    span_met = trail_age_days >= thresholds.min_window_days
    return _gate(
        GateStatus.PASS if depth_met and span_met else GateStatus.FAIL,
        measured={"closed_idea_count": closed_count, "trail_age_days": trail_age_days},
        detail=(
            f"{closed_count}/{thresholds.min_closed_ideas} closed ideas, "
            f"{trail_age_days}/{thresholds.min_window_days} days of trail"
        ),
    )


def _eligibility_gate(
    proposed_in_window: list[TradeIdeaView],
    *,
    thresholds: ScorecardThresholds,
) -> dict[str, Any]:
    total = len(proposed_in_window)
    if total == 0:
        return _gate(
            GateStatus.NOT_YET_MEASURABLE,
            measured={"proposed_count": 0},
            detail="no ideas proposed in the observation window",
        )
    eligible = sum(1 for view in proposed_in_window if not evaluate_eligibility(view.idea))
    rate = Decimal(eligible) / Decimal(total)
    return _gate(
        GateStatus.PASS if rate >= thresholds.min_eligibility_pass_rate else GateStatus.FAIL,
        measured={
            "eligible_count": eligible,
            "proposed_count": total,
            "rate": _quantized(rate),
        },
        detail=(
            f"{eligible}/{total} eligible ({_as_pct(rate)}%), "
            f"need >= {_as_pct(thresholds.min_eligibility_pass_rate)}%"
        ),
    )


def _attribution_gate(
    closed_in_window: list[TradeIdeaView],
    *,
    thresholds: ScorecardThresholds,
) -> dict[str, Any]:
    total = len(closed_in_window)
    if total == 0:
        return _gate(
            GateStatus.NOT_YET_MEASURABLE,
            measured={"closed_idea_count": 0},
            detail="no closed ideas in the observation window",
        )
    attributed = sum(1 for view in closed_in_window if view.closeout_attribution is not None)
    rate = Decimal(attributed) / Decimal(total)
    return _gate(
        GateStatus.PASS if rate >= thresholds.min_attribution_coverage else GateStatus.FAIL,
        measured={
            "attributed_count": attributed,
            "closed_idea_count": total,
            "rate": _quantized(rate),
        },
        detail=(
            f"{attributed}/{total} closed ideas attributed ({_as_pct(rate)}%), "
            f"need {_as_pct(thresholds.min_attribution_coverage)}%"
        ),
    )


def _risk_calibration_gate(
    closed_in_window: list[TradeIdeaView],
    *,
    thresholds: ScorecardThresholds,
) -> dict[str, Any]:
    losers = 0
    calibrated = 0
    losers_without_max_loss = 0
    for view in closed_in_window:
        closeout = view.closeout_attribution
        if closeout is None:
            continue
        realized = closeout.realized_profit_loss_amount
        if realized is None or realized >= 0:
            continue
        losers += 1
        max_loss = closeout.max_loss.amount
        if max_loss is None:
            # A loser without a recorded max-loss estimate cannot demonstrate
            # calibration, so it counts against the gate rather than shrinking
            # the denominator.
            losers_without_max_loss += 1
            continue
        if -realized <= max_loss:
            calibrated += 1
    if losers == 0:
        return _gate(
            GateStatus.NOT_YET_MEASURABLE,
            measured={"loser_count": 0},
            detail="no attributed losing closeouts in the observation window",
        )
    rate = Decimal(calibrated) / Decimal(losers)
    return _gate(
        GateStatus.PASS if rate >= thresholds.min_risk_calibration else GateStatus.FAIL,
        measured={
            "calibrated_count": calibrated,
            "loser_count": losers,
            "losers_without_max_loss": losers_without_max_loss,
            "rate": _quantized(rate),
        },
        detail=(
            f"{calibrated}/{losers} losers within recorded max loss ({_as_pct(rate)}%), "
            f"need >= {_as_pct(thresholds.min_risk_calibration)}%"
        ),
    )


def _expectancy_gate(closed_in_window: list[TradeIdeaView]) -> dict[str, Any]:
    returns = _returns_by_actor(closed_in_window)
    all_returns = [value for values in returns.values() for value in values]
    if not all_returns:
        return _gate(
            GateStatus.NOT_YET_MEASURABLE,
            measured={"comparable_closeout_count": 0},
            detail="no closeouts with both realized amount and max-loss estimate",
        )
    average = sum(all_returns, Decimal("0")) / Decimal(len(all_returns))
    return _gate(
        GateStatus.PASS if average > 0 else GateStatus.FAIL,
        measured={
            "average_r": _quantized(average),
            "comparable_closeout_count": len(all_returns),
        },
        detail=f"avg R {_quantized(average)} across {len(all_returns)} closeouts, need > 0",
    )


def _benchmark_edge_gate(
    closed_in_window: list[TradeIdeaView],
    *,
    thresholds: ScorecardThresholds,
) -> dict[str, Any]:
    returns = _returns_by_actor(closed_in_window)
    baseline_returns: list[Decimal] = []
    candidate_returns: list[Decimal] = []
    for actor_id, values in returns.items():
        if actor_id.startswith(thresholds.baseline_actor_prefix):
            baseline_returns.extend(values)
        else:
            candidate_returns.extend(values)
    if not baseline_returns or not candidate_returns:
        missing = "baseline" if not baseline_returns else "non-baseline"
        return _gate(
            GateStatus.NOT_YET_MEASURABLE,
            measured={
                "baseline_closeout_count": len(baseline_returns),
                "candidate_closeout_count": len(candidate_returns),
            },
            detail=f"no comparable {missing} closeouts in the observation window",
        )
    baseline_avg = sum(baseline_returns, Decimal("0")) / Decimal(len(baseline_returns))
    candidate_avg = sum(candidate_returns, Decimal("0")) / Decimal(len(candidate_returns))
    edge = candidate_avg - baseline_avg
    return _gate(
        GateStatus.PASS if edge > 0 else GateStatus.FAIL,
        measured={
            "candidate_average_r": _quantized(candidate_avg),
            "baseline_average_r": _quantized(baseline_avg),
            "edge_r": _quantized(edge),
            "baseline_closeout_count": len(baseline_returns),
            "candidate_closeout_count": len(candidate_returns),
        },
        detail=(
            f"candidate avg R {_quantized(candidate_avg)} - "
            f"baseline avg R {_quantized(baseline_avg)} = {_quantized(edge)}, need > 0"
        ),
    )


def _drawdown_gate(
    service: TradeIdeaService,
    *,
    window_start: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Score max drawdown-from-peak over the window from the equity ledger.

    Reads the same trail-derived ledger as the continuous portfolio monitors
    (#1192): the appetite is ``max_drawdown_from_peak_pct`` on the active risk
    budget (one measurement, two consumers — this promotion gate and the
    ratchet's down-ladder).
    """
    limit = service.peek_budget().max_drawdown_from_peak_pct
    points = service.equity_ledger_points()
    worst = max_drawdown_from_peak_percent(points, window_start=window_start, window_end=now)
    if worst is None:
        return _gate(
            GateStatus.NOT_YET_MEASURABLE,
            measured={"ledger_point_count": len(points)},
            detail="no attested-equity ledger points in the observation window",
        )
    measured: dict[str, Any] = {
        "max_drawdown_from_peak_pct": str(worst.quantize(_PERCENT_QUANT)),
        "ledger_point_count": len(points),
    }
    if limit is None:
        return _gate(
            GateStatus.NOT_YET_MEASURABLE,
            measured=measured,
            detail=(
                "no max_drawdown_from_peak_pct configured on the risk budget; "
                "set the lever to make this gate scoreable"
            ),
        )
    measured["max_drawdown_from_peak_limit_pct"] = str(limit)
    return _gate(
        GateStatus.PASS if worst <= limit else GateStatus.FAIL,
        measured=measured,
        detail=(
            f"max drawdown-from-peak {worst.quantize(_PERCENT_QUANT)}% over the window, "
            f"budget limit {limit}%"
        ),
    )


def _returns_by_actor(closed_in_window: list[TradeIdeaView]) -> dict[str, list[Decimal]]:
    returns: dict[str, list[Decimal]] = {}
    for view in closed_in_window:
        closeout = view.closeout_attribution
        if closeout is None:
            continue
        realized = closeout.realized_profit_loss_amount
        max_loss = closeout.max_loss.amount
        if realized is None or max_loss is None or max_loss <= 0:
            continue
        returns.setdefault(_proposing_actor(view), []).append(realized / max_loss)
    return returns


def _proposals_flowing_check(
    views: list[TradeIdeaView],
    *,
    now: datetime,
    thresholds: ScorecardThresholds,
) -> dict[str, Any]:
    proposal_times = [
        event.timestamp
        for view in views
        for event in view.events
        if event.action is AuditAction.PROPOSED
    ]
    if not proposal_times:
        return _gate(
            GateStatus.FAIL,
            measured={"latest_proposal_at": None},
            detail="no proposal events on the trail",
        )
    latest = max(proposal_times)
    age = now - latest
    flowing = age <= timedelta(hours=thresholds.proposal_freshness_hours)
    return _gate(
        GateStatus.PASS if flowing else GateStatus.FAIL,
        measured={"latest_proposal_at": latest.isoformat()},
        detail=(
            f"latest proposal {latest.isoformat()}, "
            f"freshness window {thresholds.proposal_freshness_hours}h"
        ),
    )


def _attribution_red(attribution_gate: Mapping[str, Any]) -> dict[str, Any]:
    # The loop-health red mirrors the gate: not-yet-measurable coverage is not
    # a red (nothing has closed), but any measured gap is.
    status = (
        GateStatus.PASS if attribution_gate["status"] != GateStatus.FAIL.value else GateStatus.FAIL
    )
    return _gate(
        status,
        measured=attribution_gate["measured"],
        detail=attribution_gate["detail"],
    )


def _audit_integrity_check(service: TradeIdeaService) -> dict[str, Any]:
    try:
        events = service.audit_log.verify()
    except Exception as error:
        return _gate(
            GateStatus.FAIL,
            measured={},
            detail=f"audit verification failed: {error}",
        )
    return _gate(
        GateStatus.PASS,
        measured={"event_count": len(events)},
        detail=f"{len(events)} events verified",
    )


def _replay_reports(report_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if "reports" in report_payload:
        return [_payload_mapping(report) for report in report_payload["reports"]]
    return [report_payload]


def _payload_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("replay report entries must be JSON objects")
    return value


def _replay_benchmark_edge(
    calibration: list[dict[str, Any]],
    *,
    baseline_proposer_prefix: str,
) -> dict[str, Any]:
    baseline_rows = [
        row
        for row in calibration
        if str(row["proposer_id"]).startswith(baseline_proposer_prefix)
        and row["average_return_r"] is not None
    ]
    candidate_rows = [
        row
        for row in calibration
        if not str(row["proposer_id"]).startswith(baseline_proposer_prefix)
        and row["average_return_r"] is not None
    ]
    if not baseline_rows or not candidate_rows:
        missing = "baseline" if not baseline_rows else "non-baseline"
        return {
            "baseline_proposer_id": baseline_rows[0]["proposer_id"] if baseline_rows else None,
            "baseline_average_return_r": None,
            "comparisons": [],
            "detail": f"no {missing} proposer with resolved replay returns in this window",
        }
    baseline = baseline_rows[0]
    baseline_avg = Decimal(baseline["average_return_r"])
    comparisons = [
        {
            "proposer_id": row["proposer_id"],
            "average_return_r": row["average_return_r"],
            "edge_r": _quantized(Decimal(row["average_return_r"]) - baseline_avg),
        }
        for row in candidate_rows
    ]
    return {
        "baseline_proposer_id": baseline["proposer_id"],
        "baseline_average_return_r": baseline["average_return_r"],
        "comparisons": comparisons,
        "detail": "",
    }


def _quantized(value: Decimal) -> str:
    return str(value.quantize(_RATE_QUANT))


def _as_pct(value: Decimal) -> str:
    return str((value * Decimal("100")).quantize(_PERCENT_QUANT))
