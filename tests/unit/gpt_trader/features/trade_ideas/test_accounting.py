"""Paper accounting ledger: attestations set the level, closeouts move it."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gpt_trader.features.trade_ideas.accounting import compute_paper_accounting
from gpt_trader.features.trade_ideas.audit import ActorType
from gpt_trader.features.trade_ideas.budget import DEFAULT_RISK_BUDGET, BudgetLogEntry
from gpt_trader.features.trade_ideas.closeout import (
    CloseoutAttribution,
    CloseoutResolution,
    MaxLossSnapshot,
)

_START = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _budget_entry(
    version: int,
    *,
    at: datetime,
    equity: Decimal | None,
    actor_id: str = "rj",
) -> BudgetLogEntry:
    budget = replace(
        DEFAULT_RISK_BUDGET,
        version=version,
        account_equity=equity,
        reason="test budget version",
    )
    return BudgetLogEntry(
        timestamp=at,
        actor_type=ActorType.HUMAN,
        actor_id=actor_id,
        budget=budget,
    )


def _closeout(
    decision_id: str,
    *,
    at: datetime,
    amount: Decimal | None,
    terminal_event_id: str = "event-1",
) -> CloseoutAttribution:
    return CloseoutAttribution(
        decision_id=decision_id,
        timestamp=at,
        actor_type="human",
        actor_id="rj",
        terminal_event_id=terminal_event_id,
        record_hash="hash-1",
        resolution=CloseoutResolution.THESIS_TARGET,
        max_loss=MaxLossSnapshot(amount=Decimal("50")),
        realized_profit_loss_amount=amount,
        realized_profit_loss_unavailable_reason=(
            "" if amount is not None else "fill evidence missing"
        ),
    )


def test_empty_inputs_yield_no_basis() -> None:
    summary = compute_paper_accounting([], [])

    assert summary.attestation is None
    assert summary.current_equity is None
    assert summary.high_water_mark is None
    assert summary.drawdown_amount is None
    assert summary.realized_profit_loss_total == Decimal("0")
    assert summary.realized_profit_loss_since_attestation is None
    assert summary.closeout_count == 0


def test_closeouts_accrue_onto_attested_equity() -> None:
    entries = [_budget_entry(1, at=_START, equity=Decimal("1000"))]
    closeouts = [
        _closeout("trade-1", at=_START + timedelta(hours=1), amount=Decimal("150")),
        _closeout("trade-2", at=_START + timedelta(hours=2), amount=Decimal("-90")),
    ]

    summary = compute_paper_accounting(entries, closeouts)

    assert summary.current_equity == Decimal("1060")
    assert summary.high_water_mark == Decimal("1150")
    assert summary.drawdown_amount == Decimal("90")
    assert summary.drawdown_percent is not None
    assert summary.drawdown_percent.quantize(Decimal("0.01")) == Decimal("7.83")
    assert summary.realized_profit_loss_total == Decimal("60")
    assert summary.realized_profit_loss_since_attestation == Decimal("60")
    assert summary.closeout_count == 2
    assert summary.closeout_amount_unavailable_count == 0


def test_lever_change_carrying_equity_forward_is_not_an_attestation() -> None:
    entries = [
        _budget_entry(1, at=_START, equity=Decimal("1000")),
        # A later version with the same equity is a lever change, not a fresh
        # measurement; the ledger and P&L accumulator must not reset.
        _budget_entry(2, at=_START + timedelta(hours=3), equity=Decimal("1000")),
    ]
    closeouts = [_closeout("trade-1", at=_START + timedelta(hours=1), amount=Decimal("-40"))]

    summary = compute_paper_accounting(entries, closeouts)

    assert summary.attestation is not None
    assert summary.attestation.budget_version == 1
    assert summary.current_equity == Decimal("960")
    assert summary.realized_profit_loss_since_attestation == Decimal("-40")


def test_reattestation_resets_level_but_keeps_historical_peak() -> None:
    entries = [
        _budget_entry(1, at=_START, equity=Decimal("2000")),
        _budget_entry(2, at=_START + timedelta(days=1), equity=Decimal("1000")),
    ]
    closeouts = [_closeout("trade-1", at=_START + timedelta(days=1, hours=1), amount=Decimal("25"))]

    summary = compute_paper_accounting(entries, closeouts)

    assert summary.attestation is not None
    assert summary.attestation.equity == Decimal("1000")
    assert summary.current_equity == Decimal("1025")
    assert summary.high_water_mark == Decimal("2000")
    assert summary.drawdown_amount == Decimal("975")
    assert summary.realized_profit_loss_since_attestation == Decimal("25")


def test_unavailable_amounts_are_counted_but_do_not_move_the_ledger() -> None:
    entries = [_budget_entry(1, at=_START, equity=Decimal("1000"))]
    closeouts = [
        _closeout("trade-1", at=_START + timedelta(hours=1), amount=None),
        _closeout("trade-2", at=_START + timedelta(hours=2), amount=Decimal("10")),
    ]

    summary = compute_paper_accounting(entries, closeouts)

    assert summary.current_equity == Decimal("1010")
    assert summary.closeout_count == 2
    assert summary.closeout_amount_unavailable_count == 1


def test_delayed_attribution_folds_at_terminal_time_not_entry_time() -> None:
    # Trade ends at T1, operator attests equity at T2 (which already includes
    # that trade's P&L), attribution is entered at T3. Folding at terminal
    # time puts the closeout before the attestation instead of re-applying
    # its P&L on top of the attested level.
    entries = [
        _budget_entry(1, at=_START, equity=Decimal("1000")),
        _budget_entry(2, at=_START + timedelta(hours=2), equity=Decimal("1200")),
    ]
    closeouts = [
        _closeout(
            "trade-1",
            at=_START + timedelta(hours=3),  # attribution entered after the attestation
            amount=Decimal("200"),
            terminal_event_id="terminal-1",
        )
    ]

    summary = compute_paper_accounting(
        entries,
        closeouts,
        terminal_times={"terminal-1": _START + timedelta(hours=1)},
    )

    assert summary.current_equity == Decimal("1200")  # not double-counted to 1400
    assert summary.high_water_mark == Decimal("1200")
    assert summary.realized_profit_loss_since_attestation == Decimal("0")
    assert summary.realized_profit_loss_total == Decimal("200")


def test_closeout_before_first_attestation_counts_toward_total_only() -> None:
    entries = [_budget_entry(1, at=_START + timedelta(days=1), equity=Decimal("1000"))]
    closeouts = [_closeout("trade-1", at=_START, amount=Decimal("75"))]

    summary = compute_paper_accounting(entries, closeouts)

    assert summary.current_equity == Decimal("1000")
    assert summary.realized_profit_loss_total == Decimal("75")
    assert summary.realized_profit_loss_since_attestation == Decimal("0")
