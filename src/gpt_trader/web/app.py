"""FastAPI app factory for the operator console.

Every read renders durable artifacts (records, audit log, budget/autonomy
logs) through ``TradeIdeaService``; every mutation is one of the existing
identity-stamped service calls (approve / reject / request_changes) with a
required reason. The console holds no state of its own.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import ActorType
from gpt_trader.features.trade_ideas.autonomy import (
    AUTONOMY_SOURCE_FAIL_CLOSED,
    RATCHET_ACTOR_ID,
    AutonomyIntegrityError,
)
from gpt_trader.features.trade_ideas.budget import BudgetIntegrityError, RiskBudget
from gpt_trader.features.trade_ideas.models import AutonomyMode, TradeIdea
from gpt_trader.features.trade_ideas.review_metrics import compute_review_instrumentation
from gpt_trader.features.trade_ideas.service import (
    TradeIdeaService,
    create_trade_idea_service,
    resolve_auto_approval_enabled,
    resolve_trade_idea_actor_id,
)
from gpt_trader.features.trade_ideas.service_models import (
    TradeIdeaView,
    UnknownTradeIdeaError,
)
from gpt_trader.features.trade_ideas.workflow import ALLOWED_TRANSITIONS, TradeIdeaState
from gpt_trader.web.cycle_feed import load_cycle_feed

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Newest audit events shown on the activity page.
_ACTIVITY_EVENT_LIMIT = 50

# States an operator can still act on from the console review queue.
_PENDING_STATES = (TradeIdeaState.PROPOSED, TradeIdeaState.NEEDS_CHANGES)


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 0:
        return "expired"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours >= 48:
        return f"{hours // 24}d {hours % 24}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_rate(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


def _format_money(value: Decimal | str | None) -> str:
    if value is None:
        return "—"
    amount = Decimal(str(value))
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def _format_percent(value: Decimal | str | None) -> str:
    if value is None:
        return "—"
    amount = Decimal(str(value))
    return f"{amount.quantize(Decimal('0.01')):f}%"


def _pretty_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _queue_rows(service: TradeIdeaService) -> list[dict[str, Any]]:
    status = service.queue_status()
    expirations = {expiration.decision_id: expiration for expiration in status.upcoming_expirations}
    rows: list[dict[str, Any]] = []
    for state in _PENDING_STATES:
        for view in service.list_views(state):
            expiration = expirations.get(view.idea.decision_id)
            rows.append(
                {
                    "view": view,
                    "idea": view.idea,
                    "state": view.state,
                    "violations": service.peek_approval_violations(view.idea),
                    "expires_in": (
                        _format_duration(expiration.seconds_until_expiry) if expiration else None
                    ),
                }
            )
    return rows


def _record_versions(service: TradeIdeaService, view: TradeIdeaView) -> list[dict[str, Any]]:
    """Resolve the distinct record versions pinned by the audit trail, oldest first."""
    versions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in view.events:
        if not event.record_hash or event.record_hash in seen:
            continue
        seen.add(event.record_hash)
        idea: TradeIdea | None
        try:
            idea = service.load_record_version(view.idea.decision_id, event.record_hash)
        except ValidationError:
            idea = None
        versions.append(
            {
                "number": len(versions) + 1,
                "record_hash": event.record_hash,
                "pinned_at": event.timestamp,
                "pinned_by": event.actor_id,
                "idea": idea,
            }
        )
    return versions


# Budget fields an operator can move through the envelope form; version and
# reason are handled separately (sequenced / required per submission).
_BUDGET_LEVER_FIELDS = (
    "max_loss_per_idea_pct",
    "max_daily_loss_pct",
    "max_open_notional_pct",
    "max_concurrent_approved_tickets",
    "max_review_latency_hours",
    "sizing_capped_by_budget",
    "gain_retention_floor_pct",
    "allow_futures_leverage",
    "allow_naked_shorts",
    "account_equity",
    "max_drawdown_from_peak_pct",
    "max_equity_buying_power_pct",
)


def _budget_changes(previous: RiskBudget, current: RiskBudget) -> list[str]:
    """Human-readable lever diffs between two consecutive budget versions."""
    changes: list[str] = []
    for field in _BUDGET_LEVER_FIELDS:
        before = getattr(previous, field)
        after = getattr(current, field)
        if before != after:
            changes.append(f"{field}: {before} → {after}")
    return changes


def _budget_form_values(budget: RiskBudget) -> dict[str, str | bool]:
    """Form-ready values for the lever inputs, prefilled from a budget version."""
    values: dict[str, str | bool] = {}
    for field in _BUDGET_LEVER_FIELDS:
        value = getattr(budget, field)
        if isinstance(value, bool):
            values[field] = value
        else:
            values[field] = "" if value is None else str(value)
    return values


def _budget_from_form(
    *,
    base_version: int,
    reason: str,
    values: dict[str, str | bool],
) -> RiskBudget:
    """Build the candidate next budget version from submitted form values.

    Conversion failures and ``RiskBudget`` invariant violations surface as
    ``ValueError`` with a field-named message so the caller can re-render the
    form instead of returning a bare 500.
    """

    def _decimal(field: str) -> Decimal:
        raw = str(values[field]).strip()
        try:
            return Decimal(raw)
        except ArithmeticError as error:
            raise ValueError(f"{field} must be a decimal number, got {raw!r}") from error

    def _int(field: str) -> int:
        raw = str(values[field]).strip()
        try:
            return int(raw)
        except ValueError as error:
            raise ValueError(f"{field} must be a whole number, got {raw!r}") from error

    equity_raw = str(values["account_equity"]).strip()
    account_equity: Decimal | None = None
    if equity_raw:
        try:
            account_equity = Decimal(equity_raw)
        except ArithmeticError as error:
            raise ValueError(
                f"account_equity must be a decimal number or blank, got {equity_raw!r}"
            ) from error
    drawdown_raw = str(values["max_drawdown_from_peak_pct"]).strip()
    max_drawdown_from_peak_pct: Decimal | None = None
    if drawdown_raw:
        try:
            max_drawdown_from_peak_pct = Decimal(drawdown_raw)
        except ArithmeticError as error:
            raise ValueError(
                "max_drawdown_from_peak_pct must be a decimal number or blank, "
                f"got {drawdown_raw!r}"
            ) from error
    buying_power_raw = str(values["max_equity_buying_power_pct"]).strip()
    max_equity_buying_power_pct: Decimal | None = None
    if buying_power_raw:
        try:
            max_equity_buying_power_pct = Decimal(buying_power_raw)
        except ArithmeticError as error:
            raise ValueError(
                "max_equity_buying_power_pct must be a decimal number or blank, "
                f"got {buying_power_raw!r}"
            ) from error
    return RiskBudget(
        version=base_version + 1,
        max_loss_per_idea_pct=_decimal("max_loss_per_idea_pct"),
        max_daily_loss_pct=_decimal("max_daily_loss_pct"),
        max_open_notional_pct=_decimal("max_open_notional_pct"),
        max_concurrent_approved_tickets=_int("max_concurrent_approved_tickets"),
        max_review_latency_hours=_int("max_review_latency_hours"),
        sizing_capped_by_budget=bool(values["sizing_capped_by_budget"]),
        gain_retention_floor_pct=_decimal("gain_retention_floor_pct"),
        allow_futures_leverage=bool(values["allow_futures_leverage"]),
        allow_naked_shorts=bool(values["allow_naked_shorts"]),
        reason=reason,
        account_equity=account_equity,
        max_drawdown_from_peak_pct=max_drawdown_from_peak_pct,
        max_equity_buying_power_pct=max_equity_buying_power_pct,
    )


def create_app(
    *,
    ideas_root: Path | None = None,
    actor_id: str | None = None,
    service: TradeIdeaService | None = None,
) -> FastAPI:
    """Build the console app over one TradeIdeaService and one operator identity."""
    resolved_service = service or create_trade_idea_service(ideas_root)
    resolved_actor = resolve_trade_idea_actor_id(actor_id)
    # The cycle manifest lives beside the trade-idea stores; the CLI runner
    # writes it under <ideas_root>/cycle (gpt-trader ideas cycle). Derive it
    # from the service's own root so an injected service and the feed can
    # never point at different ideas roots.
    cycle_manifest_path = resolved_service.root / "cycle" / "manifest.jsonl"
    # Serializes the staleness pre-check + audited append for budget updates:
    # sync route handlers run on a threadpool, so without this two forms
    # rendered from the same version could both pass the pre-check and both
    # append "the same" next version (RiskBudgetLog.append has no lock of its
    # own). In-process only — console writes for one operator identity.
    budget_update_lock = threading.Lock()

    app = FastAPI(title="GPT-Trader operator console", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["duration"] = _format_duration
    templates.env.filters["timestamp"] = _format_timestamp
    templates.env.filters["rate"] = _format_rate
    templates.env.filters["money"] = _format_money
    templates.env.filters["percent"] = _format_percent
    templates.env.filters["pretty_json"] = _pretty_json

    def _render_queue(request: Request, status_code: int = 200) -> HTMLResponse:
        instrumentation = compute_review_instrumentation(resolved_service.list_audit_events().items)
        return templates.TemplateResponse(
            request=request,
            name="queue.html",
            context={
                "actor_id": resolved_actor,
                "rows": _queue_rows(resolved_service),
                "queue_status": resolved_service.queue_status(),
                "headroom": resolved_service.budget_headroom(),
                "autonomy": resolved_service.peek_autonomy(),
                "instrumentation": instrumentation,
            },
            status_code=status_code,
        )

    def _render_detail(
        request: Request,
        decision_id: str,
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        try:
            view = resolved_service.get(decision_id)
        except UnknownTradeIdeaError:
            return templates.TemplateResponse(
                request=request,
                name="not_found.html",
                context={"decision_id": decision_id, "actor_id": resolved_actor},
                status_code=404,
            )
        # Offer only the decisions the workflow permits from this state:
        # e.g. a needs-changes idea can be rejected, but must be resubmitted
        # by its proposer before it can be approved again.
        allowed_targets = ALLOWED_TRANSITIONS.get(view.state, frozenset())
        return templates.TemplateResponse(
            request=request,
            name="idea_detail.html",
            context={
                "actor_id": resolved_actor,
                "view": view,
                "idea": view.idea,
                "record": view.idea.to_dict(),
                "can_approve": TradeIdeaState.APPROVED in allowed_targets,
                "can_request_changes": TradeIdeaState.NEEDS_CHANGES in allowed_targets,
                "can_reject": TradeIdeaState.REJECTED in allowed_targets,
                "violations": resolved_service.peek_approval_violations(view.idea),
                "versions": _record_versions(resolved_service, view),
                "error": error,
            },
            status_code=status_code,
        )

    def _render_envelope(
        request: Request,
        *,
        error: str | None = None,
        form_values: dict[str, str | bool] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        budget = resolved_service.peek_budget()
        autonomy = resolved_service.peek_autonomy()
        budget_history = resolved_service.budget_log.history()
        budget_rows: list[dict[str, Any]] = []
        for index, entry in enumerate(budget_history):
            previous = budget_history[index - 1].budget if index else None
            budget_rows.append(
                {
                    "entry": entry,
                    "changes": (_budget_changes(previous, entry.budget) if previous else []),
                }
            )
        budget_rows.reverse()
        autonomy_error: str | None = None
        try:
            autonomy_rows = list(reversed(resolved_service.autonomy_history()))
        except AutonomyIntegrityError as exc:
            autonomy_rows = []
            autonomy_error = str(exc)
        # Exception-queue framing: proposed ideas that clear the envelope are
        # what Stage 2 auto-approval would sweep; everything else needs a human.
        inside_envelope: list[dict[str, Any]] = []
        exceptions: list[dict[str, Any]] = []
        awaiting_resubmission: list[dict[str, Any]] = []
        for row in _queue_rows(resolved_service):
            if row["state"] is TradeIdeaState.NEEDS_CHANGES:
                awaiting_resubmission.append(row)
            elif row["violations"]:
                exceptions.append(row)
            else:
                inside_envelope.append(row)
        auto_approval_enabled = resolve_auto_approval_enabled()
        return templates.TemplateResponse(
            request=request,
            name="envelope.html",
            context={
                "actor_id": resolved_actor,
                "budget": budget,
                "autonomy": autonomy,
                "autonomy_fail_closed": autonomy.source == AUTONOMY_SOURCE_FAIL_CLOSED,
                "ratchet_actor_id": RATCHET_ACTOR_ID,
                "budget_rows": budget_rows,
                "autonomy_rows": autonomy_rows,
                "autonomy_error": autonomy_error,
                "inside_envelope": inside_envelope,
                "exceptions": exceptions,
                "awaiting_resubmission": awaiting_resubmission,
                "auto_approval_enabled": auto_approval_enabled,
                "stage2_active": (
                    auto_approval_enabled and autonomy.mode is AutonomyMode.BOUNDED_AUTONOMY
                ),
                "form": form_values or _budget_form_values(budget),
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request) -> HTMLResponse:
        return _render_queue(request)

    @app.get("/envelope", response_class=HTMLResponse)
    def envelope(request: Request) -> HTMLResponse:
        return _render_envelope(request)

    @app.post("/envelope/budget", response_model=None)
    def enact_budget(
        request: Request,
        base_version: int = Form(...),
        reason: str = Form(""),
        max_loss_per_idea_pct: str = Form(""),
        max_daily_loss_pct: str = Form(""),
        max_open_notional_pct: str = Form(""),
        max_concurrent_approved_tickets: str = Form(""),
        max_review_latency_hours: str = Form(""),
        sizing_capped_by_budget: bool = Form(False),
        gain_retention_floor_pct: str = Form(""),
        allow_futures_leverage: bool = Form(False),
        allow_naked_shorts: bool = Form(False),
        account_equity: str = Form(""),
        max_drawdown_from_peak_pct: str = Form(""),
        max_equity_buying_power_pct: str = Form(""),
    ) -> HTMLResponse | RedirectResponse:
        form_values: dict[str, str | bool] = {
            "max_loss_per_idea_pct": max_loss_per_idea_pct,
            "max_daily_loss_pct": max_daily_loss_pct,
            "max_open_notional_pct": max_open_notional_pct,
            "max_concurrent_approved_tickets": max_concurrent_approved_tickets,
            "max_review_latency_hours": max_review_latency_hours,
            "sizing_capped_by_budget": sizing_capped_by_budget,
            "gain_retention_floor_pct": gain_retention_floor_pct,
            "allow_futures_leverage": allow_futures_leverage,
            "allow_naked_shorts": allow_naked_shorts,
            "account_equity": account_equity,
            "max_drawdown_from_peak_pct": max_drawdown_from_peak_pct,
            "max_equity_buying_power_pct": max_equity_buying_power_pct,
        }

        def _form_error(message: str) -> HTMLResponse:
            return _render_envelope(
                request, error=message, form_values=form_values, status_code=400
            )

        def _version_conflict() -> HTMLResponse:
            # Optimistic-concurrency guard: the form was rendered against a
            # version that is no longer current (another operator, the CLI, or
            # an agent enacted a change since). Render the *current* levers,
            # not the submitted ones — echoing stale values behind a fresh
            # base_version would let a reflexive resubmit silently revert the
            # concurrent change.
            return _render_envelope(
                request,
                error=(
                    f"The budget moved to v{resolved_service.peek_budget().version} after "
                    f"this form was loaded (v{base_version}). The current levers are shown "
                    "below; reapply your change if it still holds."
                ),
                status_code=409,
            )

        with budget_update_lock:
            # The staleness guard runs before any other validation: every
            # other error path echoes the submitted levers back under a fresh
            # hidden base_version, which must never happen for a stale
            # submission.
            if base_version != resolved_service.peek_budget().version:
                return _version_conflict()
            cleaned_reason = reason.strip()
            if not cleaned_reason:
                return _form_error("A reason is required for every budget version.")
            try:
                candidate = _budget_from_form(
                    base_version=base_version,
                    reason=cleaned_reason,
                    values=form_values,
                )
            except ValueError as exc:
                return _form_error(str(exc))
            try:
                resolved_service.update_budget(candidate, ActorType.HUMAN, resolved_actor)
            except BudgetIntegrityError:
                # An out-of-process writer (CLI, agent) moved the log between
                # the pre-check and the append; same conflict, same
                # fresh-lever re-render.
                return _version_conflict()
            except ValidationError as exc:
                return _form_error(str(exc))
        return RedirectResponse(url="/envelope", status_code=303)

    @app.get("/accountant", response_class=HTMLResponse)
    def accountant(request: Request) -> HTMLResponse:
        # Both the summary and the monitor snapshot are the same service
        # library calls the CLI reads (`ideas monitors`), so console and CLI
        # can never disagree about HWM, drawdown-from-peak, or exposure.
        return templates.TemplateResponse(
            request=request,
            name="accountant.html",
            context={
                "actor_id": resolved_actor,
                "summary": resolved_service.paper_accounting(),
                "monitors": resolved_service.portfolio_monitors(),
                "budget": resolved_service.peek_budget(),
                "headroom": resolved_service.budget_headroom(),
                "open_approved_count": resolved_service.open_approved_count(),
            },
        )

    @app.get("/activity", response_class=HTMLResponse)
    def activity(request: Request) -> HTMLResponse:
        audit_events = resolved_service.list_audit_events().items
        return templates.TemplateResponse(
            request=request,
            name="activity.html",
            context={
                "actor_id": resolved_actor,
                "feed": load_cycle_feed(cycle_manifest_path),
                "manifest_path": str(cycle_manifest_path),
                "events": tuple(reversed(audit_events[-_ACTIVITY_EVENT_LIMIT:])),
            },
        )

    @app.get("/ideas/{decision_id}", response_class=HTMLResponse)
    def idea_detail(request: Request, decision_id: str) -> HTMLResponse:
        return _render_detail(request, decision_id)

    def _decide(
        request: Request,
        decision_id: str,
        reason: str,
        action: str,
    ) -> HTMLResponse | RedirectResponse:
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            return _render_detail(
                request,
                decision_id,
                error="A reason is required for every decision.",
                status_code=400,
            )
        try:
            if action == "approve":
                resolved_service.approve(decision_id, resolved_actor, cleaned_reason)
            elif action == "reject":
                resolved_service.reject(decision_id, resolved_actor, cleaned_reason)
            else:
                resolved_service.request_changes(decision_id, resolved_actor, cleaned_reason)
        except UnknownTradeIdeaError:
            return _render_detail(request, decision_id, status_code=404)
        except ValidationError as exc:
            return _render_detail(request, decision_id, error=str(exc), status_code=400)
        return RedirectResponse(url="/", status_code=303)

    @app.post("/ideas/{decision_id}/approve", response_model=None)
    def approve(
        request: Request, decision_id: str, reason: str = Form("")
    ) -> HTMLResponse | RedirectResponse:
        return _decide(request, decision_id, reason, "approve")

    @app.post("/ideas/{decision_id}/reject", response_model=None)
    def reject(
        request: Request, decision_id: str, reason: str = Form("")
    ) -> HTMLResponse | RedirectResponse:
        return _decide(request, decision_id, reason, "reject")

    @app.post("/ideas/{decision_id}/request-changes", response_model=None)
    def request_changes(
        request: Request, decision_id: str, reason: str = Form("")
    ) -> HTMLResponse | RedirectResponse:
        return _decide(request, decision_id, reason, "request-changes")

    return app
