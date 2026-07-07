"""Continuous portfolio monitors: HWM, drawdown-from-peak, open exposure.

Implements #1192 from the accepted adopt-event-driven-execution-topology
decision: portfolio-level risk is a running monitor evaluated against the
budget envelope, not a per-cycle check. Everything here is a pure computation
over the same durable evidence the accountant and approval paths already read
— the attested-equity ledger (``accounting.compute_paper_accounting``) and the
aggregate exposure context (``ApprovalBudgetContext``) — so monitor state is
always derivable from the trail and there is no new state store to audit.

One snapshot type serves every consumer: the CLI (``gpt-trader ideas
monitors``), the operator console, and the ratchet trigger all read the same
``TradeIdeaService.portfolio_monitors()`` call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from gpt_trader.features.trade_ideas.accounting import PaperAccountingSummary
from gpt_trader.features.trade_ideas.budget import RiskBudget
from gpt_trader.features.trade_ideas.policy import ApprovalBudgetContext


def _decimal_to_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True, slots=True)
class PortfolioMonitorSnapshot:
    """Point-in-time monitor readings against the active budget envelope.

    Breach verdicts are tri-state: ``True``/``False`` when the measurement and
    limit are both available, ``None`` when the question cannot be answered
    yet (no configured limit, no attested basis, or no equity denominator) —
    an unanswerable monitor must read as "unknown", never as "healthy".
    """

    evaluated_at: datetime
    budget_version: int

    # Equity monitors (attested-equity ledger).
    current_equity: Decimal | None
    high_water_mark: Decimal | None
    drawdown_amount: Decimal | None
    drawdown_from_peak_pct: Decimal | None

    # Exposure monitors (aggregate open ideas).
    account_equity_snapshot: Decimal | None
    open_notional: Decimal
    open_notional_pct: Decimal | None
    open_notional_unavailable_count: int
    open_approved_at_risk_pct: Decimal
    open_at_risk_unavailable_count: int
    same_day_realized_loss_pct: Decimal

    # Envelope limits in force when the snapshot was taken.
    max_drawdown_from_peak_pct: Decimal | None
    max_open_notional_pct: Decimal
    max_daily_loss_pct: Decimal

    # Breach verdicts.
    drawdown_breached: bool | None
    open_notional_breached: bool | None
    daily_loss_breached: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "budget_version": self.budget_version,
            "current_equity": _decimal_to_str(self.current_equity),
            "high_water_mark": _decimal_to_str(self.high_water_mark),
            "drawdown_amount": _decimal_to_str(self.drawdown_amount),
            "drawdown_from_peak_pct": _decimal_to_str(self.drawdown_from_peak_pct),
            "account_equity_snapshot": _decimal_to_str(self.account_equity_snapshot),
            "open_notional": str(self.open_notional),
            "open_notional_pct": _decimal_to_str(self.open_notional_pct),
            "open_notional_unavailable_count": self.open_notional_unavailable_count,
            "open_approved_at_risk_pct": str(self.open_approved_at_risk_pct),
            "open_at_risk_unavailable_count": self.open_at_risk_unavailable_count,
            "same_day_realized_loss_pct": str(self.same_day_realized_loss_pct),
            "max_drawdown_from_peak_pct": _decimal_to_str(self.max_drawdown_from_peak_pct),
            "max_open_notional_pct": str(self.max_open_notional_pct),
            "max_daily_loss_pct": str(self.max_daily_loss_pct),
            "drawdown_breached": self.drawdown_breached,
            "open_notional_breached": self.open_notional_breached,
            "daily_loss_breached": self.daily_loss_breached,
        }


def compute_portfolio_monitors(
    *,
    now: datetime,
    budget: RiskBudget,
    accounting: PaperAccountingSummary,
    exposure: ApprovalBudgetContext,
) -> PortfolioMonitorSnapshot:
    """Combine the equity ledger and exposure context into one monitor snapshot."""
    drawdown_breached: bool | None = None
    if budget.max_drawdown_from_peak_pct is not None and accounting.drawdown_percent is not None:
        drawdown_breached = accounting.drawdown_percent > budget.max_drawdown_from_peak_pct

    account_equity = exposure.account_equity_snapshot
    open_notional_pct: Decimal | None = None
    open_notional_breached: bool | None = None
    if account_equity is not None and account_equity > 0:
        open_notional_pct = abs(exposure.open_notional) / account_equity * Decimal("100")
        open_notional_breached = open_notional_pct > budget.max_open_notional_pct

    daily_loss_breached = exposure.same_day_realized_loss_pct > budget.max_daily_loss_pct

    return PortfolioMonitorSnapshot(
        evaluated_at=now,
        budget_version=budget.version,
        current_equity=accounting.current_equity,
        high_water_mark=accounting.high_water_mark,
        drawdown_amount=accounting.drawdown_amount,
        drawdown_from_peak_pct=accounting.drawdown_percent,
        account_equity_snapshot=account_equity,
        open_notional=exposure.open_notional,
        open_notional_pct=open_notional_pct,
        open_notional_unavailable_count=exposure.open_notional_unavailable_count,
        open_approved_at_risk_pct=exposure.open_approved_at_risk_pct,
        open_at_risk_unavailable_count=exposure.open_at_risk_unavailable_count,
        same_day_realized_loss_pct=exposure.same_day_realized_loss_pct,
        max_drawdown_from_peak_pct=budget.max_drawdown_from_peak_pct,
        max_open_notional_pct=budget.max_open_notional_pct,
        max_daily_loss_pct=budget.max_daily_loss_pct,
        drawdown_breached=drawdown_breached,
        open_notional_breached=open_notional_breached,
        daily_loss_breached=daily_loss_breached,
    )


__all__ = [
    "PortfolioMonitorSnapshot",
    "compute_portfolio_monitors",
]
