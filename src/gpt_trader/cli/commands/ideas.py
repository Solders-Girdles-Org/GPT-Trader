"""Trade-idea CLI commands.

This module is an operator-facing adapter over ``TradeIdeaService``. It never
submits, modifies, or cancels broker orders; submission and fill commands only
append audit records for tickets executed elsewhere.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from argparse import ArgumentParser, Namespace
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from gpt_trader.app.config import BotConfig
from gpt_trader.app.runtime import resolve_runtime_paths
from gpt_trader.cli import options
from gpt_trader.cli.commands.ideas_input import (
    CandleInputError,
    IdeaInputError,
    InputPayloadError,
    SnapshotBuildInputError,
    SnapshotInputError,
    _error_context,
    _load_candle_fixture,
    _load_market_snapshot,
    _load_trade_idea,
)
from gpt_trader.cli.response import CliError, CliErrorCode, CliResponse, RawCliOutput
from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas import (
    DEFAULT_TIME_IN_FORCE,
    DEFAULT_VENUE_ORDER_TYPE,
    ActorType,
    AuditAction,
    AuditIntegrityError,
    BaselineProposer,
    BaselineProposerConfig,
    BudgetIntegrityError,
    CloseoutResolution,
    ConfidenceLabel,
    InvalidTransitionError,
    MarketSnapshot,
    MarketSnapshotBuilder,
    MarketSnapshotBuildRequest,
    PaperFillReconciler,
    PolicyViolationError,
    ReplayReport,
    ReplayRunnerConfig,
    TradeDirection,
    TradeIdeaListQuery,
    TradeIdeaListResult,
    TradeIdeaListSortKey,
    TradeIdeaPositionSizingBridge,
    TradeIdeaReplayRunner,
    TradeIdeaService,
    TradeIdeaSizingConfig,
    TradeIdeaSizingContext,
    TradeIdeaSizingOutput,
    TradeIdeaState,
    UnknownTradeIdeaError,
    canonical_granularity,
    canonical_ticket_json,
    create_trade_idea_service,
    is_safe_decision_id,
    market_snapshot_to_payload,
    resolve_trade_idea_actor_id,
    validate_paper_reconciliation_profile,
)
from gpt_trader.features.trade_ideas.artifacts import (
    audit_events_to_csv,
    build_audit_export_artifact,
    build_audit_list_payload,
    build_closeout_export_artifact,
    build_closeout_list_payload,
    closeout_records_to_csv,
    trade_idea_report_to_csv,
)
from gpt_trader.features.trade_ideas.replay import _granularity_duration
from gpt_trader.features.trade_ideas.report import (
    build_trade_idea_track_record_report,
    format_trade_idea_track_record_report,
)
from gpt_trader.persistence.event_store import EventStore

VENUE_CHOICES = ("coinbase", "manual")
PAPER_RECONCILIATION_PROFILE_CHOICES = tuple(sorted({*options.PROFILE_CHOICES, "mock"}))
TEXT_JSON_FORMATS = ("text", "json")
REPORT_FORMATS = ("text", "json", "csv")
EXPORT_FORMATS = ("json", "csv")
BUDGET_FIELDS = (
    "max_loss_per_idea_pct",
    "max_daily_loss_pct",
    "max_open_notional_pct",
    "max_concurrent_approved_tickets",
    "max_review_latency_hours",
    "sizing_capped_by_budget",
    "gain_retention_floor_pct",
    "allow_futures_leverage",
    "allow_naked_shorts",
)


def register(subparsers: Any) -> None:
    """Register the ideas command group."""
    parser: ArgumentParser = subparsers.add_parser(
        "ideas",
        help="Review and audit trade ideas without broker execution",
        description=(
            "Thin CLI over the trade-idea approval workflow. "
            "No command places, modifies, or cancels broker orders."
        ),
    )
    ideas_subparsers = parser.add_subparsers(dest="ideas_command", required=True)

    propose = ideas_subparsers.add_parser("propose", help="Propose a new trade idea")
    _add_common_options(propose)
    _add_input_options(propose)
    _add_actor_options(propose)
    propose.add_argument(
        "--actor-type",
        choices=(ActorType.AI.value, ActorType.HUMAN.value),
        default=ActorType.AI.value,
        help="Actor type stamped into the proposed event",
    )
    propose.add_argument("--reason", default="New trade idea proposed", help="Audit reason")
    propose.set_defaults(handler=_handle_propose, subcommand="propose")

    propose_baseline = ideas_subparsers.add_parser(
        "propose-baseline",
        help="Generate proposals from a local market snapshot fixture",
        description=(
            "Generate deterministic BaselineProposer trade ideas from a local JSON "
            "market snapshot and persist them through the audited trade-idea service. "
            "This command reads no broker, account, credential, canary, or preflight data."
        ),
    )
    _add_common_options(propose_baseline)
    _add_actor_options(propose_baseline, default_description="baseline proposer id")
    propose_baseline.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="Read a local MarketSnapshot JSON fixture",
    )
    propose_baseline.add_argument(
        "--reason",
        default="Baseline proposer generated idea from local snapshot",
        help="Audit reason",
    )
    propose_baseline.set_defaults(handler=_handle_propose_baseline, subcommand="propose-baseline")

    snapshot = ideas_subparsers.add_parser(
        "snapshot",
        help="Build read-only market snapshots for proposer runs",
        description=(
            "Build MarketSnapshot JSON files for proposer and replay workflows. "
            "Snapshot build commands do not read accounts or submit orders."
        ),
    )
    snapshot_subparsers = snapshot.add_subparsers(dest="snapshot_command", required=True)
    snapshot_build = snapshot_subparsers.add_parser(
        "build",
        help="Fetch read-only Coinbase candles and write a MarketSnapshot JSON file",
        description=(
            "Fetch public Coinbase market candles, enforce point-in-time snapshot "
            "bounds, and write a JSON file accepted by ideas propose-baseline. "
            "This command requires --from-coinbase and never reads accounts or "
            "places, modifies, or cancels orders."
        ),
    )
    snapshot_build.add_argument(
        "--format",
        "--output-format",
        dest="output_format",
        type=str,
        choices=options.OUTPUT_FORMAT_CHOICES,
        default="text",
        help="Output format: text for human-readable, json for machine-readable",
    )
    snapshot_build.add_argument(
        "--output",
        "-o",
        dest="response_output_disallowed",
        type=Path,
        help=argparse.SUPPRESS,
    )
    snapshot_build.add_argument(
        "--from-coinbase",
        action="store_true",
        required=True,
        help="Explicitly fetch read-only public Coinbase market candles",
    )
    snapshot_build.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated Coinbase product ids, for example BTC-USD,ETH-USD",
    )
    snapshot_build.add_argument(
        "--granularity",
        required=True,
        help="Candle granularity, for example ONE_HOUR, FOUR_HOUR, ONE_DAY, 1H, 4H, or 1D",
    )
    snapshot_build.add_argument(
        "--lookback",
        type=_positive_int_value,
        required=True,
        help="Number of completed candles to include per symbol",
    )
    snapshot_build.add_argument(
        "--as-of",
        help="Snapshot as-of timestamp with timezone; defaults to current UTC time",
    )
    snapshot_build.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output MarketSnapshot JSON path",
    )
    snapshot_build.add_argument(
        "--source-label",
        default="coinbase:market-candles",
        help="Source label stamped into snapshot metadata",
    )
    snapshot_build.add_argument(
        "--coinbase-base-url",
        default="https://api.coinbase.com",
        help="Coinbase API base URL for market-data reads",
    )
    snapshot_build.set_defaults(handler=_handle_snapshot_build, subcommand="snapshot build")

    resubmit = ideas_subparsers.add_parser(
        "resubmit", help="Submit a revised record after requested changes"
    )
    _add_common_options(resubmit)
    _add_input_options(resubmit)
    _add_actor_options(resubmit)
    resubmit.add_argument(
        "--actor-type",
        choices=(ActorType.AI.value, ActorType.HUMAN.value),
        default=ActorType.AI.value,
        help="Actor type stamped into the resubmitted event",
    )
    resubmit.add_argument(
        "--reason", default="Revised after requested changes", help="Audit reason"
    )
    resubmit.set_defaults(handler=_handle_resubmit, subcommand="resubmit")

    list_parser = ideas_subparsers.add_parser("list", help="List stored trade ideas")
    _add_common_options(list_parser)
    list_parser.add_argument(
        "--state",
        choices=[state.value for state in TradeIdeaState],
        help="Filter by workflow state",
    )
    list_parser.add_argument("--instrument", help="Filter by exact instrument, case-insensitive")
    list_parser.add_argument("--decision-id", help="Filter by exact trade idea decision id")
    list_parser.add_argument(
        "--direction",
        choices=[direction.value for direction in TradeDirection],
        help="Filter by trade direction",
    )
    list_parser.add_argument(
        "--min-confidence",
        choices=[label.value for label in ConfidenceLabel],
        help="Minimum confidence label to include",
    )
    list_parser.add_argument(
        "--max-confidence",
        choices=[label.value for label in ConfidenceLabel],
        help="Maximum confidence label to include",
    )
    list_parser.add_argument(
        "--updated-since",
        type=_datetime_value,
        help="Filter by latest audit update at or after this ISO-8601 timestamp",
    )
    list_parser.add_argument(
        "--updated-until",
        type=_datetime_value,
        help="Filter by latest audit update at or before this ISO-8601 timestamp",
    )
    list_parser.add_argument(
        "--sort-by",
        choices=[sort_key.value for sort_key in TradeIdeaListSortKey],
        help="Sort list results by a trade-idea or audit-derived field",
    )
    list_parser.add_argument(
        "--descending",
        action="store_true",
        help="Sort descending instead of ascending",
    )
    list_parser.add_argument(
        "--limit",
        type=_positive_int_value,
        help="Maximum number of ideas to return",
    )
    list_parser.add_argument(
        "--offset",
        type=_non_negative_int_value,
        default=0,
        help="Number of matching ideas to skip before returning results",
    )
    list_parser.set_defaults(handler=_handle_list, subcommand="list")

    show = ideas_subparsers.add_parser("show", help="Show one trade idea")
    _add_common_options(show)
    show.add_argument("decision_id", help="Trade idea decision identifier")
    show.add_argument("--events", action="store_true", help="Include audit history")
    show.set_defaults(handler=_handle_show, subcommand="show")

    report = ideas_subparsers.add_parser(
        "report",
        help="Summarize proposal quality, workflow rates, and closeout coverage",
        description=(
            "Read-only track-record report over local trade-idea records, audit events, "
            "and closeout attribution. This command never contacts a broker or account."
        ),
    )
    _add_common_options(report, formats=REPORT_FORMATS)
    _add_window_options(report)
    report.add_argument(
        "--output-dir",
        type=Path,
        help="Write a durable report artifact into this directory",
    )
    report.set_defaults(handler=_handle_report, subcommand="report")

    export_ticket = ideas_subparsers.add_parser(
        "export-ticket",
        help="Export a deterministic broker-neutral ticket artifact",
        description=(
            "Render a deterministic broker-neutral ticket JSON artifact from an approved "
            "or terminal-approved trade idea. This command reads only local trade-idea "
            "records and never contacts a broker, account, preflight, or canary surface."
        ),
    )
    export_ticket.add_argument(
        "--ideas-root",
        type=Path,
        help="Trade-idea storage root (default: GPT_TRADER_IDEAS_ROOT, then var/data/trade_ideas)",
    )
    export_ticket.add_argument(
        "--format",
        "--output-format",
        dest="output_format",
        choices=options.OUTPUT_FORMAT_CHOICES,
        default="json",
        help="Output format: json for the ticket artifact (default), text for a summary",
    )
    export_ticket.add_argument(
        "--out",
        "--output",
        "-o",
        dest="ticket_output",
        type=Path,
        help="Write the ticket artifact to this path instead of stdout",
    )
    export_ticket.add_argument(
        "--decision-id",
        required=True,
        help="Approved or terminal-approved trade idea decision identifier",
    )
    export_ticket.add_argument("--venue", required=True, choices=VENUE_CHOICES)
    export_ticket.add_argument(
        "--venue-order-type",
        default=DEFAULT_VENUE_ORDER_TYPE,
        help="Broker-neutral order type placeholder for the downstream operator",
    )
    export_ticket.add_argument(
        "--time-in-force",
        default=DEFAULT_TIME_IN_FORCE,
        help="Broker-neutral time-in-force placeholder for the downstream operator",
    )
    export_ticket.add_argument(
        "--client-order-id",
        help="Optional broker-neutral client order id; defaults deterministically",
    )
    export_ticket.set_defaults(handler=_handle_export_ticket, subcommand="export-ticket")

    replay = ideas_subparsers.add_parser(
        "replay",
        help="Replay proposer calibration against local candle fixtures",
        description=(
            "Read-only calibration commands over local candle fixtures. "
            "Replay commands never contact brokers, accounts, or live market data."
        ),
    )
    replay_subparsers = replay.add_subparsers(dest="replay_command", required=True)
    baseline = replay_subparsers.add_parser(
        "baseline",
        help="Replay the deterministic baseline proposer over candle history",
        description=(
            "Feed a local OHLCV candle fixture to the deterministic baseline proposer "
            "and summarize replay scoring. This command is broker-free and read-only."
        ),
    )
    _add_output_options(baseline)
    baseline.add_argument(
        "--file",
        type=Path,
        required=True,
        help="JSON fixture with a top-level candles array of OHLCV bars",
    )
    baseline.add_argument("--symbol", required=True, help="Symbol represented by the candles")
    baseline.add_argument(
        "--granularity",
        required=True,
        help="Candle granularity, for example ONE_HOUR, 1H, or 1D",
    )
    baseline.add_argument(
        "--source",
        default="fixture:candles",
        help="Source label stamped into point-in-time replay snapshots",
    )
    baseline.add_argument(
        "--min-history",
        type=_positive_int_value,
        help=(
            "Minimum historical candles before evaluating snapshots "
            "(default: max(short-window, long-window) + crossover-lookback)"
        ),
    )
    baseline.add_argument("--short-window", type=_positive_int_value, default=10)
    baseline.add_argument("--long-window", type=_positive_int_value, default=50)
    baseline.add_argument("--crossover-lookback", type=_positive_int_value, default=3)
    baseline.add_argument(
        "--risk-per-idea-pct",
        type=_non_negative_decimal_value,
        default=Decimal("2"),
    )
    baseline.add_argument(
        "--entry-band-pct",
        type=_positive_decimal_value,
        default=Decimal("1"),
    )
    baseline.add_argument(
        "--reward-multiple",
        type=_positive_decimal_value,
        default=Decimal("2"),
    )
    baseline.add_argument("--expiry-hours", type=_positive_int_value, default=48)
    baseline.add_argument("--expected-hold", default="5-15 days")
    baseline.add_argument(
        "--price-precision",
        type=_positive_decimal_value,
        default=Decimal("0.01"),
    )
    baseline.set_defaults(handler=_handle_replay_baseline, subcommand="replay baseline")

    closeout = ideas_subparsers.add_parser(
        "closeout",
        help="Record or inspect terminal closeout attribution",
        description=(
            "Record and inspect broker-neutral closeout attribution for terminal trade "
            "ideas. This command never contacts a broker or account."
        ),
    )
    closeout_subparsers = closeout.add_subparsers(dest="closeout_command", required=True)

    closeout_record = closeout_subparsers.add_parser(
        "record",
        help="Record terminal closeout attribution; does not call a broker API",
        description=(
            "Append closeout attribution for a terminal trade idea. This records external "
            "evidence only and never places, modifies, or cancels broker orders."
        ),
    )
    _add_common_options(closeout_record)
    _add_actor_options(closeout_record)
    closeout_record.add_argument("decision_id", help="Trade idea decision identifier")
    closeout_record.add_argument(
        "--resolution",
        required=True,
        choices=[resolution.value for resolution in CloseoutResolution],
        help="Why the terminal idea resolved",
    )
    closeout_record.add_argument(
        "--realized-profit-loss-amount",
        type=_decimal_value,
        help="Realized profit/loss amount; negative values represent losses",
    )
    closeout_record.add_argument(
        "--realized-profit-loss-percent",
        type=_decimal_value,
        help="Realized profit/loss percent; negative values represent losses",
    )
    closeout_record.add_argument(
        "--realized-profit-loss-unavailable-reason",
        default="",
        help="Why realized profit/loss is unavailable",
    )
    closeout_record.add_argument(
        "--evidence",
        action="append",
        default=None,
        help="Evidence string to attach; repeat for multiple evidence entries",
    )
    closeout_record.add_argument(
        "--actor-type",
        choices=(ActorType.HUMAN.value, ActorType.SYSTEM.value),
        default=ActorType.HUMAN.value,
        help="Actor type stamped into the closeout attribution record",
    )
    closeout_record.set_defaults(handler=_handle_closeout_record, subcommand="closeout record")

    closeout_show = closeout_subparsers.add_parser(
        "show",
        help="Show closeout attribution for one trade idea",
        description="Read closeout attribution from local trade-idea records only.",
    )
    _add_common_options(closeout_show)
    closeout_show.add_argument("decision_id", help="Trade idea decision identifier")
    closeout_show.set_defaults(handler=_handle_closeout_show, subcommand="closeout show")

    closeout_list = closeout_subparsers.add_parser(
        "list",
        help="List closeout attribution records with filters",
        description="Read a paginated closeout attribution view from local storage.",
    )
    _add_common_options(closeout_list)
    _add_closeout_filter_options(closeout_list)
    _add_pagination_options(closeout_list, default_limit=50)
    closeout_list.set_defaults(handler=_handle_closeout_list, subcommand="closeout list")

    closeout_export = closeout_subparsers.add_parser(
        "export",
        help="Export closeout attribution records as JSON or CSV",
        description="Export local closeout attribution rows without mutating records.",
    )
    _add_common_options(closeout_export, formats=EXPORT_FORMATS, default_format="json")
    _add_closeout_filter_options(closeout_export)
    _add_pagination_options(closeout_export, default_limit=None)
    closeout_export.add_argument(
        "--output-dir",
        type=Path,
        help="Write a durable closeout export artifact into this directory",
    )
    closeout_export.set_defaults(handler=_handle_closeout_export, subcommand="closeout export")

    approve = ideas_subparsers.add_parser("approve", help="Approve a proposed trade idea")
    _add_common_options(approve)
    _add_actor_options(approve)
    approve.add_argument("decision_id", help="Trade idea decision identifier")
    approve.add_argument("--reason", required=True, help="Human approval reason")
    approve.set_defaults(handler=_handle_approve, subcommand="approve")

    reject = ideas_subparsers.add_parser("reject", help="Reject a proposed trade idea")
    _add_common_options(reject)
    _add_actor_options(reject)
    reject.add_argument("decision_id", help="Trade idea decision identifier")
    reject.add_argument("--reason", required=True, help="Human rejection reason")
    reject.set_defaults(handler=_handle_reject, subcommand="reject")

    request_changes = ideas_subparsers.add_parser(
        "request-changes", help="Request changes to a proposed trade idea"
    )
    _add_common_options(request_changes)
    _add_actor_options(request_changes)
    request_changes.add_argument("decision_id", help="Trade idea decision identifier")
    request_changes.add_argument("--reason", required=True, help="Requested change reason")
    request_changes.set_defaults(handler=_handle_request_changes, subcommand="request-changes")

    cancel = ideas_subparsers.add_parser("cancel", help="Cancel an approved or submitted idea")
    _add_common_options(cancel)
    _add_actor_options(cancel)
    cancel.add_argument("decision_id", help="Trade idea decision identifier")
    cancel.add_argument("--reason", required=True, help="Human cancellation reason")
    cancel.set_defaults(handler=_handle_cancel, subcommand="cancel")

    expire = ideas_subparsers.add_parser("expire", help="Expire one idea or sweep stale ideas")
    _add_common_options(expire)
    _add_actor_options(expire)
    expire.add_argument("decision_id", nargs="?", help="Trade idea decision identifier")
    expire.add_argument("--sweep", action="store_true", help="Expire every stale non-terminal idea")
    expire.add_argument(
        "--reason",
        default="Idea passed its review or execution deadline",
        help="Expiry audit reason",
    )
    expire.set_defaults(handler=_handle_expire, subcommand="expire")

    mark_submitted = ideas_subparsers.add_parser(
        "mark-submitted",
        help="Record a manually submitted approved ticket; does not call a broker API",
        description=(
            "Append an audit event for a ticket submitted outside this CLI. "
            "This command never places, modifies, or cancels broker orders."
        ),
    )
    _add_common_options(mark_submitted)
    _add_actor_options(mark_submitted)
    mark_submitted.add_argument("decision_id", help="Trade idea decision identifier")
    mark_submitted.add_argument("--venue", required=True, choices=VENUE_CHOICES)
    mark_submitted.add_argument("--external-order-id", default="", help="External order id")
    mark_submitted.add_argument("--reason", default="Approved ticket submitted")
    mark_submitted.add_argument(
        "--actor-type",
        choices=(ActorType.SYSTEM.value, ActorType.HUMAN.value),
        default=ActorType.SYSTEM.value,
        help="Actor type stamped into the submitted event",
    )
    mark_submitted.set_defaults(handler=_handle_mark_submitted, subcommand="mark-submitted")

    mark_filled = ideas_subparsers.add_parser(
        "mark-filled",
        help="Record a manually observed fill; does not call a broker API",
        description=(
            "Append an audit event for a fill observed outside this CLI. "
            "This command never contacts a broker or account."
        ),
    )
    _add_common_options(mark_filled)
    _add_actor_options(mark_filled)
    mark_filled.add_argument("decision_id", help="Trade idea decision identifier")
    mark_filled.add_argument("--venue", required=True, choices=VENUE_CHOICES)
    mark_filled.add_argument("--external-order-id", default="", help="External order id")
    mark_filled.add_argument("--reason", default="Venue confirmed fill")
    mark_filled.set_defaults(handler=_handle_mark_filled, subcommand="mark-filled")

    reconcile_paper_fills = ideas_subparsers.add_parser(
        "reconcile-paper-fills",
        help="Reconcile persisted paper/mock fill events to approved ideas",
        description=(
            "Read existing EventStore trade events and match paper/mock fills to approved "
            "trade ideas. Defaults to dry-run; --apply appends audit events only through "
            "TradeIdeaService. This command never contacts a broker or account."
        ),
    )
    _add_common_options(reconcile_paper_fills)
    _add_actor_options(
        reconcile_paper_fills,
        default_description="paper-fill-reconciler unless --actor is provided",
    )
    reconcile_paper_fills.add_argument(
        "--profile",
        required=True,
        choices=PAPER_RECONCILIATION_PROFILE_CHOICES,
        help="Runtime profile whose paper/mock event store should be reconciled",
    )
    reconcile_paper_fills.add_argument(
        "--event-store-root",
        type=Path,
        help="Runtime EventStore root (default: bot runtime path for profile)",
    )
    reconcile_paper_fills.add_argument(
        "--venue",
        choices=VENUE_CHOICES,
        default="manual",
        help="Audit venue to record for matched fills",
    )
    reconcile_paper_fills.add_argument(
        "--limit",
        type=_positive_int_value,
        help="Read only the most recent N trade events",
    )
    reconcile_paper_fills.add_argument(
        "--apply",
        action="store_true",
        help="Append matched submission/fill audit events; omitted means dry-run",
    )
    reconcile_paper_fills.set_defaults(
        handler=_handle_reconcile_paper_fills,
        subcommand="reconcile-paper-fills",
    )

    budget = ideas_subparsers.add_parser("budget", help="Inspect or update risk budget")
    budget_subparsers = budget.add_subparsers(dest="budget_command", required=True)

    budget_show = budget_subparsers.add_parser("show", help="Show current risk budget")
    _add_common_options(budget_show)
    budget_show.set_defaults(handler=_handle_budget_show, subcommand="budget show")

    budget_set = budget_subparsers.add_parser("set", help="Set a new risk budget version")
    _add_common_options(budget_set)
    _add_actor_options(budget_set)
    budget_set.add_argument("--reason", required=True, help="Reason for this budget version")
    budget_set.add_argument("--max-loss-per-idea-pct", type=_non_negative_decimal_value)
    budget_set.add_argument("--max-daily-loss-pct", type=_non_negative_decimal_value)
    budget_set.add_argument("--max-open-notional-pct", type=_non_negative_decimal_value)
    budget_set.add_argument("--max-concurrent-approved-tickets", type=_non_negative_int_value)
    budget_set.add_argument("--max-review-latency-hours", type=_non_negative_int_value)
    budget_set.add_argument(
        "--sizing-capped-by-budget",
        choices=("true", "false"),
        help="Whether sizing is capped by budget",
    )
    budget_set.add_argument("--gain-retention-floor-pct", type=_non_negative_decimal_value)
    budget_set.add_argument(
        "--allow-futures-leverage",
        choices=("true", "false"),
        help="Whether futures leverage is permitted",
    )
    budget_set.add_argument(
        "--allow-naked-shorts",
        choices=("true", "false"),
        help="Whether naked shorts are permitted",
    )
    budget_set.set_defaults(handler=_handle_budget_set, subcommand="budget set")

    audit = ideas_subparsers.add_parser("audit", help="Read and verify the audit log")
    audit_subparsers = audit.add_subparsers(dest="audit_command", required=True)

    audit_list = audit_subparsers.add_parser("list", help="List audit events with filters")
    _add_common_options(audit_list)
    _add_audit_filter_options(audit_list)
    _add_pagination_options(audit_list, default_limit=50)
    audit_list.set_defaults(handler=_handle_audit_list, subcommand="audit list")

    audit_export = audit_subparsers.add_parser(
        "export",
        help="Export audit events as JSON or CSV",
        description="Export local append-only audit events without rewriting the log.",
    )
    _add_common_options(audit_export, formats=EXPORT_FORMATS, default_format="json")
    _add_audit_filter_options(audit_export)
    _add_pagination_options(audit_export, default_limit=None)
    audit_export.add_argument(
        "--output-dir",
        type=Path,
        help="Write a durable audit export artifact into this directory",
    )
    audit_export.set_defaults(handler=_handle_audit_export, subcommand="audit export")

    audit_tail = audit_subparsers.add_parser("tail", help="Show recent audit events")
    _add_common_options(audit_tail)
    audit_tail.add_argument("-n", "--count", type=int, default=20)
    audit_tail.add_argument("--decision-id", help="Filter events by decision id")
    audit_tail.set_defaults(handler=_handle_audit_tail, subcommand="audit tail")

    audit_verify = audit_subparsers.add_parser("verify", help="Verify audit log integrity")
    _add_common_options(audit_verify)
    audit_verify.set_defaults(handler=_handle_audit_verify, subcommand="audit verify")


def _add_common_options(
    parser: ArgumentParser,
    *,
    formats: tuple[str, ...] = TEXT_JSON_FORMATS,
    default_format: str = "text",
) -> None:
    parser.add_argument(
        "--ideas-root",
        type=Path,
        help="Trade-idea storage root (default: GPT_TRADER_IDEAS_ROOT, then var/data/trade_ideas)",
    )
    _add_output_options(parser, formats=formats, default_format=default_format)


def _add_output_options(
    parser: ArgumentParser,
    *,
    formats: tuple[str, ...] = TEXT_JSON_FORMATS,
    default_format: str = "text",
) -> None:
    parser.add_argument(
        "--format",
        "--output-format",
        dest="output_format",
        choices=formats,
        default=default_format,
        help="Output format",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Write output to file instead of stdout",
    )


def _add_window_options(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--since",
        "--from",
        dest="since",
        type=_datetime_value,
        help="Include records at or after this ISO timestamp or YYYY-MM-DD date",
    )
    parser.add_argument(
        "--until",
        "--to",
        dest="until",
        type=_datetime_value,
        help="Include records at or before this ISO timestamp or YYYY-MM-DD date",
    )


def _add_pagination_options(
    parser: ArgumentParser,
    *,
    default_limit: int | None,
) -> None:
    parser.add_argument("--limit", type=_non_negative_int_value, default=default_limit)
    parser.add_argument("--offset", type=_non_negative_int_value, default=0)


def _add_audit_filter_options(parser: ArgumentParser) -> None:
    parser.add_argument("--decision-id", help="Filter events by decision id")
    parser.add_argument("--actor", dest="actor_id", help="Filter events by actor id")
    parser.add_argument(
        "--actor-type",
        choices=[actor_type.value for actor_type in ActorType],
        help="Filter events by actor type",
    )
    parser.add_argument(
        "--action",
        choices=[action.value for action in AuditAction],
        help="Filter events by audit action",
    )
    parser.add_argument(
        "--state",
        choices=[state.value for state in TradeIdeaState],
        help="Filter events by resulting workflow state",
    )
    _add_window_options(parser)


def _add_closeout_filter_options(parser: ArgumentParser) -> None:
    parser.add_argument("--decision-id", help="Filter closeouts by decision id")
    parser.add_argument("--actor", dest="actor_id", help="Filter closeouts by actor id")
    parser.add_argument(
        "--actor-type",
        choices=[actor_type.value for actor_type in ActorType],
        help="Filter closeouts by actor type",
    )
    parser.add_argument(
        "--resolution",
        choices=[resolution.value for resolution in CloseoutResolution],
        help="Filter closeouts by resolution",
    )
    parser.add_argument(
        "--has-evidence",
        choices=("true", "false"),
        help="Filter closeouts by whether evidence strings are present",
    )
    _add_window_options(parser)


def _add_input_options(parser: ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Read TradeIdea JSON from file")
    source.add_argument("--stdin", action="store_true", help="Read TradeIdea JSON from stdin")


def _add_actor_options(
    parser: ArgumentParser,
    *,
    default_description: str = "GPT_TRADER_ACTOR, then OS user",
) -> None:
    parser.add_argument(
        "--actor",
        help=f"Actor id stamped into audit events (default: {default_description})",
    )


def _decimal_value(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"invalid decimal value: {value}") from error
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError(f"decimal value must be finite: {value}")
    return parsed


def _non_negative_decimal_value(value: str) -> Decimal:
    parsed = _decimal_value(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"decimal value must be non-negative: {value}")
    return parsed


def _non_negative_int_value(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value}") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"integer value must be non-negative: {value}")
    return parsed


def _positive_decimal_value(value: str) -> Decimal:
    parsed = _decimal_value(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"decimal value must be positive: {value}")
    return parsed


def _positive_int_value(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value}") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"integer value must be positive: {value}")
    return parsed


def _datetime_value(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO datetime/date: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _bool_value(value: str) -> bool:
    return value.lower() == "true"


def _service(args: Namespace) -> TradeIdeaService:
    return create_trade_idea_service(getattr(args, "ideas_root", None))


def _actor_id(args: Namespace) -> str:
    return resolve_trade_idea_actor_id(getattr(args, "actor", None))


def _paper_reconciliation_actor_id(args: Namespace) -> str:
    explicit_actor = getattr(args, "actor", None)
    return resolve_trade_idea_actor_id(explicit_actor or "paper-fill-reconciler")


def _paper_reconciliation_event_store_root(args: Namespace, profile: str) -> Path:
    configured_root = getattr(args, "event_store_root", None)
    if configured_root is not None:
        return cast(Path, configured_root)
    profile_root = "dev" if profile == "mock" else profile
    runtime_paths = resolve_runtime_paths(
        config=BotConfig.from_env(),
        profile=profile_root,
    )
    return runtime_paths.event_store_root


def _output_format(args: Namespace) -> str:
    return getattr(args, "output_format", "text")


def _default_replay_min_history(config: BaselineProposerConfig) -> int:
    return max(config.short_window, config.long_window) + config.crossover_lookback


def _resolve_replay_min_history(args: Namespace, config: BaselineProposerConfig) -> int:
    minimum_history = _default_replay_min_history(config)
    requested_min_history = cast(int | None, args.min_history)
    if requested_min_history is None:
        return minimum_history
    if requested_min_history < minimum_history:
        raise CandleInputError(
            (
                "--min-history must be at least "
                f"{minimum_history} for short-window={config.short_window}, "
                f"long-window={config.long_window}, "
                f"crossover-lookback={config.crossover_lookback}"
            ),
            field="min_history",
        )
    return requested_min_history


def _validate_replay_granularity(granularity: str) -> None:
    if _granularity_duration(granularity) is None:
        raise CandleInputError(
            f"Unsupported replay granularity: {granularity}",
            field="granularity",
        )


def _handle_propose(args: Namespace) -> CliResponse:
    command = "ideas propose"
    try:
        idea = _load_trade_idea(args)
        service = _service(args)
        service.validate_new_proposal(idea)
        violations = service.approval_violations(idea, actor_type=ActorType.HUMAN)
        view = service.propose(
            idea,
            actor_id=_actor_id(args),
            actor_type=ActorType(args.actor_type),
            reason=args.reason,
        )
    except IdeaInputError as error:
        return _input_error(command, args, error)
    except Exception as error:
        return _mapped_error(command, args, error)

    payload = {
        **_view_summary(view),
        "record_hash": view.idea.record_hash(),
        "violations": violations,
    }
    warning_messages = [f"would fail approval: {violation}" for violation in violations]
    text = _status_line(command, "OK", f"{view.idea.decision_id}, state={view.state.value}")
    if violations:
        text += "\n" + "\n".join(f"⚠ would fail approval: {violation}" for violation in violations)
    return _success(command, args, payload, text, warnings=warning_messages)


class _LazyBudgetSizingBridge(TradeIdeaPositionSizingBridge):
    """Defer the budget read (and its seed-on-first-use side effect) until a
    candidate actually needs sizing, so no-signal baseline runs stay read-only."""

    def __init__(self, service: TradeIdeaService) -> None:
        self._budget_service = service
        self._delegate: TradeIdeaPositionSizingBridge | None = None

    def recommend(self, context: TradeIdeaSizingContext) -> TradeIdeaSizingOutput:
        if self._delegate is None:
            self._delegate = TradeIdeaPositionSizingBridge(
                TradeIdeaSizingConfig(risk_budget=self._budget_service.current_budget())
            )
        return self._delegate.recommend(context)


def _handle_propose_baseline(args: Namespace) -> CliResponse:
    command = "ideas propose-baseline"
    try:
        snapshot = _load_market_snapshot(args.snapshot)
        service = _service(args)
        proposer = BaselineProposer(sizing_bridge=_LazyBudgetSizingBridge(service))
        ideas = proposer.propose(snapshot)
        if not ideas:
            payload = _baseline_payload(snapshot, proposer.proposer_id, [])
            text = _status_line(command, "OK", "0 proposals")
            return _success(command, args, payload, text, was_noop=True)

        proposal_batch = tuple(ideas)
        service.validate_new_proposals(proposal_batch)
        previews = [service.approval_violations(idea, actor_type=ActorType.HUMAN) for idea in ideas]
        actor_id = _baseline_actor_id(args, proposer.proposer_id)
        views = service.propose_batch(
            proposal_batch,
            actor_id=actor_id,
            actor_type=ActorType.AI,
            reason=args.reason,
            evidence=_baseline_evidence(args.snapshot, snapshot, proposer.proposer_id),
        )
    except SnapshotInputError as error:
        return _input_error(command, args, error)
    except Exception as error:
        return _mapped_error(command, args, error)

    proposed = [
        _baseline_proposed_summary(view, violations)
        for view, violations in zip(views, previews, strict=True)
    ]
    payload = _baseline_payload(snapshot, proposer.proposer_id, proposed)
    warnings = [
        warning for proposal in proposed for warning in proposal["approval_preview"]["warnings"]
    ]
    text = _baseline_text(command, proposed)
    return _success(command, args, payload, text, warnings=warnings)


def _handle_snapshot_build(args: Namespace) -> CliResponse:
    command = "ideas snapshot build"
    try:
        if getattr(args, "response_output_disallowed", None) is not None:
            raise SnapshotBuildInputError(
                "ideas snapshot build does not support response --output; use --out for the "
                "MarketSnapshot JSON file",
                field="output",
            )
        request = MarketSnapshotBuildRequest(
            symbols=_snapshot_symbols(args.symbols),
            granularity=_snapshot_granularity(args.granularity),
            lookback=args.lookback,
            as_of=_snapshot_as_of(args.as_of),
        )
        snapshot = asyncio.run(_build_coinbase_market_snapshot(args, request))
        payload = market_snapshot_to_payload(snapshot)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            f"{json.dumps(payload, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )
    except SnapshotBuildInputError as error:
        return _input_error(command, args, error)
    except Exception as error:
        return _mapped_error(command, args, error)

    summary = _snapshot_build_payload(snapshot, args.out)
    text = _status_line(
        command,
        "OK",
        f"{len(snapshot.series)} series -> {args.out}",
    )
    return _success(command, args, summary, text)


async def _build_coinbase_market_snapshot(
    args: Namespace,
    request: MarketSnapshotBuildRequest,
) -> MarketSnapshot:
    from gpt_trader.backtesting.data.fetcher import CoinbaseHistoricalFetcher
    from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient

    client = CoinbaseClient(
        base_url=args.coinbase_base_url,
        auth=None,
        api_mode="advanced",
    )
    try:
        builder = MarketSnapshotBuilder(
            CoinbaseHistoricalFetcher(client=client),
            source_label=args.source_label,
        )
        return await builder.build(request)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _snapshot_symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(symbol.strip().upper() for symbol in value.split(",") if symbol.strip())
    if not symbols:
        raise SnapshotBuildInputError("--symbols must include at least one symbol", field="symbols")
    if len(set(symbols)) != len(symbols):
        raise SnapshotBuildInputError("--symbols must not contain duplicates", field="symbols")
    return symbols


def _snapshot_granularity(value: str) -> str:
    canonical = canonical_granularity(value)
    if canonical is None:
        raise SnapshotBuildInputError(
            f"Unsupported snapshot granularity: {value}",
            field="granularity",
        )
    return canonical


def _snapshot_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise SnapshotBuildInputError(
            "--as-of must be an ISO datetime string",
            field="as_of",
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotBuildInputError("--as-of must include a timezone", field="as_of")
    return parsed


def _snapshot_build_payload(snapshot: MarketSnapshot, output_path: Path) -> dict[str, Any]:
    return {
        "out": str(output_path),
        "snapshot": {
            "as_of": snapshot.as_of.isoformat(),
            "source": snapshot.source,
            "symbols": list(snapshot.symbols()),
            "series": [
                {
                    "symbol": symbol_series.symbol,
                    "granularity": symbol_series.granularity,
                    "candle_count": len(symbol_series.candles),
                    "first_ts": (
                        symbol_series.candles[0].ts.isoformat() if symbol_series.candles else None
                    ),
                    "last_ts": (
                        symbol_series.candles[-1].ts.isoformat() if symbol_series.candles else None
                    ),
                }
                for symbol_series in snapshot.series
            ],
        },
    }


def _handle_resubmit(args: Namespace) -> CliResponse:
    command = "ideas resubmit"
    try:
        idea = _load_trade_idea(args)
        service = _service(args)
        service.validate_resubmission(idea)
        violations = service.approval_violations(idea, actor_type=ActorType.HUMAN)
        view = service.resubmit(
            idea,
            actor_id=_actor_id(args),
            actor_type=ActorType(args.actor_type),
            reason=args.reason,
        )
    except IdeaInputError as error:
        return _input_error(command, args, error)
    except Exception as error:
        return _mapped_error(command, args, error)

    payload = {
        **_view_summary(view),
        "record_hash": view.idea.record_hash(),
        "violations": violations,
    }
    warning_messages = [f"would fail approval: {violation}" for violation in violations]
    text = _status_line(command, "OK", f"{view.idea.decision_id}, state={view.state.value}")
    if violations:
        text += "\n" + "\n".join(f"⚠ would fail approval: {violation}" for violation in violations)
    return _success(command, args, payload, text, warnings=warning_messages)


def _baseline_actor_id(args: Namespace, proposer_id: str) -> str:
    explicit_actor = getattr(args, "actor", None)
    return resolve_trade_idea_actor_id(explicit_actor or proposer_id)


def _baseline_evidence(
    snapshot_path: Path,
    snapshot: MarketSnapshot,
    proposer_id: str,
) -> tuple[str, ...]:
    return (
        f"proposer_id={proposer_id}",
        f"snapshot_path={snapshot_path}",
        f"snapshot_source={snapshot.source}",
        f"snapshot_as_of={snapshot.as_of.isoformat()}",
    )


def _baseline_proposed_summary(view: Any, violations: list[str]) -> dict[str, Any]:
    warning_messages = [f"would fail approval: {violation}" for violation in violations]
    return {
        **_view_summary(view),
        "record_hash": view.idea.record_hash(),
        "approval_preview": {
            "violations": violations,
            "warnings": warning_messages,
        },
    }


def _baseline_payload(
    snapshot: MarketSnapshot,
    proposer_id: str,
    proposed: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "proposer_id": proposer_id,
        "snapshot": {
            "as_of": snapshot.as_of.isoformat(),
            "source": snapshot.source,
            "symbols": list(snapshot.symbols()),
        },
        "proposal_count": len(proposed),
        "proposed": proposed,
    }


def _baseline_text(command: str, proposed: list[dict[str, Any]]) -> str:
    lines = [_status_line(command, "OK", f"{len(proposed)} proposals")]
    for proposal in proposed:
        lines.append(
            "{decision_id}  state={state}  record_hash={record_hash}".format(
                decision_id=proposal["decision_id"],
                state=proposal["state"],
                record_hash=proposal["record_hash"],
            )
        )
        for warning in proposal["approval_preview"]["warnings"]:
            lines.append(f"⚠ {proposal['decision_id']}: {warning}")
    return "\n".join(lines)


def _list_query_from_args(args: Namespace) -> TradeIdeaListQuery:
    return TradeIdeaListQuery(
        state=TradeIdeaState(args.state) if args.state else None,
        instrument=args.instrument,
        decision_id=args.decision_id,
        direction=TradeDirection(args.direction) if args.direction else None,
        min_confidence=ConfidenceLabel(args.min_confidence) if args.min_confidence else None,
        max_confidence=ConfidenceLabel(args.max_confidence) if args.max_confidence else None,
        updated_since=args.updated_since,
        updated_until=args.updated_until,
        sort_by=TradeIdeaListSortKey(args.sort_by) if args.sort_by else None,
        descending=bool(args.descending),
        limit=args.limit,
        offset=args.offset,
    )


def _list_metadata(result: TradeIdeaListResult) -> dict[str, Any]:
    return {
        "total_count": result.total_count,
        "returned_count": result.returned_count,
        "offset": result.offset,
        "limit": result.limit,
        "has_more": result.has_more,
    }


def _handle_list(args: Namespace) -> CliResponse:
    command = "ideas list"
    try:
        query = _list_query_from_args(args)
        result = _service(args).list_view_result(query)
    except Exception as error:
        return _mapped_error(command, args, error)

    ideas = [_view_summary(view) for view in result.views]
    text = _ideas_table(ideas)
    payload = {"ideas": ideas, **_list_metadata(result)}
    return _success(command, args, payload, text, was_noop=not ideas)


def _handle_show(args: Namespace) -> CliResponse:
    command = "ideas show"
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        view = _service(args).get(args.decision_id)
    except Exception as error:
        return _mapped_error(command, args, error)

    payload = _view_record(view, include_events=args.events)
    text = _record_text(payload, include_events=args.events)
    return _success(command, args, payload, text)


def _handle_report(args: Namespace) -> CliResponse:
    command = "ideas report"
    window_error = _window_error(command, args)
    if window_error is not None:
        return window_error
    try:
        payload = build_trade_idea_track_record_report(
            _service(args),
            since=args.since,
            until=args.until,
        )
    except Exception as error:
        return _mapped_error(command, args, error)

    if _output_format(args) == "csv":
        content = trade_idea_report_to_csv(payload)
        _write_output_dir_artifact(
            args,
            stem="trade-idea-report",
            artifact_id=payload["quality_report_id"],
            extension="csv",
            content=content,
        )
        return CliResponse.success_response(
            command=command,
            data=content,
            was_noop=payload["proposal_volume"]["idea_count"] == 0,
        )

    artifact_path = _write_output_dir_artifact(
        args,
        stem="trade-idea-report",
        artifact_id=payload["quality_report_id"],
        extension="json",
        content=_json_artifact(payload),
    )
    if artifact_path is not None:
        payload = {**payload, "artifact_path": artifact_path}

    text = format_trade_idea_track_record_report(payload)
    if artifact_path is not None:
        text += f"\nartifact_path: {artifact_path}"
    idea_count = payload["proposal_volume"]["idea_count"]
    return _success(command, args, payload, text, was_noop=idea_count == 0)


def _handle_export_ticket(args: Namespace) -> CliResponse | RawCliOutput:
    command = "ideas export-ticket"
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        payload = _service(args).export_broker_ticket_payload(
            args.decision_id,
            venue=args.venue,
            venue_order_type=args.venue_order_type,
            time_in_force=args.time_in_force,
            client_order_id=args.client_order_id,
        )
        ticket_json = canonical_ticket_json(payload)
        ticket_output = getattr(args, "ticket_output", None)
        if ticket_output is not None:
            ticket_output.parent.mkdir(parents=True, exist_ok=True)
            ticket_output.write_text(ticket_json, encoding="utf-8")
            if _output_format(args) == "text":
                return RawCliOutput(_ticket_export_text(payload, written_to=ticket_output) + "\n")
            return RawCliOutput("")
        if _output_format(args) == "text":
            return RawCliOutput(_ticket_export_text(payload) + "\n")
        return RawCliOutput(ticket_json)
    except OSError as error:
        return _failure(
            command,
            args,
            CliErrorCode.OPERATION_FAILED,
            f"Could not write ticket artifact: {error}",
            details={"path": str(getattr(args, "ticket_output", ""))},
        )
    except Exception as error:
        return _mapped_error(command, args, error)


def _handle_replay_baseline(args: Namespace) -> CliResponse:
    command = "ideas replay baseline"
    try:
        _validate_replay_granularity(args.granularity)
        candles = _load_candle_fixture(args.file)
        proposer_config = BaselineProposerConfig(
            short_window=args.short_window,
            long_window=args.long_window,
            crossover_lookback=args.crossover_lookback,
            risk_per_idea_pct=args.risk_per_idea_pct,
            entry_band_pct=args.entry_band_pct,
            reward_multiple=args.reward_multiple,
            expiry_hours=args.expiry_hours,
            expected_hold=args.expected_hold,
            price_precision=args.price_precision,
        )
        min_history = _resolve_replay_min_history(args, proposer_config)
        report = TradeIdeaReplayRunner(
            BaselineProposer(proposer_config),
            config=ReplayRunnerConfig(source=args.source, min_history=min_history),
        ).run_series(symbol=args.symbol, granularity=args.granularity, candles=candles)
    except CandleInputError as error:
        return _input_error(command, args, error)
    except Exception as error:
        return _mapped_error(command, args, error)

    payload = report.to_dict()
    text = _replay_report_text(report)
    return _success(command, args, payload, text, was_noop=report.ideas_proposed == 0)


def _handle_closeout_record(args: Namespace) -> CliResponse:
    command = "ideas closeout record"
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    profit_loss_error = _closeout_profit_loss_error(command, args)
    if profit_loss_error is not None:
        return profit_loss_error
    evidence_error = _evidence_error(command, args)
    if evidence_error is not None:
        return evidence_error

    try:
        record = _service(args).record_closeout_attribution(
            args.decision_id,
            actor_id=_actor_id(args),
            actor_type=ActorType(args.actor_type),
            resolution=args.resolution,
            realized_profit_loss_amount=args.realized_profit_loss_amount,
            realized_profit_loss_percent=args.realized_profit_loss_percent,
            realized_profit_loss_unavailable_reason=(
                args.realized_profit_loss_unavailable_reason.strip()
            ),
            evidence=_evidence(args),
        )
    except Exception as error:
        return _mapped_error(command, args, error)
    return _closeout_success(command, args, record)


def _handle_closeout_show(args: Namespace) -> CliResponse:
    command = "ideas closeout show"
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        record = _service(args).get_closeout_attribution(args.decision_id)
    except Exception as error:
        return _mapped_error(command, args, error)

    payload = {
        "decision_id": args.decision_id,
        "closeout_attribution": record.to_dict() if record is not None else None,
    }
    if record is None:
        text = _status_line(command, "OK", f"{args.decision_id}, no closeout attribution")
        return _success(command, args, payload, text, was_noop=True)

    text = _closeout_text(command, record.to_dict())
    return _success(command, args, payload, text)


def _handle_closeout_list(args: Namespace) -> CliResponse:
    command = "ideas closeout list"
    filter_error = _closeout_filter_error(command, args)
    if filter_error is not None:
        return filter_error
    try:
        service = _service(args)
        page = service.query_closeout_records(
            decision_id=args.decision_id,
            actor_id=args.actor_id,
            actor_type=args.actor_type,
            resolution=args.resolution,
            has_evidence=_has_evidence_filter(args),
            since=args.since,
            until=args.until,
            limit=args.limit,
            offset=args.offset,
        )
        payload = build_closeout_list_payload(
            page.items,
            terminal_events_by_id=_terminal_events_by_id(service),
            filters=_closeout_filters_payload(args),
            total_count=page.total_count,
            limit=page.limit,
            offset=page.offset,
        )
    except Exception as error:
        return _mapped_error(command, args, error)

    return _success(
        command,
        args,
        payload,
        _closeout_list_text(command, payload),
        was_noop=not payload["closeouts"],
    )


def _handle_closeout_export(args: Namespace) -> CliResponse:
    command = "ideas closeout export"
    filter_error = _closeout_filter_error(command, args)
    if filter_error is not None:
        return filter_error
    try:
        service = _service(args)
        page = service.query_closeout_records(
            decision_id=args.decision_id,
            actor_id=args.actor_id,
            actor_type=args.actor_type,
            resolution=args.resolution,
            has_evidence=_has_evidence_filter(args),
            since=args.since,
            until=args.until,
            limit=args.limit,
            offset=args.offset,
        )
        terminal_events_by_id = _terminal_events_by_id(service)
        if _output_format(args) == "csv":
            content = closeout_records_to_csv(
                page.items,
                terminal_events_by_id=terminal_events_by_id,
            )
            _write_output_dir_artifact(
                args,
                stem="trade-idea-closeouts",
                artifact_id=_csv_artifact_suffix(
                    "closeout",
                    _closeout_filters_payload(args),
                    content,
                ),
                extension="csv",
                content=content,
            )
            return CliResponse.success_response(
                command=command,
                data=content,
                was_noop=not page.items,
            )

        payload = build_closeout_export_artifact(
            page.items,
            terminal_events_by_id=terminal_events_by_id,
            filters=_closeout_filters_payload(args),
            total_count=page.total_count,
            limit=page.limit,
            offset=page.offset,
        )
        artifact_path = _write_output_dir_artifact(
            args,
            stem="trade-idea-closeouts",
            artifact_id=payload["closeout_export_id"],
            extension="json",
            content=_json_artifact(payload),
        )
        if artifact_path is not None:
            payload = {**payload, "artifact_path": artifact_path}
    except Exception as error:
        return _mapped_error(command, args, error)

    return _success(
        command,
        args,
        payload,
        _status_line(command, "OK", f"{payload['row_count']} rows"),
        was_noop=payload["row_count"] == 0,
    )


def _handle_approve(args: Namespace) -> CliResponse:
    command = "ideas approve"
    reason_error = _reason_error(command, args)
    if reason_error is not None:
        return reason_error
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        view = _service(args).approve(
            args.decision_id, actor_id=_actor_id(args), reason=args.reason
        )
    except Exception as error:
        return _mapped_error(command, args, error)
    return _state_change_success(command, args, view)


def _handle_reject(args: Namespace) -> CliResponse:
    command = "ideas reject"
    reason_error = _reason_error(command, args)
    if reason_error is not None:
        return reason_error
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        view = _service(args).reject(args.decision_id, actor_id=_actor_id(args), reason=args.reason)
    except Exception as error:
        return _mapped_error(command, args, error)
    return _state_change_success(command, args, view)


def _handle_request_changes(args: Namespace) -> CliResponse:
    command = "ideas request-changes"
    reason_error = _reason_error(command, args)
    if reason_error is not None:
        return reason_error
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        view = _service(args).request_changes(
            args.decision_id,
            actor_id=_actor_id(args),
            reason=args.reason,
        )
    except Exception as error:
        return _mapped_error(command, args, error)
    return _state_change_success(command, args, view)


def _handle_cancel(args: Namespace) -> CliResponse:
    command = "ideas cancel"
    reason_error = _reason_error(command, args)
    if reason_error is not None:
        return reason_error
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        view = _service(args).cancel(args.decision_id, actor_id=_actor_id(args), reason=args.reason)
    except Exception as error:
        return _mapped_error(command, args, error)
    return _state_change_success(command, args, view)


def _handle_expire(args: Namespace) -> CliResponse:
    command = "ideas expire"
    has_decision_id = bool(args.decision_id)
    if has_decision_id == bool(args.sweep):
        return _failure(
            command,
            args,
            CliErrorCode.MISSING_ARGUMENT,
            "Provide exactly one of DECISION_ID or --sweep",
        )
    if has_decision_id:
        decision_id_error = _decision_id_error(command, args, args.decision_id)
        if decision_id_error is not None:
            return decision_id_error

    try:
        service = _service(args)
        if args.sweep:
            expired_views = service.expire_due_ideas(actor_id=_actor_id(args), reason=args.reason)
            expired = [view.idea.decision_id for view in expired_views]
            text = _status_line(command, "OK", f"expired={len(expired)}")
            payload = {"expired": expired}
            return _success(command, args, payload, text, was_noop=not expired)

        view = service.expire(args.decision_id, actor_id=_actor_id(args), reason=args.reason)
    except Exception as error:
        return _mapped_error(command, args, error)
    return _state_change_success(command, args, view)


def _handle_mark_submitted(args: Namespace) -> CliResponse:
    command = "ideas mark-submitted"
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        view = _service(args).record_submission(
            args.decision_id,
            actor_id=_actor_id(args),
            venue=args.venue,
            external_order_id=args.external_order_id,
            reason=args.reason,
            actor_type=ActorType(args.actor_type),
        )
    except Exception as error:
        return _mapped_error(command, args, error)
    return _state_change_success(command, args, view)


def _handle_mark_filled(args: Namespace) -> CliResponse:
    command = "ideas mark-filled"
    decision_id_error = _decision_id_error(command, args, args.decision_id)
    if decision_id_error is not None:
        return decision_id_error
    try:
        view = _service(args).record_fill(
            args.decision_id,
            actor_id=_actor_id(args),
            venue=args.venue,
            external_order_id=args.external_order_id,
            reason=args.reason,
            actor_type=ActorType.VENUE,
        )
    except Exception as error:
        return _mapped_error(command, args, error)
    return _state_change_success(command, args, view)


def _handle_reconcile_paper_fills(args: Namespace) -> CliResponse:
    command = "ideas reconcile-paper-fills"
    try:
        profile = validate_paper_reconciliation_profile(args.profile)
        event_store_root = _paper_reconciliation_event_store_root(args, profile)
        event_store = EventStore(root=event_store_root)
        try:
            if args.limit is None:
                store_events = cast(list[Mapping[str, Any]], event_store.list_events())
            else:
                store_events = cast(
                    list[Mapping[str, Any]],
                    event_store.get_recent_by_type("trade", args.limit),
                )
        finally:
            event_store.close()

        report = PaperFillReconciler(
            _service(args),
            actor_id=_paper_reconciliation_actor_id(args),
            venue=args.venue,
        ).reconcile_store_events(store_events, apply=args.apply)
    except Exception as error:
        return _mapped_error(command, args, error)

    payload = report.to_dict()
    payload["profile"] = profile
    payload["event_store_root"] = str(event_store_root)
    payload["apply"] = bool(args.apply)
    return _success(
        command,
        args,
        payload,
        _paper_reconciliation_text(command, payload),
        was_noop=not args.apply or payload["recorded_count"] == 0,
    )


def _handle_budget_show(args: Namespace) -> CliResponse:
    command = "ideas budget show"
    try:
        budget = _service(args).current_budget()
    except Exception as error:
        return _mapped_error(command, args, error)
    payload = budget.to_dict()
    text = _budget_text(payload)
    return _success(command, args, payload, text)


def _handle_budget_set(args: Namespace) -> CliResponse:
    command = "ideas budget set"
    reason_error = _reason_error(command, args)
    if reason_error is not None:
        return reason_error
    overrides = _budget_overrides(args)
    if not overrides:
        return _failure(
            command,
            args,
            CliErrorCode.MISSING_ARGUMENT,
            "At least one budget field flag is required",
        )

    try:
        service = _service(args)
        current = service.current_budget()
        new_budget = replace(
            current,
            version=current.version + 1,
            reason=args.reason,
            **overrides,
        )
        service.update_budget(new_budget, ActorType.HUMAN, _actor_id(args))
    except Exception as error:
        return _mapped_error(command, args, error)

    payload = new_budget.to_dict()
    text = _status_line(command, "OK", f"version={new_budget.version}")
    return _success(command, args, payload, text)


def _handle_audit_list(args: Namespace) -> CliResponse:
    command = "ideas audit list"
    filter_error = _audit_filter_error(command, args)
    if filter_error is not None:
        return filter_error
    try:
        service = _service(args)
        page = service.list_audit_events(
            decision_id=args.decision_id,
            actor_id=args.actor_id,
            actor_type=args.actor_type,
            action=args.action,
            state=args.state,
            since=args.since,
            until=args.until,
            limit=args.limit,
            offset=args.offset,
        )
        payload = build_audit_list_payload(
            page.items,
            filters=_audit_filters_payload(args),
            total_count=page.total_count,
            limit=page.limit,
            offset=page.offset,
        )
    except Exception as error:
        return _mapped_error(command, args, error)

    return _success(
        command,
        args,
        payload,
        _audit_list_text(command, payload),
        was_noop=not payload["events"],
    )


def _handle_audit_export(args: Namespace) -> CliResponse:
    command = "ideas audit export"
    filter_error = _audit_filter_error(command, args)
    if filter_error is not None:
        return filter_error
    try:
        service = _service(args)
        page = service.list_audit_events(
            decision_id=args.decision_id,
            actor_id=args.actor_id,
            actor_type=args.actor_type,
            action=args.action,
            state=args.state,
            since=args.since,
            until=args.until,
            limit=args.limit,
            offset=args.offset,
        )
        if _output_format(args) == "csv":
            content = audit_events_to_csv(page.items)
            _write_output_dir_artifact(
                args,
                stem="trade-idea-audit",
                artifact_id=_csv_artifact_suffix(
                    "audit",
                    _audit_filters_payload(args),
                    content,
                ),
                extension="csv",
                content=content,
            )
            return CliResponse.success_response(
                command=command,
                data=content,
                was_noop=not page.items,
            )

        payload = build_audit_export_artifact(
            page.items,
            filters=_audit_filters_payload(args),
            total_count=page.total_count,
            limit=page.limit,
            offset=page.offset,
        )
        artifact_path = _write_output_dir_artifact(
            args,
            stem="trade-idea-audit",
            artifact_id=payload["audit_export_id"],
            extension="json",
            content=_json_artifact(payload),
        )
        if artifact_path is not None:
            payload = {**payload, "artifact_path": artifact_path}
    except Exception as error:
        return _mapped_error(command, args, error)

    return _success(
        command,
        args,
        payload,
        _status_line(command, "OK", f"{payload['row_count']} rows"),
        was_noop=payload["row_count"] == 0,
    )


def _handle_audit_tail(args: Namespace) -> CliResponse:
    command = "ideas audit tail"
    try:
        count = max(0, args.count)
        events = _service(args).audit_log.read_events(args.decision_id)
    except Exception as error:
        return _mapped_error(command, args, error)
    selected = events[-count:] if count else []
    payload = {"events": [event.to_dict() for event in selected]}
    text = _events_text(payload["events"])
    return _success(command, args, payload, text, was_noop=not selected)


def _handle_audit_verify(args: Namespace) -> CliResponse:
    command = "ideas audit verify"
    try:
        events = _service(args).audit_log.verify()
    except Exception as error:
        return _mapped_error(command, args, error)
    payload = {"event_count": len(events)}
    text = _status_line(command, "OK", f"{len(events)} events")
    return _success(command, args, payload, text, was_noop=not events)


def _budget_overrides(args: Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for field_name in BUDGET_FIELDS:
        value = getattr(args, field_name)
        if value is None:
            continue
        if field_name in {
            "sizing_capped_by_budget",
            "allow_futures_leverage",
            "allow_naked_shorts",
        }:
            value = _bool_value(value)
        overrides[field_name] = value
    return overrides


def _audit_filter_error(command: str, args: Namespace) -> CliResponse | None:
    if args.decision_id:
        decision_id_error = _decision_id_error(command, args, args.decision_id)
        if decision_id_error is not None:
            return decision_id_error
    return _window_error(command, args)


def _closeout_filter_error(command: str, args: Namespace) -> CliResponse | None:
    if args.decision_id:
        decision_id_error = _decision_id_error(command, args, args.decision_id)
        if decision_id_error is not None:
            return decision_id_error
    return _window_error(command, args)


def _window_error(command: str, args: Namespace) -> CliResponse | None:
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    if since is None or until is None or since <= until:
        return None
    return _failure(
        command,
        args,
        CliErrorCode.INVALID_ARGUMENT,
        "--since/--from must be before or equal to --until/--to",
        details={"field": "date_window"},
    )


def _audit_filters_payload(args: Namespace) -> dict[str, Any]:
    return {
        "decision_id": args.decision_id,
        "actor_id": args.actor_id,
        "actor_type": args.actor_type,
        "action": args.action,
        "state": args.state,
        "since": args.since.isoformat() if args.since is not None else None,
        "until": args.until.isoformat() if args.until is not None else None,
    }


def _closeout_filters_payload(args: Namespace) -> dict[str, Any]:
    return {
        "decision_id": args.decision_id,
        "actor_id": args.actor_id,
        "actor_type": args.actor_type,
        "resolution": args.resolution,
        "has_evidence": _has_evidence_filter(args),
        "since": args.since.isoformat() if args.since is not None else None,
        "until": args.until.isoformat() if args.until is not None else None,
    }


def _has_evidence_filter(args: Namespace) -> bool | None:
    value = getattr(args, "has_evidence", None)
    if value is None:
        return None
    return _bool_value(value)


def _terminal_events_by_id(service: TradeIdeaService) -> dict[str, Any]:
    return {event.event_id: event for event in service.audit_log.read_events()}


def _json_artifact(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"


def _write_output_dir_artifact(
    args: Namespace,
    *,
    stem: str,
    artifact_id: str,
    extension: str,
    content: str,
) -> str | None:
    output_dir = getattr(args, "output_dir", None)
    if output_dir is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stem}-{artifact_id}.{extension}"
    path.write_text(content, encoding="utf-8")
    return str(path)


def _csv_artifact_suffix(prefix: str, filters: Mapping[str, Any], content: str) -> str:
    encoded = json.dumps(filters, sort_keys=True, separators=(",", ":"), default=str)
    filter_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
    content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{filter_digest}-{content_digest}"


def _closeout_profit_loss_error(command: str, args: Namespace) -> CliResponse | None:
    if (
        args.realized_profit_loss_amount is not None
        or args.realized_profit_loss_percent is not None
        or args.realized_profit_loss_unavailable_reason.strip()
    ):
        return None
    return _failure(
        command,
        args,
        CliErrorCode.MISSING_ARGUMENT,
        (
            "Provide at least one of --realized-profit-loss-amount, "
            "--realized-profit-loss-percent, or --realized-profit-loss-unavailable-reason"
        ),
        details={"field": "realized_profit_loss"},
    )


def _evidence(args: Namespace) -> tuple[str, ...]:
    return tuple(item.strip() for item in (getattr(args, "evidence", None) or ()))


def _evidence_error(command: str, args: Namespace) -> CliResponse | None:
    for index, item in enumerate(getattr(args, "evidence", None) or ()):
        if item.strip():
            continue
        return _failure(
            command,
            args,
            CliErrorCode.INVALID_ARGUMENT,
            "--evidence entries must be non-empty",
            details={"field": "evidence", "index": index},
        )
    return None


def _state_change_success(command: str, args: Namespace, view: Any) -> CliResponse:
    payload = _view_summary(view)
    text = _status_line(command, "OK", f"{view.idea.decision_id}, state={view.state.value}")
    return _success(command, args, payload, text)


def _closeout_success(command: str, args: Namespace, record: Any) -> CliResponse:
    record_payload = record.to_dict()
    payload = {
        "decision_id": record.decision_id,
        "closeout_attribution": record_payload,
    }
    return _success(command, args, payload, _closeout_text(command, record_payload))


def _view_summary(view: Any) -> dict[str, Any]:
    idea = view.idea
    expires_at = idea.time_horizon.expires_at
    percent = idea.max_loss.percent_of_account
    return {
        "decision_id": idea.decision_id,
        "state": view.state.value,
        "instrument": idea.instrument,
        "direction": idea.direction.value,
        "max_loss_pct": str(percent) if percent is not None else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "confidence": idea.confidence.label.value,
    }


def _view_record(view: Any, *, include_events: bool) -> dict[str, Any]:
    payload: dict[str, Any] = view.idea.to_dict()
    payload["state"] = view.state.value
    if include_events:
        payload["events"] = [event.to_dict() for event in view.events]
    return payload


def _success(
    command: str,
    args: Namespace,
    payload: Any,
    text: str,
    *,
    warnings: list[str] | None = None,
    was_noop: bool = False,
) -> CliResponse:
    data = text if _output_format(args) == "text" else payload
    return CliResponse.success_response(
        command=command,
        data=data,
        warnings=warnings,
        was_noop=was_noop,
    )


def _input_error(command: str, args: Namespace, error: InputPayloadError) -> CliResponse:
    details = {"field": error.field} if error.field else {}
    return _failure(
        command,
        args,
        CliErrorCode.INVALID_ARGUMENT,
        str(error),
        details=details,
        data={"field": error.field} if error.field else None,
    )


def _decision_id_error(command: str, args: Namespace, decision_id: str) -> CliResponse | None:
    if is_safe_decision_id(decision_id):
        return None
    return _failure(
        command,
        args,
        CliErrorCode.INVALID_ARGUMENT,
        "decision_id must be a safe path segment",
        details={"field": "decision_id", "value": decision_id},
        data={"field": "decision_id"},
    )


def _reason_error(command: str, args: Namespace) -> CliResponse | None:
    reason = getattr(args, "reason", "")
    if reason and reason.strip():
        return None
    return _failure(
        command,
        args,
        CliErrorCode.MISSING_ARGUMENT,
        "--reason must be non-empty",
        details={"field": "reason"},
    )


def _mapped_error(command: str, args: Namespace, error: Exception) -> CliResponse:
    if isinstance(error, PolicyViolationError):
        violations = error.violations or [str(error)]
        message = f"approval refused ({len(violations)} violations)"
        return _failure(
            command,
            args,
            CliErrorCode.POLICY_VIOLATION,
            message,
            details={"violations": violations},
            data={"violations": violations},
            text_lines=[
                f"✗ {command} FAILED: {message}",
                *[f"  - {violation}" for violation in violations],
            ],
        )
    if isinstance(error, UnknownTradeIdeaError):
        return _failure(
            command,
            args,
            CliErrorCode.IDEA_NOT_FOUND,
            str(error),
            details=_error_context(error),
        )
    if isinstance(error, InvalidTransitionError):
        return _failure(
            command,
            args,
            CliErrorCode.VALIDATION_ERROR,
            str(error),
            details=_error_context(error),
        )
    if isinstance(error, (AuditIntegrityError, BudgetIntegrityError)):
        return _failure(
            command,
            args,
            CliErrorCode.OPERATION_FAILED,
            str(error),
            details=_error_context(error),
        )
    if isinstance(error, ValidationError):
        return _failure(
            command,
            args,
            CliErrorCode.VALIDATION_ERROR,
            str(error),
            details=_error_context(error),
        )
    return _failure(
        command,
        args,
        CliErrorCode.OPERATION_FAILED,
        str(error),
        details={"exception_type": type(error).__name__},
    )


def _failure(
    command: str,
    args: Namespace,
    code: CliErrorCode,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    data: Any = None,
    text_lines: list[str] | None = None,
) -> CliResponse:
    output_format = _output_format(args)
    response_data = data
    if output_format == "text":
        response_data = "\n".join(text_lines or [f"✗ {command} FAILED: {message}"])
    return CliResponse(
        success=False,
        command=command,
        data=response_data,
        errors=[CliError.from_code(code, message, **(details or {}))],
        exit_code=1,
    )


def _status_line(command: str, status: str, details: str) -> str:
    return f"✓ {command} {status} ({details})"


def _ideas_table(ideas: list[dict[str, Any]]) -> str:
    if not ideas:
        return _status_line("ideas list", "OK", "0 ideas")
    lines = ["DECISION_ID  STATE  INSTRUMENT  DIRECTION  MAX_LOSS%  EXPIRES_AT"]
    for idea in ideas:
        lines.append(
            "{decision_id}  {state}  {instrument}  {direction}  {max_loss_pct}  {expires_at}".format(
                decision_id=idea["decision_id"],
                state=idea["state"],
                instrument=idea["instrument"],
                direction=idea["direction"],
                max_loss_pct=idea["max_loss_pct"] or "",
                expires_at=idea["expires_at"] or "",
            )
        )
    return "\n".join(lines)


def _record_text(payload: dict[str, Any], *, include_events: bool) -> str:
    lines = [
        f"decision_id: {payload['decision_id']}",
        f"state: {payload['state']}",
        f"instrument: {payload['instrument']}",
        f"product_type: {payload['product_type']}",
        f"direction: {payload['direction']}",
        f"thesis: {payload['thesis']}",
        f"invalidation: {payload['invalidation']}",
        f"target_exit: {payload['target_exit']}",
        f"max_loss.percent_of_account: {payload['max_loss']['percent_of_account']}",
        f"expires_at: {payload['time_horizon']['expires_at']}",
        f"confidence: {payload['confidence']['label']}",
    ]
    if include_events:
        lines.append("")
        lines.append("TIMESTAMP  ACTOR  ACTION  TRANSITION  REASON")
        lines.append(_events_text(payload.get("events", [])))
    return "\n".join(lines)


def _events_text(events: list[dict[str, Any]]) -> str:
    if not events:
        return "No audit events found."
    lines: list[str] = []
    for event in events:
        before = event["before_state"] or "none"
        after = event["after_state"]
        lines.append(
            "{timestamp}  {actor_type}/{actor_id}  {action}  {before}->{after}  {reason}".format(
                timestamp=event["timestamp"],
                actor_type=event["actor_type"],
                actor_id=event["actor_id"],
                action=event["action"],
                before=before,
                after=after,
                reason=event["reason"],
            )
        )
    return "\n".join(lines)


def _paper_reconciliation_text(command: str, payload: dict[str, Any]) -> str:
    details = (
        f"mode={payload['mode']}, matched={payload['matched_count']}, "
        f"recorded={payload['recorded_count']}, "
        f"unmatched={payload['unmatched_count']}, skipped={payload['skipped_count']}"
    )
    lines = [
        _status_line(command, "OK", details),
        f"profile: {payload['profile']}",
        f"event_store_root: {payload['event_store_root']}",
    ]
    lines.extend(_paper_reconciliation_section("matched", payload["matched"]))
    lines.extend(_paper_reconciliation_section("unmatched", payload["unmatched"]))
    lines.extend(_paper_reconciliation_section("skipped", payload["skipped"]))
    return "\n".join(lines)


def _paper_reconciliation_section(
    label: str,
    entries: list[dict[str, Any]],
) -> list[str]:
    lines = [f"{label}:"]
    if not entries:
        lines.append("  none")
        return lines
    for entry in entries:
        event = entry["event"]
        decision_id = entry.get("decision_id") or "-"
        lines.append(
            "  {decision_id} {symbol} {side} order={order_id} reason={reason}".format(
                decision_id=decision_id,
                symbol=event.get("symbol") or "-",
                side=event.get("side") or "-",
                order_id=event.get("order_id") or event.get("client_order_id") or "-",
                reason=entry["reason"],
            )
        )
    return lines


def _audit_list_text(command: str, payload: dict[str, Any]) -> str:
    pagination = payload["pagination"]
    lines = [
        _status_line(
            command,
            "OK",
            f"{pagination['returned_count']} events, total={pagination['total_count']}",
        )
    ]
    if payload["events"]:
        lines.append(_events_text(payload["events"]))
    else:
        lines.append("No audit events found.")
    return "\n".join(lines)


def _closeout_list_text(command: str, payload: dict[str, Any]) -> str:
    pagination = payload["pagination"]
    lines = [
        _status_line(
            command,
            "OK",
            f"{pagination['returned_count']} closeouts, total={pagination['total_count']}",
        )
    ]
    if not payload["closeouts"]:
        lines.append("No closeout attribution records found.")
        return "\n".join(lines)
    lines.append("TIMESTAMP  DECISION_ID  ACTOR  RESOLUTION  REALIZED_AMOUNT  TERMINAL_STATE")
    for record in payload["closeouts"]:
        lines.append(
            "{timestamp}  {decision_id}  {actor_type}/{actor_id}  {resolution}  "
            "{realized_profit_loss_amount}  {terminal_state}".format(
                timestamp=record["timestamp"],
                decision_id=record["decision_id"],
                actor_type=record["actor_type"],
                actor_id=record["actor_id"],
                resolution=record["resolution"],
                realized_profit_loss_amount=record["realized_profit_loss_amount"] or "",
                terminal_state=record["terminal_state"] or "",
            )
        )
    return "\n".join(lines)


def _budget_text(payload: dict[str, Any]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in payload.items())


def _replay_report_text(report: ReplayReport) -> str:
    average_return_r = report.average_return_r
    return "\n".join(
        [
            _status_line(
                "ideas replay baseline",
                "OK",
                (
                    f"{report.symbol} {report.granularity}, "
                    f"snapshots={report.snapshots_evaluated}, "
                    f"ideas={report.ideas_proposed}"
                ),
            ),
            f"proposer_id: {report.proposer_id}",
            (
                "outcomes: "
                f"target_hits={report.target_hits}, "
                f"stop_hits={report.stop_hits}, "
                f"timed_out={report.timed_out}, "
                f"not_filled={report.not_filled}, "
                f"no_future_data={report.no_future_data}"
            ),
            (
                "hit_rates: "
                f"target={_decimal_pct(report.target_hit_rate)}, "
                f"stop={_decimal_pct(report.stop_hit_rate)}"
            ),
            (
                "average_return_r: "
                f"{average_return_r.normalize() if average_return_r is not None else 'n/a'}"
            ),
        ]
    )


def _decimal_pct(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('0.01'))}%"


def _closeout_text(command: str, payload: dict[str, Any]) -> str:
    lines = [
        _status_line(
            command, "OK", f"{payload['decision_id']}, resolution={payload['resolution']}"
        ),
        f"actor: {payload['actor_type']}/{payload['actor_id']}",
        f"realized_profit_loss_amount: {payload['realized_profit_loss_amount']}",
        f"realized_profit_loss_percent: {payload['realized_profit_loss_percent']}",
        "realized_profit_loss_unavailable_reason: "
        f"{payload['realized_profit_loss_unavailable_reason']}",
        f"terminal_event_id: {payload['terminal_event_id']}",
        f"record_hash: {payload['record_hash']}",
    ]
    evidence = payload.get("evidence", [])
    if evidence:
        lines.append("evidence:")
        lines.extend(f"- {item}" for item in evidence)
    return "\n".join(lines)


def _ticket_export_text(
    payload: dict[str, Any],
    *,
    written_to: Path | None = None,
) -> str:
    details = (
        f"{payload['decision_id']}, "
        f"venue={payload['venue_request']['venue']}, "
        f"ticket_hash={payload['ticket_hash']}"
    )
    lines = [_status_line("ideas export-ticket", "OK", details)]
    if written_to is not None:
        lines.append(f"written_to: {written_to}")
    lines.extend(
        [
            f"record_hash: {payload['record_hash']}",
            f"client_order_id: {payload['venue_request']['client_order_id']}",
        ]
    )
    return "\n".join(lines)
