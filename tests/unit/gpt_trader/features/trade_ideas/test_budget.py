from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from filelock import FileLock

from gpt_trader.features.trade_ideas import (
    DEFAULT_RISK_BUDGET,
    ActorType,
    BudgetIntegrityError,
    BudgetLogEntry,
    RiskBudget,
    RiskBudgetLog,
)


def build_entry(budget: RiskBudget, minute: int = 0) -> BudgetLogEntry:
    return BudgetLogEntry(
        timestamp=datetime(2026, 6, 12, 9, minute, tzinfo=UTC),
        actor_type=ActorType.HUMAN,
        actor_id="rj",
        budget=budget,
    )


@pytest.fixture
def budget_log(tmp_path: Path) -> RiskBudgetLog:
    return RiskBudgetLog(tmp_path / "risk_budget.jsonl")


def test_seeded_defaults_reflect_accepted_risk_philosophy() -> None:
    assert DEFAULT_RISK_BUDGET.version == 1
    assert DEFAULT_RISK_BUDGET.max_loss_per_idea_pct == Decimal("5")
    assert DEFAULT_RISK_BUDGET.max_daily_loss_pct == Decimal("10")
    assert DEFAULT_RISK_BUDGET.gain_retention_floor_pct == Decimal("50")
    assert DEFAULT_RISK_BUDGET.sizing_capped_by_budget is True
    assert DEFAULT_RISK_BUDGET.allow_futures_leverage is False


def test_budget_round_trip() -> None:
    restored = RiskBudget.from_dict(DEFAULT_RISK_BUDGET.to_dict())

    assert restored == DEFAULT_RISK_BUDGET
    assert isinstance(restored.max_loss_per_idea_pct, Decimal)


@pytest.mark.parametrize(
    "field_name",
    [
        "max_loss_per_idea_pct",
        "max_daily_loss_pct",
        "max_open_notional_pct",
        "gain_retention_floor_pct",
    ],
)
def test_budget_rejects_negative_decimal_limits_from_dict(field_name: str) -> None:
    payload = {**DEFAULT_RISK_BUDGET.to_dict(), field_name: "-0.01"}

    with pytest.raises(ValueError, match=f"{field_name} must be non-negative"):
        RiskBudget.from_dict(payload)


@pytest.mark.parametrize(
    "field_name",
    [
        "max_concurrent_approved_tickets",
        "max_review_latency_hours",
    ],
)
def test_budget_rejects_negative_integer_limits_from_dict(field_name: str) -> None:
    payload = {**DEFAULT_RISK_BUDGET.to_dict(), field_name: "-1"}

    with pytest.raises(ValueError, match=f"{field_name} must be non-negative"):
        RiskBudget.from_dict(payload)


def test_empty_log_has_no_current_budget(budget_log: RiskBudgetLog) -> None:
    assert budget_log.current() is None
    assert budget_log.history() == []


def test_append_and_current(budget_log: RiskBudgetLog) -> None:
    budget_log.append(build_entry(DEFAULT_RISK_BUDGET))

    assert budget_log.current() == DEFAULT_RISK_BUDGET
    assert len(budget_log.history()) == 1


def test_versions_must_be_contiguous(budget_log: RiskBudgetLog) -> None:
    budget_log.append(build_entry(DEFAULT_RISK_BUDGET))
    skipped = RiskBudget.from_dict({**DEFAULT_RISK_BUDGET.to_dict(), "version": 3})

    with pytest.raises(BudgetIntegrityError):
        budget_log.append(build_entry(skipped, minute=1))


def test_malformed_line_raises_integrity_error(budget_log: RiskBudgetLog) -> None:
    budget_log.append(build_entry(DEFAULT_RISK_BUDGET))
    with budget_log.path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    with pytest.raises(BudgetIntegrityError, match="line 2 is malformed"):
        budget_log.history()


def test_malformed_decimal_raises_integrity_error(budget_log: RiskBudgetLog) -> None:
    budget_log.append(build_entry(DEFAULT_RISK_BUDGET))
    corrupted = budget_log.path.read_text(encoding="utf-8").replace(
        '"max_loss_per_idea_pct":"5"',
        '"max_loss_per_idea_pct":"bad"',
    )
    budget_log.path.write_text(corrupted, encoding="utf-8")

    with pytest.raises(BudgetIntegrityError, match="line 1 is malformed"):
        budget_log.history()


def test_invalid_utf8_raises_integrity_error(budget_log: RiskBudgetLog) -> None:
    budget_log.path.parent.mkdir(parents=True, exist_ok=True)
    budget_log.path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(BudgetIntegrityError, match="unreadable"):
        budget_log.history()


def test_version_gap_in_file_raises_integrity_error(budget_log: RiskBudgetLog) -> None:
    budget_log.append(build_entry(DEFAULT_RISK_BUDGET))
    skipped = RiskBudget.from_dict({**DEFAULT_RISK_BUDGET.to_dict(), "version": 5})
    with budget_log.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(build_entry(skipped, minute=1).to_dict()) + "\n")

    with pytest.raises(BudgetIntegrityError, match="version 5; expected 2"):
        budget_log.history()


def test_duplicated_version_in_file_raises_integrity_error(budget_log: RiskBudgetLog) -> None:
    # The artifact an unlocked concurrent append would have left behind:
    # two entries both claiming the same next version.
    budget_log.append(build_entry(DEFAULT_RISK_BUDGET))
    line = budget_log.path.read_text(encoding="utf-8")
    budget_log.path.write_text(line + line, encoding="utf-8")

    with pytest.raises(BudgetIntegrityError, match="version 1; expected 2"):
        budget_log.history()

    with pytest.raises(BudgetIntegrityError):
        budget_log.current()


def test_append_times_out_when_lock_is_held(
    budget_log: RiskBudgetLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RiskBudgetLog, "_LOCK_TIMEOUT_SECONDS", 0.05)
    budget_log.path.parent.mkdir(parents=True, exist_ok=True)
    foreign_lock = FileLock(str(budget_log.path) + ".lock")
    with foreign_lock.acquire(timeout=1):
        with pytest.raises(BudgetIntegrityError, match="budget log lock"):
            budget_log.append(build_entry(DEFAULT_RISK_BUDGET))

    assert budget_log.current() is None


def test_unusable_lock_file_raises_integrity_error(budget_log: RiskBudgetLog) -> None:
    lock_path = Path(str(budget_log.path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.mkdir()

    with pytest.raises(BudgetIntegrityError, match="lock is unusable"):
        budget_log.append(build_entry(DEFAULT_RISK_BUDGET))

    assert budget_log.current() is None


def test_append_succeeds_after_lock_is_released(budget_log: RiskBudgetLog) -> None:
    budget_log.path.parent.mkdir(parents=True, exist_ok=True)
    foreign_lock = FileLock(str(budget_log.path) + ".lock")
    with foreign_lock.acquire(timeout=1):
        pass

    budget_log.append(build_entry(DEFAULT_RISK_BUDGET))

    assert budget_log.current() == DEFAULT_RISK_BUDGET


def test_renegotiated_budget_becomes_current(budget_log: RiskBudgetLog) -> None:
    budget_log.append(build_entry(DEFAULT_RISK_BUDGET))
    widened = RiskBudget.from_dict(
        {
            **DEFAULT_RISK_BUDGET.to_dict(),
            "version": 2,
            "max_loss_per_idea_pct": "8",
            "reason": "Earned after 90 days of accurate max-loss estimates",
        }
    )

    budget_log.append(build_entry(widened, minute=1))

    assert budget_log.current() == widened
    assert [entry.budget.version for entry in budget_log.history()] == [1, 2]


def test_max_drawdown_from_peak_lever_round_trips_and_validates() -> None:
    budget = replace(
        DEFAULT_RISK_BUDGET,
        max_drawdown_from_peak_pct=Decimal("20"),
        reason="Configure the drawdown-from-peak appetite",
    )

    restored = RiskBudget.from_dict(budget.to_dict())
    assert restored.max_drawdown_from_peak_pct == Decimal("20")

    with pytest.raises(ValueError, match="max_drawdown_from_peak_pct"):
        replace(DEFAULT_RISK_BUDGET, max_drawdown_from_peak_pct=Decimal("-1"))
    with pytest.raises(ValueError, match="max_drawdown_from_peak_pct"):
        replace(DEFAULT_RISK_BUDGET, max_drawdown_from_peak_pct=Decimal("NaN"))


def test_budget_payload_without_drawdown_lever_still_loads() -> None:
    # Budget logs written before the lever existed (#1192) must keep loading;
    # an absent key means no drawdown limit is configured.
    payload = {
        key: value
        for key, value in DEFAULT_RISK_BUDGET.to_dict().items()
        if key != "max_drawdown_from_peak_pct"
    }

    restored = RiskBudget.from_dict(payload)

    assert restored.max_drawdown_from_peak_pct is None


def test_equity_buying_power_lever_round_trips_and_validates() -> None:
    budget = replace(
        DEFAULT_RISK_BUDGET,
        max_equity_buying_power_pct=Decimal("100"),
        reason="Configure cash-account buying power for equities",
    )

    restored = RiskBudget.from_dict(budget.to_dict())
    assert restored.max_equity_buying_power_pct == Decimal("100")

    with pytest.raises(ValueError, match="max_equity_buying_power_pct"):
        replace(DEFAULT_RISK_BUDGET, max_equity_buying_power_pct=Decimal("-1"))
    with pytest.raises(ValueError, match="max_equity_buying_power_pct"):
        replace(DEFAULT_RISK_BUDGET, max_equity_buying_power_pct=Decimal("NaN"))


def test_equity_buying_power_lever_is_unconfigured_by_default() -> None:
    # The seeded default keeps the lever off (#1231): configuring it changes
    # what an unclassifiable instrument does at approval time, and crypto
    # approval outcomes are pinned unchanged until an operator versions the
    # lever in (100 = cash-account fidelity).
    assert DEFAULT_RISK_BUDGET.max_equity_buying_power_pct is None


def test_budget_payload_without_buying_power_lever_still_loads() -> None:
    # Budget logs written before the lever existed (#1231) must keep loading;
    # an absent key means the buying-power dimension is not configured.
    payload = {
        key: value
        for key, value in DEFAULT_RISK_BUDGET.to_dict().items()
        if key != "max_equity_buying_power_pct"
    }

    restored = RiskBudget.from_dict(payload)

    assert restored.max_equity_buying_power_pct is None
