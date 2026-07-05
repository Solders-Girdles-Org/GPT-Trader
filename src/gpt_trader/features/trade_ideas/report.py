"""Read-only trade-idea track-record reporting."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from gpt_trader.features.trade_ideas.artifacts import stable_artifact_id
from gpt_trader.features.trade_ideas.audit import ActorType, AuditAction
from gpt_trader.features.trade_ideas.closeout import CloseoutResolution
from gpt_trader.features.trade_ideas.eligibility import evaluate_eligibility
from gpt_trader.features.trade_ideas.models import ConfidenceLabel
from gpt_trader.features.trade_ideas.policy import ApprovalPolicy
from gpt_trader.features.trade_ideas.service import TradeIdeaService, TradeIdeaView
from gpt_trader.features.trade_ideas.workflow import TERMINAL_STATES, TradeIdeaState

_RATE_QUANT = Decimal("0.0000")
_PERCENT_QUANT = Decimal("0.01")
REPORT_SCHEMA_VERSION = "gpt-trader.trade_ideas.report.v1"
_ACTION_KEYS = {
    AuditAction.PROPOSED: "proposed",
    AuditAction.CHANGED: "requested_changes",
    AuditAction.AUTO_APPROVAL_SKIPPED: "auto_approval_skipped",
    AuditAction.AUTO_EXECUTION_SKIPPED: "auto_execution_skipped",
    AuditAction.APPROVED: "approved",
    AuditAction.REJECTED: "rejected",
    AuditAction.SUBMITTED: "submitted",
    AuditAction.FILLED: "filled",
    AuditAction.CANCELLED: "cancelled",
    AuditAction.EXPIRED: "expired",
}


def build_trade_idea_track_record_report(
    service: TradeIdeaService,
    *,
    now: datetime | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Build a read-only report from stored records, audit events, and closeouts."""
    current_time = now or datetime.now(UTC)
    report_cutoff = until or current_time
    views = _snapshot_views_by_window(
        service,
        service.list_views(),
        since=since,
        until=report_cutoff,
    )
    total_ideas = len(views)

    event_counts = _zero_action_counts()
    state_counts = {state.value: 0 for state in TradeIdeaState}
    confidence_counts = {label.value: 0 for label in ConfidenceLabel}

    for view in views:
        state_counts[view.state.value] += 1
        confidence_counts[view.idea.confidence.label.value] += 1
        for event in view.events:
            event_counts[_ACTION_KEYS[event.action]] += 1

    quality = _quality_summary(service, views, now=report_cutoff)
    workflow = _workflow_summary(views, event_counts, state_counts)
    closeouts = _closeout_summary(views)
    monthly = _monthly_summary(views)
    filters = _filters_payload(since=since, until=until)

    payload = {
        "proposal_volume": {
            "idea_count": total_ideas,
            "proposal_event_count": event_counts["proposed"],
            "resubmission_count": max(event_counts["proposed"] - total_ideas, 0),
            "by_month": monthly,
        },
        "workflow": workflow,
        "quality": {
            **quality,
            "confidence_counts": confidence_counts,
        },
        "closeouts": closeouts,
    }
    source = {
        "audit_event_count": sum(len(view.events) for view in views),
        "closeout_count": sum(1 for view in views if view.closeout_attribution is not None),
        "idea_count": total_ideas,
    }
    quality_report_id = stable_artifact_id(
        "tir",
        {
            "schema_version": REPORT_SCHEMA_VERSION,
            "filters": filters,
            "source": source,
            "payload": payload,
        },
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "quality_report_id": quality_report_id,
        "generated_at": current_time.isoformat(),
        "filters": filters,
        "row_count": total_ideas,
        "source": source,
        **payload,
    }


def format_trade_idea_track_record_report(report: Mapping[str, Any]) -> str:
    """Render a compact text report for operators."""
    proposal_volume = report["proposal_volume"]
    monthly = proposal_volume["by_month"]
    workflow = report["workflow"]
    quality = report["quality"]
    closeouts = report["closeouts"]
    profit_loss = closeouts["realized_profit_loss"]

    lines = [
        "✓ ideas report OK "
        f"({proposal_volume['idea_count']} ideas, "
        f"approval_rate={workflow['approval_rate_pct']}%, "
        f"closeout_coverage={closeouts['coverage_rate_pct']}%)",
        "",
        "Workflow",
        _counts_line("events", workflow["event_counts"]),
        _counts_line("current_states", workflow["current_state_counts"]),
        (
            f"approval_rate: {workflow['ever_approved_count']}/"
            f"{proposal_volume['idea_count']} ({workflow['approval_rate_pct']}%)"
        ),
        (
            f"submitted_rate: {workflow['ever_submitted_count']}/"
            f"{proposal_volume['idea_count']} ({workflow['submitted_rate_pct']}%)"
        ),
        (
            f"filled_rate: {workflow['ever_filled_count']}/"
            f"{proposal_volume['idea_count']} ({workflow['filled_rate_pct']}%)"
        ),
        "",
        "Quality",
        (
            f"eligible_records: {quality['eligible_count']}/"
            f"{proposal_volume['idea_count']} ({quality['eligible_rate_pct']}%)"
        ),
        (
            f"approval_ready_records: {quality['approval_ready_count']}/"
            f"{proposal_volume['idea_count']} ({quality['approval_ready_rate_pct']}%)"
        ),
        _counts_line("missing_quality_fields", quality["missing_field_counts"]),
        _counts_line("approval_policy_violations", quality["approval_policy_violation_counts"]),
        "",
        "Closeouts",
        (
            f"coverage: {closeouts['with_closeout_count']}/"
            f"{closeouts['terminal_count']} terminal "
            f"({closeouts['coverage_rate_pct']}%)"
        ),
        f"missing_closeout_count: {closeouts['missing_closeout_count']}",
        _counts_line("resolutions", closeouts["resolution_counts"]),
        _counts_line("outcomes", closeouts["outcome_distribution"]),
        (
            f"realized_profit_loss_total: {profit_loss['total_amount']} "
            f"across {profit_loss['available_amount_count']} closeouts"
        ),
        (
            "realized_vs_max_loss: "
            f"{profit_loss['max_loss_comparison']['total_realized_to_max_loss_ratio']} "
            f"across {profit_loss['max_loss_comparison']['comparable_count']} comparable closeouts"
        ),
    ]
    if monthly:
        lines.extend(
            [
                "",
                "Monthly",
                *_monthly_lines(monthly),
            ]
        )
    return "\n".join(lines)


def _workflow_summary(
    views: list[TradeIdeaView],
    event_counts: dict[str, int],
    state_counts: dict[str, int],
) -> dict[str, Any]:
    total_ideas = len(views)
    ever_approved = _ever_count(views, AuditAction.APPROVED)
    ever_submitted = _ever_count(views, AuditAction.SUBMITTED)
    ever_filled = _ever_count(views, AuditAction.FILLED)
    return {
        "event_counts": event_counts,
        "current_state_counts": state_counts,
        "ever_approved_count": ever_approved,
        "approval_rate": _rate(ever_approved, total_ideas),
        "approval_rate_pct": _percentage(ever_approved, total_ideas),
        "ever_submitted_count": ever_submitted,
        "submitted_rate": _rate(ever_submitted, total_ideas),
        "submitted_rate_pct": _percentage(ever_submitted, total_ideas),
        "ever_filled_count": ever_filled,
        "filled_rate": _rate(ever_filled, total_ideas),
        "filled_rate_pct": _percentage(ever_filled, total_ideas),
    }


def _snapshot_views_by_window(
    service: TradeIdeaService,
    views: list[TradeIdeaView],
    *,
    since: datetime | None,
    until: datetime | None,
) -> list[TradeIdeaView]:
    snapshots: list[TradeIdeaView] = []
    for view in views:
        if not view.events or not _timestamp_in_window(
            view.events[0].timestamp,
            since=since,
            until=until,
        ):
            continue
        events = tuple(
            event
            for event in view.events
            if _timestamp_in_window(event.timestamp, since=since, until=until)
        )
        if not events:
            continue
        idea = service.load_record_version(view.idea.decision_id, events[-1].record_hash)
        closeout = view.closeout_attribution
        if closeout is not None and not _timestamp_in_window(
            closeout.timestamp,
            since=since,
            until=until,
        ):
            closeout = None
        snapshots.append(
            TradeIdeaView(
                idea=idea,
                state=events[-1].after_state,
                events=events,
                closeout_attribution=closeout,
            )
        )
    return snapshots


def _timestamp_in_window(
    timestamp: datetime,
    *,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    if since is not None and timestamp < since:
        return False
    if until is not None and timestamp > until:
        return False
    return True


def _filters_payload(
    *,
    since: datetime | None,
    until: datetime | None,
) -> dict[str, str | None]:
    return {
        "since": since.isoformat() if since is not None else None,
        "until": until.isoformat() if until is not None else None,
    }


def _quality_summary(
    service: TradeIdeaService,
    views: list[TradeIdeaView],
    *,
    now: datetime,
) -> dict[str, Any]:
    policy = ApprovalPolicy()
    missing_fields: Counter[str] = Counter()
    eligibility_violations: Counter[str] = Counter()
    policy_violations: Counter[str] = Counter()
    eligible_count = 0
    approval_ready_count = 0

    for view in views:
        idea = view.idea
        if idea.max_loss.amount is None:
            missing_fields["max_loss.amount"] += 1
        if idea.max_loss.percent_of_account is None:
            missing_fields["max_loss.percent_of_account"] += 1
        if idea.time_horizon.expires_at is None:
            missing_fields["time_horizon.expires_at"] += 1
        if not idea.data_used:
            missing_fields["data_used"] += 1

        eligibility = evaluate_eligibility(idea)
        if not eligibility:
            eligible_count += 1
        eligibility_violations.update(eligibility)

        approval = policy.approval_violations(
            idea,
            actor_type=ActorType.HUMAN,
            # Active budget via a non-mutating read, so readiness matches what
            # approve() would decide under tightened caps without the report
            # seeding risk_budget.jsonl.
            budget=service.peek_budget(),
            open_approved_count=0,
            now=now,
            review_started_at=None,
            # Same budget-exposure context the approval path evaluates, so
            # approval_ready cannot report ready what approve() would refuse.
            budget_context=service.approval_budget_context(
                exclude_decision_id=idea.decision_id,
                now=now,
            ),
        )
        if not approval:
            approval_ready_count += 1
        policy_violations.update(approval)

    total_ideas = len(views)
    return {
        "eligible_count": eligible_count,
        "eligible_rate": _rate(eligible_count, total_ideas),
        "eligible_rate_pct": _percentage(eligible_count, total_ideas),
        "approval_ready_count": approval_ready_count,
        "approval_ready_rate": _rate(approval_ready_count, total_ideas),
        "approval_ready_rate_pct": _percentage(approval_ready_count, total_ideas),
        "missing_field_counts": _sorted_counter(missing_fields),
        "eligibility_violation_counts": _sorted_counter(eligibility_violations),
        "approval_policy_violation_counts": _sorted_counter(policy_violations),
    }


def _closeout_summary(views: list[TradeIdeaView]) -> dict[str, Any]:
    terminal_views = [view for view in views if view.state in TERMINAL_STATES]
    terminal_count = len(terminal_views)
    resolution_counts = {resolution.value: 0 for resolution in CloseoutResolution}
    missing_closeout_ids: list[str] = []
    outcome_distribution = {
        "profit": 0,
        "loss": 0,
        "flat": 0,
        "unavailable": 0,
    }
    total_amount = Decimal("0")
    available_amount_count = 0
    max_loss_comparisons: list[dict[str, Any]] = []

    for view in terminal_views:
        closeout = view.closeout_attribution
        if closeout is None:
            missing_closeout_ids.append(view.idea.decision_id)
            continue

        resolution_counts[closeout.resolution.value] += 1
        realized_amount = closeout.realized_profit_loss_amount
        if realized_amount is None:
            outcome_distribution[
                _realized_profit_loss_outcome(closeout.realized_profit_loss_percent)
            ] += 1
        else:
            available_amount_count += 1
            total_amount += realized_amount
            outcome_distribution[_realized_profit_loss_outcome(realized_amount)] += 1

        max_loss_amount = closeout.max_loss.amount
        if realized_amount is not None and max_loss_amount is not None and max_loss_amount > 0:
            max_loss_comparisons.append(
                {
                    "decision_id": closeout.decision_id,
                    "realized_profit_loss_amount": _decimal_to_str(realized_amount),
                    "max_loss_amount": _decimal_to_str(max_loss_amount),
                    "realized_to_max_loss_ratio": _decimal_ratio(
                        realized_amount,
                        max_loss_amount,
                    ),
                }
            )

    comparable_max_loss_total = sum(
        (Decimal(comparison["max_loss_amount"]) for comparison in max_loss_comparisons),
        Decimal("0"),
    )
    comparable_realized_total = sum(
        (Decimal(comparison["realized_profit_loss_amount"]) for comparison in max_loss_comparisons),
        Decimal("0"),
    )

    with_closeout_count = terminal_count - len(missing_closeout_ids)
    return {
        "terminal_count": terminal_count,
        "with_closeout_count": with_closeout_count,
        "missing_closeout_count": len(missing_closeout_ids),
        "missing_closeout_decision_ids": sorted(missing_closeout_ids),
        "coverage_rate": _rate(with_closeout_count, terminal_count),
        "coverage_rate_pct": _percentage(with_closeout_count, terminal_count),
        "resolution_counts": resolution_counts,
        "outcome_distribution": outcome_distribution,
        "realized_profit_loss": {
            "available_amount_count": available_amount_count,
            "total_amount": _decimal_to_str(total_amount),
            "average_amount": _average_decimal(total_amount, available_amount_count),
            "max_loss_comparison": {
                "comparable_count": len(max_loss_comparisons),
                "total_realized_amount": _decimal_to_str(comparable_realized_total),
                "total_max_loss_amount": _decimal_to_str(comparable_max_loss_total),
                "total_realized_to_max_loss_ratio": _decimal_ratio(
                    comparable_realized_total,
                    comparable_max_loss_total,
                ),
                "by_decision_id": sorted(
                    max_loss_comparisons,
                    key=lambda item: item["decision_id"],
                ),
            },
        },
    }


def _realized_profit_loss_outcome(value: Decimal | None) -> str:
    if value is None:
        return "unavailable"
    if value > 0:
        return "profit"
    if value < 0:
        return "loss"
    return "flat"


def _monthly_summary(views: list[TradeIdeaView]) -> dict[str, dict[str, Any]]:
    monthly: dict[str, dict[str, Any]] = {}
    for view in views:
        if not view.events:
            continue
        month = view.events[0].timestamp.strftime("%Y-%m")
        bucket = monthly.setdefault(
            month,
            {
                "idea_count": 0,
                "approved_count": 0,
                "terminal_count": 0,
                "with_closeout_count": 0,
                "realized_profit_loss_amount": Decimal("0"),
            },
        )
        bucket["idea_count"] += 1
        if _has_event(view, AuditAction.APPROVED):
            bucket["approved_count"] += 1
        if view.state in TERMINAL_STATES:
            bucket["terminal_count"] += 1
            if view.closeout_attribution is not None:
                bucket["with_closeout_count"] += 1
                realized_amount = view.closeout_attribution.realized_profit_loss_amount
                if realized_amount is not None:
                    bucket["realized_profit_loss_amount"] += realized_amount

    return {
        month: {
            **bucket,
            "realized_profit_loss_amount": _decimal_to_str(bucket["realized_profit_loss_amount"]),
            "approval_rate": _rate(bucket["approved_count"], bucket["idea_count"]),
            "approval_rate_pct": _percentage(bucket["approved_count"], bucket["idea_count"]),
            "closeout_coverage_rate": _rate(
                bucket["with_closeout_count"],
                bucket["terminal_count"],
            ),
            "closeout_coverage_rate_pct": _percentage(
                bucket["with_closeout_count"],
                bucket["terminal_count"],
            ),
        }
        for month, bucket in sorted(monthly.items())
    }


def _zero_action_counts() -> dict[str, int]:
    return {key: 0 for key in _ACTION_KEYS.values()}


def _ever_count(views: list[TradeIdeaView], action: AuditAction) -> int:
    return sum(1 for view in views if _has_event(view, action))


def _has_event(view: TradeIdeaView, action: AuditAction) -> bool:
    return any(event.action is action for event in view.events)


def _rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0000"
    return str((Decimal(numerator) / Decimal(denominator)).quantize(_RATE_QUANT))


def _percentage(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.00"
    value = (Decimal(numerator) / Decimal(denominator) * Decimal("100")).quantize(_PERCENT_QUANT)
    return str(value)


def _decimal_ratio(numerator: Decimal, denominator: Decimal) -> str | None:
    if denominator == 0:
        return None
    return str((numerator / denominator).quantize(_RATE_QUANT))


def _decimal_to_str(value: Decimal) -> str:
    return str(value)


def _average_decimal(total: Decimal, count: int) -> str | None:
    if count == 0:
        return None
    return str(total / Decimal(count))


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _counts_line(label: str, counts: Mapping[str, Any]) -> str:
    if not counts:
        return f"{label}: none"
    return f"{label}: " + ", ".join(f"{key}={value}" for key, value in counts.items())


def _monthly_lines(monthly: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return [
        (
            f"{month}: ideas={bucket['idea_count']}, "
            f"approval_rate={bucket['approval_rate_pct']}%, "
            f"closeout_coverage={bucket['closeout_coverage_rate_pct']}%, "
            f"realized_profit_loss={bucket['realized_profit_loss_amount']}"
        )
        for month, bucket in monthly.items()
    ]
