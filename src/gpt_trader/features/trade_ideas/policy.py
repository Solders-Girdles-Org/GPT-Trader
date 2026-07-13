"""Approval policy: encodes the autonomy mode as enforceable checks.

This module is the seam where autonomy is handed over. Moving up the ladder
(human approval -> bounded autonomy) means changing policy data and rules
here — never the service plumbing or the audit trail. In the seeded default
mode (``human_approved_execution``), only a human actor can move an idea to
``approved``, and approvals must clear the eligibility gate and the current
risk budget. In ``bounded_autonomy``, a system actor may approve too — the
Stage 2 exception scoped by
docs/decisions/stage2-auto-approval-workflow.md — subject to the identical
checks; AI and venue actors are always refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from gpt_trader.core.instruments import AssetClass, InstrumentParseError
from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import ActorType
from gpt_trader.features.trade_ideas.budget import RiskBudget
from gpt_trader.features.trade_ideas.eligibility import (
    INVARIANT_ELIGIBILITY_PREFIX,
    MODE_DEPENDENT_ELIGIBILITY_PREFIX,
    evaluate_eligibility,
)
from gpt_trader.features.trade_ideas.models import (
    AutonomyMode,
    ProductType,
    TradeDirection,
    TradeIdea,
)


class PolicyViolationError(ValidationError):
    """Raised when an action violates the active approval policy."""

    def __init__(self, message: str, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.violations = violations or []


BOUNDED_AUTONOMY_APPROVAL_ACTORS = frozenset({ActorType.HUMAN, ActorType.SYSTEM})
BOUNDED_AUTONOMY_ACTOR_APPROVAL_VIOLATION = (
    "Autonomy mode 'bounded_autonomy' permits only human or system approvals "
    "inside the budget envelope "
    "(docs/decisions/stage2-auto-approval-workflow.md)"
)
BOUNDED_AUTONOMY_NON_HUMAN_BUDGET_CHANGE_VIOLATION = (
    "Autonomy mode 'bounded_autonomy' does not permit non-human budget changes "
    "until a budget meta-envelope is modeled or a later decision packet scopes "
    "a narrower exception"
)


@dataclass(frozen=True, slots=True)
class ApprovalBudgetContext:
    """Aggregate budget exposure visible at an approval decision."""

    same_day_realized_loss_pct: Decimal = Decimal("0")
    same_day_realized_loss_unavailable_count: int = 0
    daily_loss_session_dates: tuple[str, ...] = ()
    open_approved_at_risk_pct: Decimal = Decimal("0")
    open_at_risk_unavailable_count: int = 0
    open_notional: Decimal = Decimal("0")
    open_notional_unavailable_count: int = 0
    account_equity_snapshot: Decimal | None = None
    # Cash-account buying-power inputs (#1231), aggregated over equity-asset-
    # class instruments only: open exposure that consumed settled cash, plus
    # sale proceeds still inside their settlement window (T+1) and therefore
    # not yet spendable. Unavailable counts cover open ideas and closeouts
    # whose equity exposure or proceeds cannot be verified — including
    # instruments that fail classification.
    open_equity_notional: Decimal = Decimal("0")
    open_equity_notional_unavailable_count: int = 0
    unsettled_equity_proceeds: Decimal = Decimal("0")
    unsettled_equity_proceeds_unavailable_count: int = 0


def _decimal(value: Decimal | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _equity_buying_power_violations(
    idea: TradeIdea,
    budget: RiskBudget,
    context: ApprovalBudgetContext,
) -> list[str]:
    """Cash-account buying-power check for equity candidates (#1231).

    Runs only when ``max_equity_buying_power_pct`` is configured on the
    budget. Crypto-spot candidates settle immediately and are never checked
    here — ``max_open_notional_pct`` remains their complete story — so
    configuring the lever provably cannot change a crypto approval outcome.
    Every input that cannot be verified (unclassifiable instrument, missing
    candidate notional, unverifiable open equity exposure or unsettled
    proceeds, missing or non-positive attested equity) refuses the approval
    loudly rather than passing silently.
    """
    if budget.max_equity_buying_power_pct is None:
        return []
    try:
        instrument = idea.instrument_info
    except InstrumentParseError as error:
        return [
            "instrument cannot be classified to verify max_equity_buying_power_pct "
            f"budget exposure: {error}"
        ]
    if instrument.asset_class is not AssetClass.EQUITY:
        return []
    violations: list[str] = []
    if context.open_equity_notional_unavailable_count:
        violations.append(
            "open budget exposure includes "
            f"{context.open_equity_notional_unavailable_count} idea(s) whose equity "
            "notional cannot be verified; max_equity_buying_power_pct budget "
            "exposure cannot be verified"
        )
    if context.unsettled_equity_proceeds_unavailable_count:
        violations.append(
            "unsettled equity closeouts include "
            f"{context.unsettled_equity_proceeds_unavailable_count} closeout(s) whose "
            "sale proceeds cannot be verified; max_equity_buying_power_pct budget "
            "exposure cannot be verified"
        )
    candidate_notional = idea.sizing_recommendation.notional
    if candidate_notional is None:
        violations.append(
            "sizing_recommendation.notional is required to verify "
            "max_equity_buying_power_pct budget exposure"
        )
        return violations
    projected_usage = (
        abs(context.open_equity_notional)
        + abs(candidate_notional)
        + abs(context.unsettled_equity_proceeds)
    )
    if projected_usage <= 0:
        return violations
    account_equity = context.account_equity_snapshot
    if account_equity is None:
        violations.append(
            "account_equity_snapshot is required to verify "
            "max_equity_buying_power_pct budget exposure"
        )
        return violations
    if account_equity <= 0:
        violations.append(
            "account_equity_snapshot must be positive to verify "
            "max_equity_buying_power_pct budget exposure; "
            f"got {_format_decimal(account_equity)}"
        )
        return violations
    projected_usage_pct = projected_usage / account_equity * _decimal(100)
    if projected_usage_pct > budget.max_equity_buying_power_pct:
        violations.append(
            "max_equity_buying_power_pct budget breached: projected equity "
            f"buying-power usage {_format_decimal(projected_usage_pct)}% exceeds "
            f"limit {_format_decimal(budget.max_equity_buying_power_pct)}% "
            f"(open_equity_notional={_format_decimal(abs(context.open_equity_notional))}, "
            f"candidate_notional={_format_decimal(abs(candidate_notional))}, "
            "unsettled_equity_proceeds="
            f"{_format_decimal(abs(context.unsettled_equity_proceeds))}, "
            f"account_equity_snapshot={_format_decimal(account_equity)})"
        )
    return violations


class ApprovalPolicy:
    """Checks workflow actions against the active autonomy mode and budget."""

    def __init__(self, autonomy_mode: AutonomyMode = AutonomyMode.HUMAN_APPROVED_EXECUTION) -> None:
        self._autonomy_mode = autonomy_mode

    @property
    def autonomy_mode(self) -> AutonomyMode:
        return self._autonomy_mode

    def approval_violations(
        self,
        idea: TradeIdea,
        actor_type: ActorType,
        budget: RiskBudget,
        open_approved_count: int,
        now: datetime,
        review_started_at: datetime | None = None,
        review_deadline: datetime | None = None,
        budget_context: ApprovalBudgetContext | None = None,
    ) -> list[str]:
        """Return every reason this approval must be refused; empty means allowed."""
        violations: list[str] = []
        has_budget_context = budget_context is not None
        budget_context = budget_context or ApprovalBudgetContext()

        if self._autonomy_mode is AutonomyMode.RESEARCH_ONLY:
            violations.append("Autonomy mode 'research_only' does not permit approvals")
        elif self._autonomy_mode is AutonomyMode.HUMAN_APPROVED_EXECUTION:
            if actor_type is not ActorType.HUMAN:
                violations.append(
                    "Autonomy mode 'human_approved_execution' requires a human approver; "
                    f"got actor_type '{actor_type.value}'"
                )
        elif (
            self._autonomy_mode is AutonomyMode.BOUNDED_AUTONOMY
            and actor_type not in BOUNDED_AUTONOMY_APPROVAL_ACTORS
        ):
            violations.append(
                BOUNDED_AUTONOMY_ACTOR_APPROVAL_VIOLATION + f"; got actor_type '{actor_type.value}'"
            )

        violations.extend(
            INVARIANT_ELIGIBILITY_PREFIX + reason for reason in evaluate_eligibility(idea)
        )

        if has_budget_context and budget_context.same_day_realized_loss_unavailable_count:
            violations.append(
                "same-day closeout budget exposure includes "
                f"{budget_context.same_day_realized_loss_unavailable_count} closeout(s) "
                "without realized profit/loss; max_daily_loss_pct budget exposure "
                "cannot be verified"
            )
        if has_budget_context and budget_context.open_at_risk_unavailable_count:
            violations.append(
                "open budget exposure includes "
                f"{budget_context.open_at_risk_unavailable_count} idea(s) without "
                "max_loss.percent_of_account; max_daily_loss_pct budget exposure "
                "cannot be verified"
            )

        percent = idea.max_loss.percent_of_account
        if percent is None:
            violations.append(
                "max_loss.percent_of_account is required to verify the idea against the budget"
            )
        elif percent > budget.max_loss_per_idea_pct:
            violations.append(
                f"max_loss {percent}% exceeds budget cap of "
                f"{budget.max_loss_per_idea_pct}% per idea"
            )
        else:
            projected_daily_loss_pct = (
                budget_context.same_day_realized_loss_pct
                + budget_context.open_approved_at_risk_pct
                + percent
            )
            if projected_daily_loss_pct > budget.max_daily_loss_pct:
                violations.append(
                    "max_daily_loss_pct budget breached: projected daily loss exposure "
                    f"{_format_decimal(projected_daily_loss_pct)}% exceeds limit "
                    f"{_format_decimal(budget.max_daily_loss_pct)}% "
                    f"(same_day_realized_loss_pct="
                    f"{_format_decimal(budget_context.same_day_realized_loss_pct)}%, "
                    f"open_approved_at_risk_pct="
                    f"{_format_decimal(budget_context.open_approved_at_risk_pct)}%, "
                    f"candidate_max_loss_pct={_format_decimal(percent)}%)"
                )

        if has_budget_context:
            if budget_context.open_notional_unavailable_count:
                violations.append(
                    "open budget exposure includes "
                    f"{budget_context.open_notional_unavailable_count} idea(s) without "
                    "sizing_recommendation.notional; max_open_notional_pct budget "
                    "exposure cannot be verified"
                )
            candidate_notional = idea.sizing_recommendation.notional
            if candidate_notional is None:
                violations.append(
                    "sizing_recommendation.notional is required to verify "
                    "max_open_notional_pct budget exposure"
                )
            else:
                projected_notional = abs(budget_context.open_notional) + abs(candidate_notional)
                account_equity = budget_context.account_equity_snapshot
                if projected_notional > 0:
                    if account_equity is None:
                        violations.append(
                            "account_equity_snapshot is required to verify "
                            "max_open_notional_pct budget exposure"
                        )
                    elif account_equity <= 0:
                        violations.append(
                            "account_equity_snapshot must be positive to verify "
                            "max_open_notional_pct budget exposure; "
                            f"got {_format_decimal(account_equity)}"
                        )
                    else:
                        projected_open_notional_pct = (
                            projected_notional / account_equity * _decimal(100)
                        )
                        if projected_open_notional_pct > budget.max_open_notional_pct:
                            violations.append(
                                "max_open_notional_pct budget breached: projected open notional "
                                f"{_format_decimal(projected_open_notional_pct)}% exceeds limit "
                                f"{_format_decimal(budget.max_open_notional_pct)}% "
                                f"(projected_open_notional={_format_decimal(projected_notional)}, "
                                f"account_equity_snapshot={_format_decimal(account_equity)})"
                            )
            violations.extend(_equity_buying_power_violations(idea, budget, budget_context))

        if idea.product_type is ProductType.FUTURES and not budget.allow_futures_leverage:
            violations.append(
                "product_type futures requires risk budget allow_futures_leverage=true"
            )

        if idea.direction is TradeDirection.SHORT and not budget.allow_naked_shorts:
            violations.append("direction short requires risk budget allow_naked_shorts=true")

        if open_approved_count >= budget.max_concurrent_approved_tickets:
            violations.append(
                f"{open_approved_count} tickets already approved; budget allows "
                f"{budget.max_concurrent_approved_tickets} concurrent approved tickets"
            )

        expires_at = idea.time_horizon.expires_at
        if expires_at is not None and expires_at <= now:
            violations.append(f"Idea expired at {expires_at.isoformat()}; approve nothing stale")

        review_latency_violation = self.review_latency_violation(
            review_started_at=review_started_at,
            budget=budget,
            now=now,
            review_deadline=review_deadline,
        )
        if review_latency_violation is not None:
            violations.append(review_latency_violation)

        return violations

    @property
    def review_latency_applies(self) -> bool:
        """True when human-review-latency survivability constrains this mode.

        Mode-dependent eligibility (#1190): review latency exists only because
        a human review loop is in the decision path. Under ``bounded_autonomy``
        the horizon floor comes from measured capability, not human latency,
        so the constraint does not apply. Every other mode — including the
        fail-closed ``research_only`` — keeps it, conservatively.
        """
        return self._autonomy_mode is not AutonomyMode.BOUNDED_AUTONOMY

    def review_latency_violation(
        self,
        *,
        review_started_at: datetime | None,
        budget: RiskBudget,
        now: datetime,
        review_deadline: datetime | None = None,
    ) -> str | None:
        """Return a violation when the active review window has elapsed.

        Mode-dependent: returns ``None`` unconditionally when
        ``review_latency_applies`` is false for the policy's autonomy mode.
        """
        if not self.review_latency_applies:
            return None
        if review_started_at is None:
            return None
        effective_deadline = review_deadline or (
            review_started_at + timedelta(hours=budget.max_review_latency_hours)
        )
        if effective_deadline > now:
            return None
        return (
            MODE_DEPENDENT_ELIGIBILITY_PREFIX
            + f"Idea review deadline expired at {effective_deadline.isoformat()} "
            f"after max_review_latency_hours={budget.max_review_latency_hours}"
        )

    def budget_change_violations(self, actor_type: ActorType) -> list[str]:
        """Budget renegotiation rules for the current autonomy mode.

        Agents may *propose* budget changes at any stage; until a budget
        meta-envelope is modeled, only a human can enact one.
        """
        if self._autonomy_mode is AutonomyMode.BOUNDED_AUTONOMY:
            if actor_type is ActorType.HUMAN:
                return []
            return [
                BOUNDED_AUTONOMY_NON_HUMAN_BUDGET_CHANGE_VIOLATION
                + f"; got actor_type '{actor_type.value}'"
            ]
        if actor_type is not ActorType.HUMAN:
            return [
                f"Autonomy mode '{self._autonomy_mode.value}' requires a human to enact "
                f"budget changes; got actor_type '{actor_type.value}'"
            ]
        return []
