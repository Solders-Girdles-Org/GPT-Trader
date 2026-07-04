"""FastAPI app factory for the operator console.

Every read renders durable artifacts (records, audit log, budget/autonomy
logs) through ``TradeIdeaService``; every mutation is one of the existing
identity-stamped service calls (approve / reject / request_changes) with a
required reason. The console holds no state of its own.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.models import TradeIdea
from gpt_trader.features.trade_ideas.review_metrics import compute_review_instrumentation
from gpt_trader.features.trade_ideas.service import (
    TradeIdeaService,
    create_trade_idea_service,
    resolve_trade_idea_actor_id,
)
from gpt_trader.features.trade_ideas.service_models import (
    TradeIdeaView,
    UnknownTradeIdeaError,
)
from gpt_trader.features.trade_ideas.workflow import TradeIdeaState

_TEMPLATES_DIR = Path(__file__).parent / "templates"

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
                    "violations": service.approval_violations(view.idea),
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


def create_app(
    *,
    ideas_root: Path | None = None,
    actor_id: str | None = None,
    service: TradeIdeaService | None = None,
) -> FastAPI:
    """Build the console app over one TradeIdeaService and one operator identity."""
    resolved_service = service or create_trade_idea_service(ideas_root)
    resolved_actor = resolve_trade_idea_actor_id(actor_id)

    app = FastAPI(title="GPT-Trader operator console", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["duration"] = _format_duration
    templates.env.filters["timestamp"] = _format_timestamp
    templates.env.filters["rate"] = _format_rate
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
        return templates.TemplateResponse(
            request=request,
            name="idea_detail.html",
            context={
                "actor_id": resolved_actor,
                "view": view,
                "idea": view.idea,
                "record": view.idea.to_dict(),
                "actionable": view.state in _PENDING_STATES,
                "violations": resolved_service.approval_violations(view.idea),
                "versions": _record_versions(resolved_service, view),
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request) -> HTMLResponse:
        return _render_queue(request)

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
