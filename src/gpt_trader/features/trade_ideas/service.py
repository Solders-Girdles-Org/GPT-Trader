"""Trade-idea lifecycle service: the one audited code path for every actor.

Humans, development agents, and (eventually) operating agents all act through
this service. Every action is identity-stamped, checked against the approval
policy, and appended to the audit log; interfaces such as CLI or MCP
servers must stay thin adapters over these methods.
"""

from __future__ import annotations

import getpass
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from gpt_trader.core.risk_units import (
    fraction_to_pct_points,
    pct_points_to_fraction,
    same_trading_day,
)
from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import (
    ActorType,
    AuditAction,
    AuditEvent,
    AuditIntegrityError,
    TradeIdeaAuditLog,
    new_event_id,
)
from gpt_trader.features.trade_ideas.autonomy import (
    AUTONOMY_RANK,
    AUTONOMY_SOURCE_FAIL_CLOSED,
    AUTONOMY_SOURCE_LOG,
    AUTONOMY_SOURCE_SEEDED_DEFAULT,
    DEFAULT_AUTONOMY_MODE,
    RATCHET_ACTOR_ID,
    AutonomyIntegrityError,
    AutonomyResolution,
    AutonomyStateEntry,
    AutonomyStateLog,
    autonomy_transition_violations,
    daily_loss_breach_evidence,
    resolve_autonomy,
)
from gpt_trader.features.trade_ideas.broker_payloads import (
    BrokerTicketExportRequest,
    build_broker_neutral_ticket_payload,
)
from gpt_trader.features.trade_ideas.budget import (
    DEFAULT_RISK_BUDGET,
    BudgetLogEntry,
    RiskBudget,
    RiskBudgetLog,
)
from gpt_trader.features.trade_ideas.closeout import (
    CloseoutAttribution,
    CloseoutAttributionIntegrityError,
    CloseoutAttributionLog,
    CloseoutResolution,
    MaxLossSnapshot,
)
from gpt_trader.features.trade_ideas.models import (
    AutonomyMode,
    ConfidenceLabel,
    TicketStatus,
    TicketVenue,
    TradeIdea,
)
from gpt_trader.features.trade_ideas.policy import (
    ApprovalBudgetContext,
    ApprovalPolicy,
    PolicyViolationError,
)
from gpt_trader.features.trade_ideas.service_models import (
    AutoApprovalSkip,
    AutoApprovalSweepResult,
    DuplicateTradeIdeaError,
    PreApprovalBrokerTicketError,
    TradeIdeaListQuery,
    TradeIdeaListResult,
    TradeIdeaListSortKey,
    TradeIdeaQueryPage,
    TradeIdeaQueueExpiration,
    TradeIdeaQueueStatus,
    TradeIdeaView,
    UnknownTradeIdeaError,
    _QueryItem,
)
from gpt_trader.features.trade_ideas.store import TradeIdeaStore
from gpt_trader.features.trade_ideas.workflow import (
    TERMINAL_STATES,
    InvalidTransitionError,
    TradeIdeaState,
    validate_transition,
)

DEFAULT_IDEAS_ROOT = Path("var/data/trade_ideas")
IDEAS_ROOT_ENV_VAR = "GPT_TRADER_IDEAS_ROOT"
ACTOR_ENV_VAR = "GPT_TRADER_ACTOR"
AUTO_APPROVAL_ENV_VAR = "GPT_TRADER_IDEAS_AUTO_APPROVAL"
AUTO_APPROVAL_ACTOR_ID = "auto-approval-sweep"
AUTO_APPROVAL_REASON_PREFIX = "auto-approval: "
_AUTO_APPROVAL_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
EXPIRABLE_STATES = frozenset(
    {
        TradeIdeaState.PROPOSED,
        TradeIdeaState.NEEDS_CHANGES,
        TradeIdeaState.APPROVED,
    }
)
OPEN_BUDGET_EXPOSURE_STATES = frozenset(
    {
        TradeIdeaState.APPROVED,
        TradeIdeaState.SUBMITTED,
    }
)
_AUDIT_VENUES = frozenset({TicketVenue.COINBASE, TicketVenue.MANUAL, TicketVenue.PAPER})
_CONFIDENCE_RANK = {
    ConfidenceLabel.LOW: 0,
    ConfidenceLabel.MEDIUM: 1,
    ConfidenceLabel.HIGH: 2,
}
_STATE_SORT_RANK = {
    TradeIdeaState.PROPOSED: 0,
    TradeIdeaState.NEEDS_CHANGES: 1,
    TradeIdeaState.APPROVED: 2,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _max_loss_snapshot_for(idea: TradeIdea) -> MaxLossSnapshot:
    return MaxLossSnapshot(
        amount=idea.max_loss.amount,
        percent_of_account=idea.max_loss.percent_of_account,
        assumptions=idea.max_loss.assumptions,
    )


def _max_loss_snapshot_context(snapshot: MaxLossSnapshot) -> dict[str, object]:
    return {
        "amount": str(snapshot.amount) if snapshot.amount is not None else None,
        "percent_of_account": (
            str(snapshot.percent_of_account) if snapshot.percent_of_account is not None else None
        ),
        "assumptions": list(snapshot.assumptions),
    }


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


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _percent_of_amount(amount: Decimal, equity: Decimal) -> Decimal | None:
    if equity <= 0:
        return None
    return fraction_to_pct_points(amount / equity)


def _equity_from_max_loss_amount_percent(
    *,
    amount: Decimal | None,
    percent_of_account: Decimal | None,
) -> Decimal | None:
    if amount is None or percent_of_account is None or percent_of_account <= 0:
        return None
    return amount * Decimal("100") / percent_of_account


def _closeout_loss_percent(closeout: CloseoutAttribution) -> Decimal | None:
    if closeout.realized_profit_loss_percent is not None:
        return abs(min(closeout.realized_profit_loss_percent, Decimal("0")))
    realized_amount = closeout.realized_profit_loss_amount
    if realized_amount is None:
        return None
    if realized_amount >= 0:
        return Decimal("0")
    closeout_equity_snapshot = _equity_from_max_loss_amount_percent(
        amount=closeout.max_loss.amount,
        percent_of_account=closeout.max_loss.percent_of_account,
    )
    if closeout_equity_snapshot is None:
        return None
    return _percent_of_amount(abs(realized_amount), closeout_equity_snapshot)


def _closeout_has_realized_profit_loss(closeout: CloseoutAttribution) -> bool:
    return (
        closeout.realized_profit_loss_amount is not None
        or closeout.realized_profit_loss_percent is not None
    )


def _absolute_notional(idea: TradeIdea) -> Decimal | None:
    notional = idea.sizing_recommendation.notional
    if notional is None:
        return None
    return abs(notional)


def _page_items(
    items: tuple[_QueryItem, ...],
    *,
    limit: int | None,
    offset: int,
) -> TradeIdeaQueryPage[_QueryItem]:
    normalized_offset = max(offset, 0)
    if limit is None:
        selected = items[normalized_offset:]
    else:
        selected = items[normalized_offset : normalized_offset + max(limit, 0)]
    return TradeIdeaQueryPage(
        items=tuple(selected),
        total_count=len(items),
        limit=limit,
        offset=normalized_offset,
    )


def resolve_ideas_root(root: Path | None = None) -> Path:
    """Resolve the trade-idea storage root from arg, environment, or default."""
    if root is not None:
        return root
    configured_root = os.environ.get(IDEAS_ROOT_ENV_VAR, "").strip()
    if configured_root:
        return Path(configured_root)
    return DEFAULT_IDEAS_ROOT


def resolve_trade_idea_actor_id(actor_id: str | None = None) -> str:
    """Resolve actor identity from arg, environment, or the current OS user."""
    if actor_id and actor_id.strip():
        return actor_id
    configured_actor = os.environ.get(ACTOR_ENV_VAR, "").strip()
    if configured_actor:
        return configured_actor
    return getpass.getuser()


def resolve_auto_approval_enabled() -> bool:
    """True only when the operator explicitly enabled auto-approval (default off).

    Deliberately environment-only, with no argument override: enabling the
    Stage 2 sweep is an operator configuration act, never something a caller
    can pass in (docs/decisions/stage2-auto-approval-workflow.md).
    """
    configured = os.environ.get(AUTO_APPROVAL_ENV_VAR, "").strip().casefold()
    return configured in _AUTO_APPROVAL_ENABLED_VALUES


class TradeIdeaService:
    def __init__(
        self,
        root: Path,
        *,
        now_factory: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._root = root
        self._store = TradeIdeaStore(root / "records")
        self._audit = TradeIdeaAuditLog(root / "audit.jsonl")
        self._closeouts = CloseoutAttributionLog(root / "closeout_attributions.jsonl")
        self._budget_log = RiskBudgetLog(root / "risk_budget.jsonl")
        self._autonomy_log = AutonomyStateLog(root / "autonomy_state.jsonl")
        self._now = now_factory

    @property
    def root(self) -> Path:
        """Storage root all of this service's durable artifacts live under."""
        return self._root

    @property
    def audit_log(self) -> TradeIdeaAuditLog:
        return self._audit

    @property
    def closeout_log(self) -> CloseoutAttributionLog:
        return self._closeouts

    @property
    def budget_log(self) -> RiskBudgetLog:
        return self._budget_log

    # -- budget ----------------------------------------------------------

    def peek_budget(self) -> RiskBudget:
        """Return the active budget without seeding the log (read-only paths)."""
        return self._budget_log.current() or DEFAULT_RISK_BUDGET

    def current_budget(self) -> RiskBudget:
        """Return the active budget, seeding the accepted defaults on first use."""
        budget = self._budget_log.current()
        if budget is not None:
            return budget
        self._budget_log.append(
            BudgetLogEntry(
                timestamp=self._now(),
                actor_type=ActorType.SYSTEM,
                actor_id="seed-defaults",
                budget=DEFAULT_RISK_BUDGET,
            )
        )
        return DEFAULT_RISK_BUDGET

    # -- autonomy ----------------------------------------------------------

    def peek_autonomy(self) -> AutonomyResolution:
        """Resolve the active autonomy mode without seeding the log (read-only paths)."""
        return resolve_autonomy(self._autonomy_log)

    def current_autonomy(self) -> AutonomyResolution:
        """Resolve the active mode, seeding the accepted default on first use."""
        resolution = self.peek_autonomy()
        if resolution.source != AUTONOMY_SOURCE_SEEDED_DEFAULT:
            return resolution
        self._autonomy_log.append(
            AutonomyStateEntry(
                version=1,
                timestamp=self._now(),
                mode=DEFAULT_AUTONOMY_MODE,
                actor_type=ActorType.SYSTEM,
                actor_id="seed-defaults",
                reason=(
                    "Seeded default accepted in docs/decisions/persistent-autonomy-state.md: "
                    "human approval required for every execution"
                ),
            )
        )
        return AutonomyResolution(
            mode=DEFAULT_AUTONOMY_MODE,
            version=1,
            source=AUTONOMY_SOURCE_LOG,
        )

    def autonomy_history(self) -> list[AutonomyStateEntry]:
        """Return the full audited autonomy-level history (raises on a broken log)."""
        return self._autonomy_log.history()

    def set_autonomy_mode(
        self,
        mode: AutonomyMode,
        *,
        actor_type: ActorType,
        actor_id: str,
        reason: str,
        evidence: tuple[str, ...] = (),
    ) -> AutonomyStateEntry:
        """Enact an autonomy-level change through the audited log.

        Raising (or re-affirming) the level requires a human actor; lowering is
        open to any actor so the breach ratchet can act without a human.
        """
        resolution = self.current_autonomy()
        if resolution.source == AUTONOMY_SOURCE_FAIL_CLOSED:
            raise AutonomyIntegrityError(
                "Autonomy state log is unreadable or integrity-broken; repair it "
                f"before changing the level: {resolution.error}",
                field="autonomy_state_log",
                value=str(self._autonomy_log.path),
            )
        violations = autonomy_transition_violations(
            current_mode=resolution.mode,
            requested_mode=mode,
            actor_type=actor_type,
        )
        if violations:
            raise PolicyViolationError(
                "Autonomy change refused: " + "; ".join(violations), violations
            )
        entry = AutonomyStateEntry(
            version=(resolution.version or 0) + 1,
            timestamp=self._now(),
            mode=mode,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence=evidence,
        )
        self._autonomy_log.append(entry)
        return entry

    def _decision_autonomy(
        self,
        *,
        now: datetime,
        budget: RiskBudget | None = None,
        budget_context: ApprovalBudgetContext | None = None,
    ) -> AutonomyResolution:
        """Resolve the mode at a decision boundary, applying the automatic ratchet.

        When the resolved level grants more than human-approved execution and
        the same-trading-day realized loss has breached ``max_daily_loss_pct``,
        the level ratchets down to ``human_approved_execution`` with the breach
        evidence appended to the autonomy log before the decision proceeds.
        """
        resolution = self.current_autonomy()
        if resolution.source == AUTONOMY_SOURCE_FAIL_CLOSED:
            return resolution
        if AUTONOMY_RANK[resolution.mode] <= AUTONOMY_RANK[AutonomyMode.HUMAN_APPROVED_EXECUTION]:
            return resolution
        active_budget = budget if budget is not None else self.current_budget()
        context = (
            budget_context if budget_context is not None else self.approval_budget_context(now=now)
        )
        evidence = daily_loss_breach_evidence(
            same_day_realized_loss_pct=context.same_day_realized_loss_pct,
            max_daily_loss_pct=active_budget.max_daily_loss_pct,
            budget_version=active_budget.version,
            moment=now,
        )
        if evidence is None:
            return resolution
        entry = AutonomyStateEntry(
            version=(resolution.version or 0) + 1,
            timestamp=now,
            mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
            actor_type=ActorType.SYSTEM,
            actor_id=RATCHET_ACTOR_ID,
            reason=(
                "Automatic ratchet-down: same-trading-day realized loss breached max_daily_loss_pct"
            ),
            evidence=evidence,
        )
        self._autonomy_log.append(entry)
        return AutonomyResolution(
            mode=entry.mode,
            version=entry.version,
            source=AUTONOMY_SOURCE_LOG,
        )

    def resolve_execution_autonomy(self, *, now: datetime | None = None) -> AutonomyResolution:
        """Resolve autonomy at an execution decision boundary, applying the ratchet."""
        return self._decision_autonomy(now=now or self._now())

    @staticmethod
    def _autonomy_resolution_violations(resolution: AutonomyResolution) -> list[str]:
        if resolution.source != AUTONOMY_SOURCE_FAIL_CLOSED:
            return []
        return [
            "autonomy state resolution failed closed to "
            f"'{resolution.mode.value}': {resolution.error}"
        ]

    def approval_violations(
        self,
        idea: TradeIdea,
        *,
        actor_type: ActorType = ActorType.HUMAN,
    ) -> list[str]:
        """Return every current-policy reason an idea could not be approved."""
        resolution = self.current_autonomy()
        policy = ApprovalPolicy(resolution.mode)
        return [
            *self._autonomy_resolution_violations(resolution),
            *policy.approval_violations(
                idea,
                actor_type=actor_type,
                budget=self.current_budget(),
                open_approved_count=self.open_approved_count(),
                now=self._now(),
                review_started_at=self._review_started_at(idea.decision_id),
                budget_context=self.approval_budget_context(
                    exclude_decision_id=idea.decision_id,
                ),
            ),
        ]

    def approval_budget_context(
        self,
        *,
        exclude_decision_id: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalBudgetContext:
        """Return aggregate budget exposure for approval policy evaluation."""
        evaluation_time = now or self._now()
        latest_events = self._latest_audit_events_by_decision_id()
        open_ideas: list[TradeIdea] = []
        closeouts: list[CloseoutAttribution] = []
        for decision_id, event in latest_events.items():
            if event.after_state in OPEN_BUDGET_EXPOSURE_STATES:
                if decision_id == exclude_decision_id:
                    continue
                open_ideas.append(self.load_record_version(decision_id, event.record_hash))
                continue
            if event.after_state not in TERMINAL_STATES:
                continue
            events = tuple(self._audit.read_events(decision_id))
            idea = self.load_record_version(decision_id, event.record_hash)
            closeout = self._validated_closeout_attribution(idea, events)
            if (
                event.after_state is TradeIdeaState.FILLED
                and closeout is None
                and decision_id != exclude_decision_id
            ):
                open_ideas.append(idea)
                continue
            if closeout is not None and same_trading_day(closeout.timestamp, evaluation_time):
                if (
                    event.after_state is TradeIdeaState.FILLED
                    or _closeout_has_realized_profit_loss(closeout)
                ):
                    closeouts.append(closeout)
        account_equity_snapshot = self._account_equity_snapshot(
            # Non-mutating read: building a budget context (e.g. during a
            # render-only ticket export) must not seed risk_budget.jsonl.
            budget_equity=self.peek_budget().account_equity,
            open_ideas=open_ideas,
            closeouts=closeouts,
        )
        closeout_loss_pcts = tuple(_closeout_loss_percent(closeout) for closeout in closeouts)
        same_day_realized_loss_pct = sum(
            (loss_pct for loss_pct in closeout_loss_pcts if loss_pct is not None),
            Decimal("0"),
        )
        same_day_realized_loss_unavailable_count = sum(
            1 for loss_pct in closeout_loss_pcts if loss_pct is None
        )
        open_at_risk_pcts = tuple(idea.max_loss.percent_of_account for idea in open_ideas)
        open_approved_at_risk_pct = sum(
            (risk_pct for risk_pct in open_at_risk_pcts if risk_pct is not None),
            Decimal("0"),
        )
        open_at_risk_unavailable_count = sum(
            1 for risk_pct in open_at_risk_pcts if risk_pct is None
        )
        open_notionals = tuple(_absolute_notional(idea) for idea in open_ideas)
        open_notional = sum(
            (notional for notional in open_notionals if notional is not None), Decimal("0")
        )
        open_notional_unavailable_count = sum(1 for notional in open_notionals if notional is None)
        return ApprovalBudgetContext(
            same_day_realized_loss_pct=same_day_realized_loss_pct,
            same_day_realized_loss_unavailable_count=(same_day_realized_loss_unavailable_count),
            open_approved_at_risk_pct=open_approved_at_risk_pct,
            open_at_risk_unavailable_count=open_at_risk_unavailable_count,
            open_notional=open_notional,
            open_notional_unavailable_count=open_notional_unavailable_count,
            account_equity_snapshot=account_equity_snapshot,
        )

    def budget_headroom(self, *, now: datetime | None = None) -> dict[str, object]:
        """Return read-only aggregate budget headroom for operators and agents."""
        evaluation_time = now or self._now()
        budget = self.current_budget()
        context = self.approval_budget_context(now=evaluation_time)
        account_equity = context.account_equity_snapshot
        daily_loss_used_pct = context.same_day_realized_loss_pct + context.open_approved_at_risk_pct
        daily_loss_headroom_pct = max(
            budget.max_daily_loss_pct - daily_loss_used_pct,
            Decimal("0"),
        )
        open_notional_pct = None
        open_notional_headroom = None
        open_notional_headroom_pct = None
        if account_equity is not None and account_equity > 0:
            open_notional_pct = _percent_of_amount(context.open_notional, account_equity)
            max_open_notional = account_equity * pct_points_to_fraction(
                budget.max_open_notional_pct
            )
            open_notional_headroom = max(max_open_notional - context.open_notional, Decimal("0"))
            open_notional_headroom_pct = max(
                budget.max_open_notional_pct - (open_notional_pct or Decimal("0")),
                Decimal("0"),
            )
        return {
            "evaluated_at": evaluation_time.isoformat(),
            "account_equity_snapshot": _decimal_to_str(account_equity),
            "same_day_realized_loss_pct": str(context.same_day_realized_loss_pct),
            "same_day_realized_loss_unavailable_count": (
                context.same_day_realized_loss_unavailable_count
            ),
            "open_approved_at_risk_pct": str(context.open_approved_at_risk_pct),
            "open_at_risk_unavailable_count": context.open_at_risk_unavailable_count,
            "daily_loss_used_pct": str(daily_loss_used_pct),
            "daily_loss_headroom_pct": str(daily_loss_headroom_pct),
            "open_notional": str(context.open_notional),
            "open_notional_unavailable_count": context.open_notional_unavailable_count,
            "open_notional_pct": _decimal_to_str(open_notional_pct),
            "open_notional_headroom": _decimal_to_str(open_notional_headroom),
            "open_notional_headroom_pct": _decimal_to_str(open_notional_headroom_pct),
            "open_budget_exposure_states": sorted(
                state.value for state in OPEN_BUDGET_EXPOSURE_STATES
            ),
        }

    def validate_new_proposal(self, idea: TradeIdea) -> None:
        """Validate proposal lifecycle preconditions without writing state."""
        self._require_default_preapproval_broker_ticket(idea)
        self._require_new_decision_id(idea.decision_id)

    def validate_new_proposals(self, ideas: tuple[TradeIdea, ...]) -> None:
        """Validate proposal batch preconditions without writing state."""
        seen_decision_ids: set[str] = set()
        for idea in ideas:
            if idea.decision_id in seen_decision_ids:
                raise DuplicateTradeIdeaError(
                    f"Trade idea decision_id '{idea.decision_id}' appears more than once "
                    "in the proposal batch",
                    field="decision_id",
                    value=idea.decision_id,
                )
            seen_decision_ids.add(idea.decision_id)
            self.validate_new_proposal(idea)

    def validate_resubmission(self, idea: TradeIdea) -> None:
        """Validate resubmission lifecycle preconditions without writing state."""
        self._require_default_preapproval_broker_ticket(idea)
        self._require_idea(idea.decision_id)
        current_state = self._audit.current_state(idea.decision_id)
        if current_state is not TradeIdeaState.NEEDS_CHANGES:
            recorded = current_state.value if current_state else "none"
            raise InvalidTransitionError(
                f"Trade idea '{idea.decision_id}' must be in state "
                f"'{TradeIdeaState.NEEDS_CHANGES.value}' before resubmit; got '{recorded}'",
                field="before_state",
                value=recorded,
            )
        validate_transition(current_state, TradeIdeaState.PROPOSED)

    def update_budget(self, budget: RiskBudget, actor_type: ActorType, actor_id: str) -> None:
        """Enact a new budget version, subject to the autonomy-mode policy."""
        now = self._now()
        resolution = self._decision_autonomy(now=now)
        policy = ApprovalPolicy(resolution.mode)
        violations = [
            *self._autonomy_resolution_violations(resolution),
            *policy.budget_change_violations(actor_type),
        ]
        if violations:
            raise PolicyViolationError(
                "Budget change refused: " + "; ".join(violations), violations
            )
        self.current_budget()  # ensure the seed entry exists so versions stay contiguous
        self._budget_log.append(
            BudgetLogEntry(
                timestamp=self._now(),
                actor_type=actor_type,
                actor_id=actor_id,
                budget=budget,
            )
        )
        # The enacted budget is part of this decision: re-check the ratchet
        # against it so tightening max_daily_loss_pct below already-realized
        # same-day losses lowers the level now, not at some later decision.
        self._decision_autonomy(now=now)

    # -- lifecycle actions -------------------------------------------------

    def propose(
        self,
        idea: TradeIdea,
        actor_id: str,
        actor_type: ActorType = ActorType.AI,
        reason: str = "New trade idea proposed",
        evidence: tuple[str, ...] = (),
    ) -> TradeIdeaView:
        self.validate_new_proposal(idea)
        self._store.save(idea)
        self._append(
            idea,
            action=AuditAction.PROPOSED,
            after_state=TradeIdeaState.PROPOSED,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence=evidence,
        )
        return self.get(idea.decision_id)

    def propose_batch(
        self,
        ideas: tuple[TradeIdea, ...],
        *,
        actor_id: str,
        actor_type: ActorType = ActorType.AI,
        reason: str = "New trade idea proposed",
        evidence: tuple[str, ...] = (),
    ) -> list[TradeIdeaView]:
        """Persist a batch of new proposals as an all-or-nothing operation."""
        self.validate_new_proposals(ideas)
        if not ideas:
            return []

        audit_path = self._audit.path
        original_audit = audit_path.read_bytes() if audit_path.exists() else None
        created_decision_ids: list[str] = []
        try:
            views: list[TradeIdeaView] = []
            for idea in ideas:
                created_decision_ids.append(idea.decision_id)
                self._store.save(idea)
                self._append(
                    idea,
                    action=AuditAction.PROPOSED,
                    after_state=TradeIdeaState.PROPOSED,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    reason=reason,
                    evidence=evidence,
                )
                views.append(self.get(idea.decision_id))
        except Exception:
            self._restore_failed_proposal_batch(
                created_decision_ids=tuple(created_decision_ids),
                original_audit=original_audit,
            )
            raise
        return views

    def request_changes(self, decision_id: str, actor_id: str, reason: str) -> TradeIdeaView:
        idea = self._require_idea(decision_id)
        self._append(
            idea,
            action=AuditAction.CHANGED,
            after_state=TradeIdeaState.NEEDS_CHANGES,
            actor_type=ActorType.HUMAN,
            actor_id=actor_id,
            reason=reason,
        )
        return self.get(decision_id)

    def resubmit(
        self,
        idea: TradeIdea,
        actor_id: str,
        actor_type: ActorType = ActorType.AI,
        reason: str = "Revised after requested changes",
    ) -> TradeIdeaView:
        self.validate_resubmission(idea)
        self._store.save(idea)
        self._append(
            idea,
            action=AuditAction.PROPOSED,
            after_state=TradeIdeaState.PROPOSED,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        return self.get(idea.decision_id)

    def approve(self, decision_id: str, actor_id: str, reason: str) -> TradeIdeaView:
        idea = self._require_idea(decision_id)
        now = self._now()
        budget = self.current_budget()
        budget_context = self.approval_budget_context(
            exclude_decision_id=decision_id,
            now=now,
        )
        resolution = self._decision_autonomy(
            now=now,
            budget=budget,
            budget_context=budget_context,
        )
        policy = ApprovalPolicy(resolution.mode)
        violations = [
            *self._autonomy_resolution_violations(resolution),
            *policy.approval_violations(
                idea,
                actor_type=ActorType.HUMAN,
                budget=budget,
                open_approved_count=self.open_approved_count(),
                now=now,
                review_started_at=self._review_started_at(decision_id),
                budget_context=budget_context,
            ),
        ]
        if violations:
            raise PolicyViolationError(
                f"Approval of '{decision_id}' refused: " + "; ".join(violations), violations
            )
        self._append(
            idea,
            action=AuditAction.APPROVED,
            after_state=TradeIdeaState.APPROVED,
            actor_type=ActorType.HUMAN,
            actor_id=actor_id,
            reason=reason,
        )
        return self.get(decision_id)

    def auto_approve_sweep(
        self,
        *,
        actor_id: str = AUTO_APPROVAL_ACTOR_ID,
    ) -> AutoApprovalSweepResult:
        """Auto-approve every violation-free proposed idea inside the budget envelope.

        The Stage 2 exception scoped by
        docs/decisions/stage2-auto-approval-workflow.md: the sweep refuses
        loudly unless the operator enabled the default-off feature flag and
        the audited autonomy log resolves to ``bounded_autonomy``. The mode is
        re-resolved before each decision so the daily-loss ratchet can halt a
        sweep mid-pass. Ideas with any violation stay ``proposed`` for human
        review and are returned with their reasons — never silently dropped.
        """
        gate_violations: list[str] = []
        if not resolve_auto_approval_enabled():
            gate_violations.append(
                f"auto-approval feature flag is off (default); set "
                f"{AUTO_APPROVAL_ENV_VAR}=1 to enable the Stage 2 sweep"
            )
            raise PolicyViolationError(
                "Auto-approval sweep refused: " + "; ".join(gate_violations),
                gate_violations,
            )
        now = self._now()
        resolution = self._decision_autonomy(now=now)
        gate_violations.extend(self._autonomy_resolution_violations(resolution))
        if resolution.mode is not AutonomyMode.BOUNDED_AUTONOMY:
            gate_violations.append(
                "auto-approval requires audited autonomy mode "
                f"'{AutonomyMode.BOUNDED_AUTONOMY.value}'; resolved "
                f"'{resolution.mode.value}' (source={resolution.source})"
            )
        if gate_violations:
            raise PolicyViolationError(
                "Auto-approval sweep refused: " + "; ".join(gate_violations),
                gate_violations,
            )

        candidates = sorted(
            self.list_views(TradeIdeaState.PROPOSED),
            key=lambda view: (view.events[-1].timestamp, view.idea.decision_id),
        )
        approved: list[TradeIdeaView] = []
        skipped: list[AutoApprovalSkip] = []
        for candidate in candidates:
            idea = candidate.idea
            decision_time = self._now()
            budget = self.current_budget()
            budget_context = self.approval_budget_context(
                exclude_decision_id=idea.decision_id,
                now=decision_time,
            )
            decision_resolution = self._decision_autonomy(
                now=decision_time,
                budget=budget,
                budget_context=budget_context,
            )
            policy = ApprovalPolicy(decision_resolution.mode)
            violations = [
                *self._autonomy_resolution_violations(decision_resolution),
                *policy.approval_violations(
                    idea,
                    actor_type=ActorType.SYSTEM,
                    budget=budget,
                    open_approved_count=self.open_approved_count(),
                    now=decision_time,
                    review_started_at=self._review_started_at(idea.decision_id),
                    budget_context=budget_context,
                ),
            ]
            if violations:
                self._append(
                    idea,
                    action=AuditAction.AUTO_APPROVAL_SKIPPED,
                    after_state=TradeIdeaState.PROPOSED,
                    actor_type=ActorType.SYSTEM,
                    actor_id=actor_id,
                    reason=(
                        f"{AUTO_APPROVAL_REASON_PREFIX}skipped because "
                        "approval-policy violations remain"
                    ),
                    evidence=(
                        f"autonomy_state version {decision_resolution.version} "
                        f"mode={decision_resolution.mode.value} "
                        f"(source={decision_resolution.source})",
                        f"risk budget version {budget.version}: "
                        f"approval_violations={len(violations)}",
                        *(f"violation: {violation}" for violation in violations),
                    ),
                )
                skipped.append(
                    AutoApprovalSkip(
                        decision_id=idea.decision_id,
                        violations=tuple(violations),
                    )
                )
                continue
            self._append(
                idea,
                action=AuditAction.APPROVED,
                after_state=TradeIdeaState.APPROVED,
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                reason=(
                    f"{AUTO_APPROVAL_REASON_PREFIX}zero approval-policy violations "
                    f"inside the budget envelope of risk budget version {budget.version}"
                ),
                evidence=(
                    f"autonomy_state version {decision_resolution.version} "
                    f"mode={decision_resolution.mode.value} "
                    f"(source={decision_resolution.source})",
                    f"risk budget version {budget.version}: approval_violations=0",
                    "budget envelope: "
                    f"same_day_realized_loss_pct={budget_context.same_day_realized_loss_pct} "
                    f"open_approved_at_risk_pct={budget_context.open_approved_at_risk_pct} "
                    f"candidate_max_loss_pct={idea.max_loss.percent_of_account} "
                    f"max_daily_loss_pct={budget.max_daily_loss_pct}",
                ),
            )
            approved.append(self.get(idea.decision_id))
        return AutoApprovalSweepResult(
            evaluated_at=now,
            autonomy_mode=resolution.mode.value,
            autonomy_version=resolution.version,
            approved=tuple(approved),
            skipped=tuple(skipped),
        )

    def reject(
        self,
        decision_id: str,
        actor_id: str,
        reason: str,
        actor_type: ActorType = ActorType.HUMAN,
    ) -> TradeIdeaView:
        idea = self._require_idea(decision_id)
        self._append(
            idea,
            action=AuditAction.REJECTED,
            after_state=TradeIdeaState.REJECTED,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        return self.get(decision_id)

    def cancel(
        self,
        decision_id: str,
        actor_id: str,
        reason: str,
        actor_type: ActorType = ActorType.HUMAN,
    ) -> TradeIdeaView:
        idea = self._require_idea(decision_id)
        self._append(
            idea,
            action=AuditAction.CANCELLED,
            after_state=TradeIdeaState.CANCELLED,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        return self.get(decision_id)

    def expire(
        self,
        decision_id: str,
        actor_id: str = "expiry-sweep",
        reason: str = "Idea passed its review or execution deadline",
        actor_type: ActorType = ActorType.SYSTEM,
    ) -> TradeIdeaView:
        idea = self._require_idea(decision_id)
        self._append(
            idea,
            action=AuditAction.EXPIRED,
            after_state=TradeIdeaState.EXPIRED,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
        )
        return self.get(decision_id)

    def expire_due_ideas(
        self,
        *,
        actor_id: str = "expiry-sweep",
        reason: str = "Idea passed its review or execution deadline",
        actor_type: ActorType = ActorType.SYSTEM,
    ) -> list[TradeIdeaView]:
        """Expire all stale ideas that can legally transition to expired."""
        now = self._now()
        budget = self._budget_log.current() or DEFAULT_RISK_BUDGET
        # Review-latency checks are autonomy-mode independent; a static policy
        # is safe here and keeps the expiry sweep read-only on the autonomy log.
        policy = ApprovalPolicy()
        expired: list[TradeIdeaView] = []
        for view in self.list_views():
            if view.state not in EXPIRABLE_STATES:
                continue
            expires_at = view.idea.time_horizon.expires_at
            idea_expired = expires_at is not None and expires_at <= now
            review_latency_violation = policy.review_latency_violation(
                review_started_at=self._review_started_at_from_events(view.events),
                budget=budget,
                now=now,
            )
            if idea_expired or review_latency_violation is not None:
                expired.append(
                    self.expire(
                        view.idea.decision_id,
                        actor_id=actor_id,
                        reason=reason,
                        actor_type=actor_type,
                    )
                )
        return expired

    def record_submission(
        self,
        decision_id: str,
        actor_id: str,
        venue: str,
        external_order_id: str = "",
        reason: str = "Approved ticket submitted",
        actor_type: ActorType = ActorType.SYSTEM,
        evidence: tuple[str, ...] = (),
    ) -> TradeIdeaView:
        venue = _validate_audit_venue(venue)
        idea = self._require_idea(decision_id)
        self._append(
            idea,
            action=AuditAction.SUBMITTED,
            after_state=TradeIdeaState.SUBMITTED,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence=evidence,
            venue=venue,
            external_order_id=external_order_id,
        )
        return self.get(decision_id)

    def record_fill(
        self,
        decision_id: str,
        actor_id: str,
        venue: str,
        external_order_id: str = "",
        reason: str = "Venue confirmed fill",
        actor_type: ActorType = ActorType.VENUE,
    ) -> TradeIdeaView:
        venue = _validate_audit_venue(venue)
        idea = self._require_idea(decision_id)
        self._append(
            idea,
            action=AuditAction.FILLED,
            after_state=TradeIdeaState.FILLED,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            venue=venue,
            external_order_id=external_order_id,
        )
        return self.get(decision_id)

    def record_closeout_attribution(
        self,
        decision_id: str,
        *,
        actor_id: str,
        resolution: CloseoutResolution | str,
        realized_profit_loss_amount: object = None,
        realized_profit_loss_percent: object = None,
        realized_profit_loss_unavailable_reason: str = "",
        evidence: tuple[str, ...] = (),
        actor_type: ActorType = ActorType.HUMAN,
    ) -> CloseoutAttribution:
        """Record why a terminal idea resolved and its realized profit/loss evidence."""
        view = self.get(decision_id)
        if view.state not in TERMINAL_STATES:
            raise InvalidTransitionError(
                f"Trade idea '{decision_id}' must be terminal before closeout attribution; "
                f"got '{view.state.value}'",
                field="after_state",
                value=view.state.value,
            )

        terminal_event = view.events[-1]
        record = CloseoutAttribution(
            decision_id=decision_id,
            timestamp=self._now(),
            actor_type=actor_type.value,
            actor_id=actor_id,
            terminal_event_id=terminal_event.event_id,
            record_hash=terminal_event.record_hash,
            resolution=CloseoutResolution(resolution),
            realized_profit_loss_amount=cast(
                Decimal | None,
                realized_profit_loss_amount,
            ),
            realized_profit_loss_percent=cast(
                Decimal | None,
                realized_profit_loss_percent,
            ),
            realized_profit_loss_unavailable_reason=realized_profit_loss_unavailable_reason,
            max_loss=MaxLossSnapshot(
                amount=view.idea.max_loss.amount,
                percent_of_account=view.idea.max_loss.percent_of_account,
                assumptions=view.idea.max_loss.assumptions,
            ),
            evidence=evidence,
        )
        return self._closeouts.append(record)

    def get_closeout_attribution(self, decision_id: str) -> CloseoutAttribution | None:
        return self.get(decision_id).closeout_attribution

    def export_broker_ticket_payload(
        self,
        decision_id: str,
        *,
        venue: str,
        venue_order_type: str,
        time_in_force: str,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        """Render a deterministic broker-neutral ticket without mutating records."""
        view = self.get(decision_id)
        budget = self._budget_log.current()
        budget_source = "risk_budget_log" if budget is not None else "default"
        effective_budget = budget or DEFAULT_RISK_BUDGET
        export_time = self._now()
        expires_at = view.idea.time_horizon.expires_at
        # Render-only export: resolve the mode without seeding or ratcheting
        # the autonomy log, mirroring the non-mutating budget read above.
        autonomy_resolution = self.peek_autonomy()
        policy = ApprovalPolicy(autonomy_resolution.mode)
        policy_violations = [
            *self._autonomy_resolution_violations(autonomy_resolution),
            *policy.approval_violations(
                view.idea,
                actor_type=ActorType.HUMAN,
                budget=effective_budget,
                open_approved_count=self._open_approved_count_excluding(decision_id),
                now=export_time,
                review_started_at=self._review_started_at_from_events(view.events),
                budget_context=self.approval_budget_context(
                    exclude_decision_id=decision_id,
                    now=export_time,
                ),
            ),
        ]
        if (
            view.state is TradeIdeaState.APPROVED
            and expires_at is not None
            and expires_at <= export_time
        ):
            violation = f"Idea expired at {expires_at.isoformat()}; export no stale ticket"
            raise PolicyViolationError(
                f"Ticket export for '{decision_id}' refused: {violation}",
                [violation, *policy_violations],
            )
        request = BrokerTicketExportRequest.from_values(
            venue=venue,
            venue_order_type=venue_order_type,
            time_in_force=time_in_force,
            client_order_id=client_order_id,
        )
        return build_broker_neutral_ticket_payload(
            idea=view.idea,
            state=view.state,
            events=view.events,
            request=request,
            budget=effective_budget,
            budget_source=budget_source,
            active_autonomy_mode=autonomy_resolution.mode,
            active_autonomy_source=autonomy_resolution.source,
            export_time=export_time,
            approval_policy_violations=policy_violations,
        )

    def load_record_version(self, decision_id: str, record_hash: str) -> TradeIdea:
        """Load the exact record version referenced by an audit event."""
        try:
            idea = self._store.load_version(decision_id, record_hash)
        except KeyError as error:
            missing_field = str(error.args[0]) if error.args else "unknown"
            raise AuditIntegrityError(
                f"Stored trade idea '{decision_id}' version '{record_hash}' is missing "
                f"required field '{missing_field}'",
                field="record_hash",
                value=record_hash,
                context={
                    "decision_id": decision_id,
                    "record_hash": record_hash,
                    "missing_field": missing_field,
                },
            ) from error
        except (InvalidOperation, TypeError, ValueError) as error:
            raise AuditIntegrityError(
                f"Stored trade idea '{decision_id}' version '{record_hash}' is invalid: {error}",
                field="record_hash",
                value=record_hash,
            ) from error
        if idea is None:
            raise AuditIntegrityError(
                f"Stored trade idea '{decision_id}' is missing audit record_hash '{record_hash}'",
                field="record_hash",
                value=record_hash,
            )
        if idea.decision_id != decision_id:
            raise AuditIntegrityError(
                f"Stored trade idea '{decision_id}' version '{record_hash}' contains "
                f"decision_id '{idea.decision_id}'",
                field="decision_id",
                value=idea.decision_id,
            )
        actual_record_hash = idea.record_hash()
        if actual_record_hash != record_hash:
            raise AuditIntegrityError(
                f"Stored trade idea '{decision_id}' version '{record_hash}' hashes to "
                f"'{actual_record_hash}'",
                field="record_hash",
                value=actual_record_hash,
            )
        return idea

    # -- queries -----------------------------------------------------------

    def get(self, decision_id: str) -> TradeIdeaView:
        idea = self._require_idea(decision_id)
        events = tuple(self._audit.read_events(decision_id))
        if not events:
            raise AuditIntegrityError(
                f"Stored trade idea '{decision_id}' has no audit trail",
                field="decision_id",
                value=decision_id,
            )
        state = events[-1].after_state
        return TradeIdeaView(
            idea=idea,
            state=state,
            events=events,
            closeout_attribution=self._validated_closeout_attribution(idea, events),
        )

    def list_views(self, state: TradeIdeaState | None = None) -> list[TradeIdeaView]:
        """Return stored views, preserving the historical optional state filter."""
        return list(self.list_view_result(TradeIdeaListQuery(state=state)).views)

    def list_view_result(
        self,
        query: TradeIdeaListQuery | None = None,
    ) -> TradeIdeaListResult:
        """Return filtered, sorted, and paginated trade-idea views."""
        query = query or TradeIdeaListQuery()
        _validate_list_query(query)
        views = [self.get(decision_id) for decision_id in self._store.list_decision_ids()]
        filtered = [view for view in views if _matches_list_query(view, query)]
        ordered = _sort_list_views(filtered, query)
        total_count = len(ordered)
        start = query.offset
        stop = None if query.limit is None else start + query.limit
        page = tuple(ordered[start:stop])
        return TradeIdeaListResult(
            views=page,
            total_count=total_count,
            offset=query.offset,
            limit=query.limit,
            has_more=start + len(page) < total_count,
        )

    def queue_status(self, *, warning_window_hours: int = 24) -> TradeIdeaQueueStatus:
        """Return read-only approval queue counts and upcoming pending expirations."""
        if warning_window_hours < 0:
            raise ValidationError(
                "Trade idea queue warning_window_hours must be non-negative",
                field="warning_window_hours",
                value=warning_window_hours,
            )

        as_of = self._now()
        window_end = as_of + timedelta(hours=warning_window_hours)
        budget = self._budget_log.current() or DEFAULT_RISK_BUDGET
        proposed = self.list_view_result(
            TradeIdeaListQuery(
                state=TradeIdeaState.PROPOSED,
                sort_by=TradeIdeaListSortKey.EXPIRES_AT,
            )
        ).views
        needs_changes = self.list_view_result(
            TradeIdeaListQuery(
                state=TradeIdeaState.NEEDS_CHANGES,
                sort_by=TradeIdeaListSortKey.EXPIRES_AT,
            )
        ).views
        upcoming_expirations: list[TradeIdeaQueueExpiration] = []
        for view in (*proposed, *needs_changes):
            expiration = _queue_expiration(
                view,
                as_of,
                window_end,
                budget,
                review_started_at=self._review_started_at_from_events(view.events),
            )
            if expiration is not None:
                upcoming_expirations.append(expiration)
        upcoming = tuple(
            sorted(
                upcoming_expirations,
                key=lambda expiration: (expiration.expires_at, expiration.decision_id),
            )
        )
        return TradeIdeaQueueStatus(
            as_of=as_of,
            warning_window_hours=warning_window_hours,
            proposed_count=len(proposed),
            needs_changes_count=len(needs_changes),
            upcoming_expirations=upcoming,
        )

    def list_audit_events(
        self,
        *,
        decision_id: str | None = None,
        actor_id: str | None = None,
        actor_type: ActorType | str | None = None,
        action: AuditAction | str | None = None,
        state: TradeIdeaState | str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> TradeIdeaQueryPage[AuditEvent]:
        """Return a filtered, stable page of audit events without mutating storage."""
        requested_actor_type = ActorType(actor_type) if actor_type is not None else None
        requested_action = AuditAction(action) if action is not None else None
        requested_state = TradeIdeaState(state) if state is not None else None
        events = tuple(
            event
            for event in self._audit.read_events(decision_id)
            if (actor_id is None or event.actor_id == actor_id)
            and (requested_actor_type is None or event.actor_type is requested_actor_type)
            and (requested_action is None or event.action is requested_action)
            and (requested_state is None or event.after_state is requested_state)
            and _timestamp_in_window(event.timestamp, since=since, until=until)
        )
        return _page_items(events, limit=limit, offset=offset)

    def query_closeout_records(
        self,
        *,
        decision_id: str | None = None,
        actor_id: str | None = None,
        actor_type: ActorType | str | None = None,
        resolution: CloseoutResolution | str | None = None,
        has_evidence: bool | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> TradeIdeaQueryPage[CloseoutAttribution]:
        """Return a filtered, stable page of closeout attribution records."""
        requested_actor_type = ActorType(actor_type).value if actor_type is not None else None
        requested_resolution = CloseoutResolution(resolution) if resolution is not None else None
        records: list[CloseoutAttribution] = []
        if decision_id is not None:
            try:
                views = [self.get(decision_id)]
            except UnknownTradeIdeaError:
                views = []
        else:
            views = self.list_views()
        for view in views:
            record = view.closeout_attribution
            if record is None:
                continue
            if (
                (decision_id is None or record.decision_id == decision_id)
                and (actor_id is None or record.actor_id == actor_id)
                and (requested_actor_type is None or record.actor_type == requested_actor_type)
                and (requested_resolution is None or record.resolution is requested_resolution)
                and (has_evidence is None or bool(record.evidence) is has_evidence)
                and _timestamp_in_window(record.timestamp, since=since, until=until)
            ):
                records.append(record)
        ordered_records = tuple(
            sorted(records, key=lambda record: (record.timestamp, record.decision_id))
        )
        return _page_items(ordered_records, limit=limit, offset=offset)

    def open_approved_count(self) -> int:
        approved_count = 0
        for decision_id, event in self._latest_audit_events_by_decision_id().items():
            if event.after_state is not TradeIdeaState.APPROVED:
                continue
            self._require_idea(decision_id)
            approved_count += 1
        return approved_count

    def _open_approved_count_excluding(self, excluded_decision_id: str) -> int:
        approved_count = 0
        for decision_id, event in self._latest_audit_events_by_decision_id().items():
            if decision_id == excluded_decision_id:
                continue
            if event.after_state is not TradeIdeaState.APPROVED:
                continue
            self._require_idea(decision_id)
            approved_count += 1
        return approved_count

    # -- internals -----------------------------------------------------------

    def _account_equity_snapshot(
        self,
        *,
        budget_equity: Decimal | None,
        open_ideas: list[TradeIdea],
        closeouts: list[CloseoutAttribution],
    ) -> Decimal | None:
        # Equity must come from a source independent of the candidate under
        # evaluation; a candidate could otherwise inflate max_loss.amount to
        # fabricate an arbitrarily large denominator for the notional cap.
        # Precedence: operator-attested budget equity, then records that
        # already passed human approval. With no independent source the policy
        # fails closed (None blocks max_open_notional_pct verification).
        if budget_equity is not None:
            return budget_equity
        for idea in open_ideas:
            equity = _equity_from_max_loss_amount_percent(
                amount=idea.max_loss.amount,
                percent_of_account=idea.max_loss.percent_of_account,
            )
            if equity is not None:
                return equity
        for closeout in closeouts:
            equity = _equity_from_max_loss_amount_percent(
                amount=closeout.max_loss.amount,
                percent_of_account=closeout.max_loss.percent_of_account,
            )
            if equity is not None:
                return equity
        return None

    def _latest_audit_events_by_decision_id(self) -> dict[str, AuditEvent]:
        latest_events: dict[str, AuditEvent] = {}
        for event in self._audit.read_events():
            latest_events[event.decision_id] = event
        return latest_events

    def _validated_closeout_attribution(
        self,
        idea: TradeIdea,
        events: tuple[AuditEvent, ...],
    ) -> CloseoutAttribution | None:
        decision_id = idea.decision_id
        closeout = self._closeouts.get(decision_id)
        if closeout is None:
            return None

        current_event = events[-1]
        current_max_loss = _max_loss_snapshot_for(idea)
        context = {
            "decision_id": decision_id,
            "stored_terminal_event_id": closeout.terminal_event_id,
            "current_terminal_event_id": current_event.event_id,
            "stored_record_hash": closeout.record_hash,
            "current_record_hash": current_event.record_hash,
            "stored_max_loss": _max_loss_snapshot_context(closeout.max_loss),
            "current_max_loss": _max_loss_snapshot_context(current_max_loss),
        }
        if current_event.after_state not in TERMINAL_STATES:
            raise CloseoutAttributionIntegrityError(
                f"Closeout attribution for decision_id '{decision_id}' cannot be current "
                f"because latest audit state is '{current_event.after_state.value}', "
                "not terminal",
                field="after_state",
                value=current_event.after_state.value,
                context=context,
            )

        mismatch_details: list[str] = []
        mismatch_field = "closeout_attribution"
        if closeout.terminal_event_id != current_event.event_id:
            mismatch_field = "terminal_event_id"
            mismatch_details.append(
                f"terminal_event_id expected '{current_event.event_id}' "
                f"but found '{closeout.terminal_event_id}'"
            )
        if closeout.record_hash != current_event.record_hash:
            if mismatch_field == "closeout_attribution":
                mismatch_field = "record_hash"
            mismatch_details.append(
                f"record_hash expected '{current_event.record_hash}' "
                f"but found '{closeout.record_hash}'"
            )
        if mismatch_details:
            raise CloseoutAttributionIntegrityError(
                f"Closeout attribution for decision_id '{decision_id}' does not match "
                f"the current terminal audit event: {'; '.join(mismatch_details)}",
                field=mismatch_field,
                value=decision_id,
                context=context,
            )
        max_loss_mismatch_details: list[str] = []
        max_loss_mismatch_field = "max_loss"
        if closeout.max_loss.amount != current_max_loss.amount:
            max_loss_mismatch_field = "max_loss.amount"
            max_loss_mismatch_details.append(
                f"max_loss.amount expected '{current_max_loss.amount}' "
                f"but found '{closeout.max_loss.amount}'"
            )
        if closeout.max_loss.percent_of_account != current_max_loss.percent_of_account:
            if max_loss_mismatch_field == "max_loss":
                max_loss_mismatch_field = "max_loss.percent_of_account"
            max_loss_mismatch_details.append(
                "max_loss.percent_of_account expected "
                f"'{current_max_loss.percent_of_account}' "
                f"but found '{closeout.max_loss.percent_of_account}'"
            )
        if closeout.max_loss.assumptions != current_max_loss.assumptions:
            if max_loss_mismatch_field == "max_loss":
                max_loss_mismatch_field = "max_loss.assumptions"
            max_loss_mismatch_details.append(
                f"max_loss.assumptions expected {list(current_max_loss.assumptions)!r} "
                f"but found {list(closeout.max_loss.assumptions)!r}"
            )
        if max_loss_mismatch_details:
            raise CloseoutAttributionIntegrityError(
                f"Closeout attribution for decision_id '{decision_id}' does not match "
                "the audited max-loss snapshot from the current terminal record: "
                f"{'; '.join(max_loss_mismatch_details)}",
                field=max_loss_mismatch_field,
                value=decision_id,
                context=context,
            )
        return closeout

    def _require_idea(self, decision_id: str) -> TradeIdea:
        try:
            idea = self._store.load_latest(decision_id)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValidationError(
                f"Stored trade idea '{decision_id}' is invalid: {error}",
                field="decision_id",
                value=decision_id,
            ) from error
        if idea is None:
            raise UnknownTradeIdeaError(
                f"No trade idea stored for decision_id '{decision_id}'",
                field="decision_id",
                value=decision_id,
            )
        self._verify_latest_record_is_audited(idea)
        return idea

    def _verify_latest_record_is_audited(self, idea: TradeIdea) -> None:
        events = tuple(self._audit.read_events(idea.decision_id))
        if not events:
            return
        audited_record_hash = events[-1].record_hash
        latest_record_hash = idea.record_hash()
        if latest_record_hash == audited_record_hash:
            return
        raise AuditIntegrityError(
            f"Stored trade idea '{idea.decision_id}' latest record hash "
            f"'{latest_record_hash}' does not match latest audit record_hash "
            f"'{audited_record_hash}'",
            field="record_hash",
            value=latest_record_hash,
        )

    def _require_new_decision_id(self, decision_id: str) -> None:
        if self._store.exists(decision_id) or self._audit.current_state(decision_id) is not None:
            raise DuplicateTradeIdeaError(
                f"Trade idea decision_id '{decision_id}' already exists; use resubmit "
                "after requested changes",
                field="decision_id",
                value=decision_id,
            )

    def _restore_failed_proposal_batch(
        self,
        *,
        created_decision_ids: tuple[str, ...],
        original_audit: bytes | None,
    ) -> None:
        for decision_id in created_decision_ids:
            try:
                shutil.rmtree(self._store.root / decision_id)
            except FileNotFoundError:
                pass

        audit_path = self._audit.path
        if original_audit is None:
            audit_path.unlink(missing_ok=True)
            return
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_bytes(original_audit)

    def _require_default_preapproval_broker_ticket(self, idea: TradeIdea) -> None:
        broker_ticket = idea.broker_ticket
        if (
            broker_ticket.venue is TicketVenue.NONE
            and broker_ticket.status is TicketStatus.NOT_CREATED
        ):
            return
        raise PreApprovalBrokerTicketError(
            "Trade ideas entering proposed must omit broker_ticket or use "
            "broker_ticket venue='none' and status='not_created' before human approval; "
            f"got venue='{broker_ticket.venue.value}', status='{broker_ticket.status.value}'",
            field="broker_ticket",
            value=broker_ticket.to_dict(),
        )

    def _review_started_at(self, decision_id: str) -> datetime | None:
        return self._review_started_at_from_events(tuple(self._audit.read_events(decision_id)))

    def _review_started_at_from_events(self, events: tuple[AuditEvent, ...]) -> datetime | None:
        if not events or events[-1].after_state is not TradeIdeaState.PROPOSED:
            return None
        for event in reversed(events):
            if (
                event.action is AuditAction.PROPOSED
                and event.after_state is TradeIdeaState.PROPOSED
            ):
                return event.timestamp
        return None

    def _append(
        self,
        idea: TradeIdea,
        *,
        action: AuditAction,
        after_state: TradeIdeaState,
        actor_type: ActorType,
        actor_id: str,
        reason: str,
        evidence: tuple[str, ...] = (),
        venue: str = "",
        external_order_id: str = "",
    ) -> None:
        self._audit.append(
            AuditEvent(
                event_id=new_event_id(),
                timestamp=self._now(),
                decision_id=idea.decision_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                before_state=self._audit.current_state(idea.decision_id),
                after_state=after_state,
                reason=reason,
                record_hash=idea.record_hash(),
                evidence=evidence,
                venue=venue,
                external_order_id=external_order_id,
            )
        )


def _validate_list_query(query: TradeIdeaListQuery) -> None:
    if query.offset < 0:
        raise ValidationError(
            "Trade idea list offset must be non-negative",
            field="offset",
            value=query.offset,
        )
    if query.limit is not None and query.limit <= 0:
        raise ValidationError(
            "Trade idea list limit must be positive",
            field="limit",
            value=query.limit,
        )
    if (
        query.min_confidence is not None
        and query.max_confidence is not None
        and _CONFIDENCE_RANK[query.min_confidence] > _CONFIDENCE_RANK[query.max_confidence]
    ):
        raise ValidationError(
            "Trade idea list min_confidence cannot be greater than max_confidence",
            field="confidence",
            value={
                "min_confidence": query.min_confidence.value,
                "max_confidence": query.max_confidence.value,
            },
        )
    _validate_query_timestamp(query.updated_since, "updated_since")
    _validate_query_timestamp(query.updated_until, "updated_until")
    if (
        query.updated_since is not None
        and query.updated_until is not None
        and query.updated_since > query.updated_until
    ):
        raise ValidationError(
            "Trade idea list updated_since cannot be after updated_until",
            field="updated_since",
            value=query.updated_since.isoformat(),
        )


def _validate_query_timestamp(value: datetime | None, field: str) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(
            f"Trade idea list {field} must include a timezone",
            field=field,
            value=value.isoformat(),
        )


def _matches_list_query(view: TradeIdeaView, query: TradeIdeaListQuery) -> bool:
    idea = view.idea
    if query.state is not None and view.state is not query.state:
        return False
    if query.instrument and idea.instrument.casefold() != query.instrument.casefold():
        return False
    if query.decision_id and idea.decision_id != query.decision_id:
        return False
    if query.direction is not None and idea.direction is not query.direction:
        return False
    confidence_rank = _CONFIDENCE_RANK[idea.confidence.label]
    if (
        query.min_confidence is not None
        and confidence_rank < _CONFIDENCE_RANK[query.min_confidence]
    ):
        return False
    if (
        query.max_confidence is not None
        and confidence_rank > _CONFIDENCE_RANK[query.max_confidence]
    ):
        return False
    updated_at = view.events[-1].timestamp
    if query.updated_since is not None and updated_at < query.updated_since:
        return False
    if query.updated_until is not None and updated_at > query.updated_until:
        return False
    return True


def _sort_list_views(
    views: list[TradeIdeaView],
    query: TradeIdeaListQuery,
) -> list[TradeIdeaView]:
    if query.sort_by is None:
        return views

    keyed: list[tuple[Any, TradeIdeaView]] = []
    missing: list[TradeIdeaView] = []
    for view in views:
        value = _list_sort_value(view, query.sort_by)
        if value is None:
            missing.append(view)
            continue
        keyed.append((value, view))
    keyed.sort(key=lambda item: item[0], reverse=query.descending)
    return [view for _, view in keyed] + missing


def _list_sort_value(
    view: TradeIdeaView,
    sort_by: TradeIdeaListSortKey,
) -> Any | None:
    idea = view.idea
    if sort_by is TradeIdeaListSortKey.DECISION_ID:
        return idea.decision_id
    if sort_by is TradeIdeaListSortKey.STATE:
        return (
            _STATE_SORT_RANK.get(view.state, 9),
            idea.time_horizon.expires_at or datetime.max.replace(tzinfo=UTC),
            idea.decision_id,
        )
    if sort_by is TradeIdeaListSortKey.INSTRUMENT:
        return (idea.instrument.casefold(), idea.decision_id)
    if sort_by is TradeIdeaListSortKey.DIRECTION:
        return (idea.direction.value, idea.decision_id)
    if sort_by is TradeIdeaListSortKey.CONFIDENCE:
        return (_CONFIDENCE_RANK[idea.confidence.label], idea.decision_id)
    if sort_by is TradeIdeaListSortKey.MAX_LOSS_PCT:
        percent = idea.max_loss.percent_of_account
        return (percent, idea.decision_id) if percent is not None else None
    if sort_by is TradeIdeaListSortKey.EXPIRES_AT:
        expires_at = idea.time_horizon.expires_at
        return (expires_at, idea.decision_id) if expires_at is not None else None
    if sort_by is TradeIdeaListSortKey.CREATED_AT:
        return (view.events[0].timestamp, idea.decision_id)
    if sort_by is TradeIdeaListSortKey.UPDATED_AT:
        return (view.events[-1].timestamp, idea.decision_id)
    return None


def _queue_expiration(
    view: TradeIdeaView,
    as_of: datetime,
    window_end: datetime,
    budget: RiskBudget,
    *,
    review_started_at: datetime | None,
) -> TradeIdeaQueueExpiration | None:
    deadlines: list[tuple[str, datetime]] = []
    horizon_expires_at = view.idea.time_horizon.expires_at
    if horizon_expires_at is not None:
        deadlines.append(("time_horizon", horizon_expires_at))
    if review_started_at is not None:
        review_deadline = review_started_at + timedelta(hours=budget.max_review_latency_hours)
        deadlines.append(("review_latency", review_deadline))

    upcoming = [
        (deadline_type, expires_at)
        for deadline_type, expires_at in deadlines
        if expires_at <= window_end
    ]
    if not upcoming:
        return None

    deadline_type, expires_at = min(upcoming, key=lambda item: (item[1], item[0]))
    return TradeIdeaQueueExpiration(
        decision_id=view.idea.decision_id,
        state=view.state,
        instrument=view.idea.instrument,
        expires_at=expires_at,
        deadline_type=deadline_type,
        seconds_until_expiry=max(0, int((expires_at - as_of).total_seconds())),
    )


def create_trade_idea_service(
    root: Path | None = None,
    *,
    now_factory: Callable[[], datetime] = _utc_now,
) -> TradeIdeaService:
    """Resolve root (arg > GPT_TRADER_IDEAS_ROOT > default) and build the service.

    The autonomy mode is deliberately not injectable: it resolves through the
    audited autonomy state log at each decision boundary
    (docs/decisions/persistent-autonomy-state.md).
    """
    return TradeIdeaService(resolve_ideas_root(root), now_factory=now_factory)


def _validate_audit_venue(venue: str) -> str:
    try:
        parsed = TicketVenue(venue)
    except ValueError as error:
        allowed = ", ".join(sorted(item.value for item in _AUDIT_VENUES))
        raise ValidationError(
            f"Unsupported trade-idea venue '{venue}'; expected one of: {allowed}",
            field="venue",
            value=venue,
        ) from error
    if parsed in _AUDIT_VENUES:
        return parsed.value
    allowed = ", ".join(sorted(item.value for item in _AUDIT_VENUES))
    raise ValidationError(
        f"Unsupported trade-idea venue '{venue}'; expected one of: {allowed}",
        field="venue",
        value=venue,
    )
