"""Continuous portfolio monitors: snapshot verdicts against the envelope (#1192)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from gpt_trader.features.trade_ideas.accounting import compute_paper_accounting
from gpt_trader.features.trade_ideas.audit import ActorType
from gpt_trader.features.trade_ideas.budget import DEFAULT_RISK_BUDGET, BudgetLogEntry
from gpt_trader.features.trade_ideas.closeout import (
    CloseoutAttribution,
    CloseoutResolution,
    MaxLossSnapshot,
)
from gpt_trader.features.trade_ideas.monitors import compute_portfolio_monitors
from gpt_trader.features.trade_ideas.policy import ApprovalBudgetContext

_NOW = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)


def _budget(**overrides):
    return replace(DEFAULT_RISK_BUDGET, **overrides)


def _accounting(entries=(), closeouts=()):
    return compute_paper_accounting(entries, closeouts)


def _budget_entry(version: int, *, at: datetime, equity: Decimal) -> BudgetLogEntry:
    return BudgetLogEntry(
        timestamp=at,
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        budget=replace(
            DEFAULT_RISK_BUDGET,
            version=version,
            account_equity=equity,
            reason="test budget version",
        ),
    )


def _closeout(decision_id: str, *, at: datetime, amount: Decimal) -> CloseoutAttribution:
    return CloseoutAttribution(
        decision_id=decision_id,
        timestamp=at,
        actor_type="human",
        actor_id="rj",
        terminal_event_id="event-1",
        record_hash="hash-1",
        resolution=CloseoutResolution.INVALIDATION,
        max_loss=MaxLossSnapshot(amount=Decimal("100")),
        realized_profit_loss_amount=amount,
    )


def test_unconfigured_and_unmeasurable_monitors_read_unknown() -> None:
    snapshot = compute_portfolio_monitors(
        now=_NOW,
        budget=_budget(),
        accounting=_accounting(),
        exposure=ApprovalBudgetContext(),
    )

    assert snapshot.evaluated_at == _NOW
    assert snapshot.current_equity is None
    assert snapshot.high_water_mark is None
    assert snapshot.drawdown_from_peak_pct is None
    # No limit configured and no attested basis: unknown, never "healthy".
    assert snapshot.drawdown_breached is None
    # No attested equity denominator: open-notional verdict is unknown too.
    assert snapshot.open_notional_pct is None
    assert snapshot.open_notional_breached is None
    assert snapshot.daily_loss_breached is False


def test_drawdown_breach_verdict_reads_limit_and_ledger() -> None:
    entries = [_budget_entry(1, at=_NOW, equity=Decimal("1000"))]
    closeouts = [_closeout("trade-1", at=_NOW, amount=Decimal("-80"))]
    accounting = _accounting(entries, closeouts)
    assert accounting.drawdown_percent == Decimal("8")

    within = compute_portfolio_monitors(
        now=_NOW,
        budget=_budget(max_drawdown_from_peak_pct=Decimal("10")),
        accounting=accounting,
        exposure=ApprovalBudgetContext(),
    )
    breached = compute_portfolio_monitors(
        now=_NOW,
        budget=_budget(max_drawdown_from_peak_pct=Decimal("5")),
        accounting=accounting,
        exposure=ApprovalBudgetContext(),
    )

    assert within.drawdown_breached is False
    assert breached.drawdown_breached is True
    assert breached.drawdown_from_peak_pct == Decimal("8")
    assert breached.max_drawdown_from_peak_pct == Decimal("5")


def test_exposure_verdicts_use_attested_denominator() -> None:
    exposure = ApprovalBudgetContext(
        open_notional=Decimal("600"),
        account_equity_snapshot=Decimal("1000"),
        same_day_realized_loss_pct=Decimal("12"),
    )
    snapshot = compute_portfolio_monitors(
        now=_NOW,
        budget=_budget(
            max_open_notional_pct=Decimal("50"),
            max_daily_loss_pct=Decimal("10"),
        ),
        accounting=_accounting(),
        exposure=exposure,
    )

    assert snapshot.open_notional_pct == Decimal("60")
    assert snapshot.open_notional_breached is True
    assert snapshot.daily_loss_breached is True


def test_to_dict_is_json_ready() -> None:
    snapshot = compute_portfolio_monitors(
        now=_NOW,
        budget=_budget(max_drawdown_from_peak_pct=Decimal("20")),
        accounting=_accounting(),
        exposure=ApprovalBudgetContext(open_notional=Decimal("100")),
    )
    payload = snapshot.to_dict()

    assert payload["evaluated_at"] == _NOW.isoformat()
    assert payload["max_drawdown_from_peak_pct"] == "20"
    assert payload["open_notional"] == "100"
    assert payload["drawdown_breached"] is None
    assert payload["current_equity"] is None
