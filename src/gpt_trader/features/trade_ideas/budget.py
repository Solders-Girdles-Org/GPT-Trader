"""Versioned, renegotiable risk budget for trade-idea workflows.

The budget is the lever-handover mechanism from the accepted direction: limits
are explicit data that agents can propose changes to through the same audited
workflow as everything else. They are never silently removed — each version is
appended to its own log with the actor and rationale that produced it.

Seeded defaults reflect the owner's accepted risk philosophy
(docs/DIRECTION.md): principal is fully at risk, so per-idea and daily
caps are aggressive; realized gains are not principal, so a gain-retention
floor defends a share of peak gains once the account is above its
high-water mark.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.audit import ActorType


def _require_finite_decimal(value: Decimal, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _require_non_negative_decimal(value: Decimal, field: str) -> None:
    if value < 0:
        raise ValueError(f"{field} must be non-negative")


def _require_non_negative_int(value: int, field: str) -> None:
    if value < 0:
        raise ValueError(f"{field} must be non-negative")


class BudgetIntegrityError(ValidationError):
    """Raised when the budget log is malformed, contended, or an append breaks sequencing."""


def _require_boolean(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise BudgetIntegrityError(
        f"{field} must be a JSON boolean",
        field=field,
        value=value,
    )


@dataclass(frozen=True, slots=True)
class RiskBudget:
    """One immutable version of the risk budget."""

    version: int
    max_loss_per_idea_pct: Decimal
    max_daily_loss_pct: Decimal
    max_open_notional_pct: Decimal
    max_concurrent_approved_tickets: int
    max_review_latency_hours: int
    sizing_capped_by_budget: bool
    gain_retention_floor_pct: Decimal
    allow_futures_leverage: bool
    allow_naked_shorts: bool
    reason: str
    # Operator-attested account equity used as the denominator for
    # max_open_notional_pct. Deliberately human-set (versioned in this log)
    # rather than inferred from idea records, so a candidate idea can never
    # supply its own denominator. None means notional exposure cannot be
    # verified from the budget alone.
    account_equity: Decimal | None = None
    # Drawdown-from-peak appetite for the continuous portfolio monitors
    # (#1192): breach ratchets autonomy down through the audited path.
    # None means no drawdown limit is configured — nothing to breach.
    max_drawdown_from_peak_pct: Decimal | None = None
    # Cash-account buying-power cap for equity-asset-class instruments
    # (#1231), in percent points of the attested account_equity: projected
    # open equity notional plus the candidate plus same-settlement-window
    # (T+1) unsettled equity sale proceeds may not exceed this share of the
    # attested equity. Crypto spot settles immediately and is never checked
    # against it — max_open_notional_pct remains its complete story. None
    # means the buying-power dimension is not configured; the existing
    # notional check still applies unchanged.
    max_equity_buying_power_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if self.account_equity is not None:
            _require_finite_decimal(self.account_equity, "account_equity")
            if self.account_equity <= 0:
                raise ValueError("account_equity must be positive")
        if self.max_drawdown_from_peak_pct is not None:
            _require_finite_decimal(self.max_drawdown_from_peak_pct, "max_drawdown_from_peak_pct")
            _require_non_negative_decimal(
                self.max_drawdown_from_peak_pct, "max_drawdown_from_peak_pct"
            )
        if self.max_equity_buying_power_pct is not None:
            _require_finite_decimal(self.max_equity_buying_power_pct, "max_equity_buying_power_pct")
            _require_non_negative_decimal(
                self.max_equity_buying_power_pct, "max_equity_buying_power_pct"
            )
        _require_finite_decimal(self.max_loss_per_idea_pct, "max_loss_per_idea_pct")
        _require_finite_decimal(self.max_daily_loss_pct, "max_daily_loss_pct")
        _require_finite_decimal(self.max_open_notional_pct, "max_open_notional_pct")
        _require_finite_decimal(self.gain_retention_floor_pct, "gain_retention_floor_pct")
        _require_non_negative_decimal(self.max_loss_per_idea_pct, "max_loss_per_idea_pct")
        _require_non_negative_decimal(self.max_daily_loss_pct, "max_daily_loss_pct")
        _require_non_negative_decimal(self.max_open_notional_pct, "max_open_notional_pct")
        _require_non_negative_decimal(self.gain_retention_floor_pct, "gain_retention_floor_pct")
        _require_non_negative_int(
            self.max_concurrent_approved_tickets, "max_concurrent_approved_tickets"
        )
        _require_non_negative_int(self.max_review_latency_hours, "max_review_latency_hours")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "max_loss_per_idea_pct": str(self.max_loss_per_idea_pct),
            "max_daily_loss_pct": str(self.max_daily_loss_pct),
            "max_open_notional_pct": str(self.max_open_notional_pct),
            "max_concurrent_approved_tickets": self.max_concurrent_approved_tickets,
            "max_review_latency_hours": self.max_review_latency_hours,
            "sizing_capped_by_budget": self.sizing_capped_by_budget,
            "gain_retention_floor_pct": str(self.gain_retention_floor_pct),
            "allow_futures_leverage": self.allow_futures_leverage,
            "allow_naked_shorts": self.allow_naked_shorts,
            "reason": self.reason,
            "account_equity": str(self.account_equity) if self.account_equity is not None else None,
            "max_drawdown_from_peak_pct": (
                str(self.max_drawdown_from_peak_pct)
                if self.max_drawdown_from_peak_pct is not None
                else None
            ),
            "max_equity_buying_power_pct": (
                str(self.max_equity_buying_power_pct)
                if self.max_equity_buying_power_pct is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RiskBudget:
        return cls(
            version=int(payload["version"]),
            max_loss_per_idea_pct=Decimal(payload["max_loss_per_idea_pct"]),
            max_daily_loss_pct=Decimal(payload["max_daily_loss_pct"]),
            max_open_notional_pct=Decimal(payload["max_open_notional_pct"]),
            max_concurrent_approved_tickets=int(payload["max_concurrent_approved_tickets"]),
            max_review_latency_hours=int(payload["max_review_latency_hours"]),
            sizing_capped_by_budget=_require_boolean(
                payload["sizing_capped_by_budget"], "sizing_capped_by_budget"
            ),
            gain_retention_floor_pct=Decimal(payload["gain_retention_floor_pct"]),
            allow_futures_leverage=_require_boolean(
                payload["allow_futures_leverage"], "allow_futures_leverage"
            ),
            allow_naked_shorts=_require_boolean(
                payload["allow_naked_shorts"], "allow_naked_shorts"
            ),
            reason=payload.get("reason", ""),
            account_equity=(
                Decimal(str(payload["account_equity"]))
                if payload.get("account_equity") is not None
                else None
            ),
            # Optional lever added after logs already existed (#1192); absent
            # in older entries means no drawdown limit was configured.
            max_drawdown_from_peak_pct=(
                Decimal(str(payload["max_drawdown_from_peak_pct"]))
                if payload.get("max_drawdown_from_peak_pct") is not None
                else None
            ),
            # Optional lever added after logs already existed (#1231); absent
            # in older entries means no buying-power cap was configured.
            max_equity_buying_power_pct=(
                Decimal(str(payload["max_equity_buying_power_pct"]))
                if payload.get("max_equity_buying_power_pct") is not None
                else None
            ),
        )


DEFAULT_RISK_BUDGET = RiskBudget(
    version=1,
    max_loss_per_idea_pct=Decimal("5"),
    max_daily_loss_pct=Decimal("10"),
    max_open_notional_pct=Decimal("100"),
    max_concurrent_approved_tickets=5,
    max_review_latency_hours=72,
    sizing_capped_by_budget=True,
    gain_retention_floor_pct=Decimal("50"),
    allow_futures_leverage=False,
    allow_naked_shorts=False,
    reason=(
        "Seeded aggressive defaults accepted 2026-06-11: principal fully at risk, "
        "gain-retention floor defends 50% of peak gains above the high-water mark"
    ),
    # max_equity_buying_power_pct stays unconfigured in the seeded default:
    # configuring it here would newly refuse ideas whose instrument cannot be
    # classified (test-pinned: crypto approval outcomes are unchanged until
    # an operator versions the lever in — 100 = cash-account fidelity).
)


@dataclass(frozen=True, slots=True)
class BudgetLogEntry:
    """One appended budget version plus the actor and time that produced it."""

    timestamp: datetime
    actor_type: ActorType
    actor_id: str
    budget: RiskBudget

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "budget": self.budget.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BudgetLogEntry:
        return cls(
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            actor_type=ActorType(payload["actor_type"]),
            actor_id=payload["actor_id"],
            budget=RiskBudget.from_dict(payload["budget"]),
        )


class RiskBudgetLog:
    """Append-only JSONL log of budget versions; the last entry is current.

    Appends hold an OS-level file lock while re-reading the current version,
    so two processes (e.g. the CLI and the web console) cannot both append
    the same next version; the loser gets a ``BudgetIntegrityError`` and must
    re-read the budget it is renegotiating against.

    Reads validate version sequencing and fail closed by raising
    ``BudgetIntegrityError``: the budget powers approval decisions, and the
    seeded defaults may be wider (and lack the operator-attested equity) than
    the operator's current version, so a tampered or duplicated log must stop
    budget resolution rather than fall back.
    """

    _LOCK_TIMEOUT_SECONDS = 10.0

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = FileLock(str(path) + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def append(self, entry: BudgetLogEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire(timeout=self._LOCK_TIMEOUT_SECONDS)
        except Timeout as error:
            raise BudgetIntegrityError(
                f"Timed out waiting for the budget log lock: {self._lock.lock_file}",
                field="path",
                value=str(self._path),
            ) from error
        except OSError as error:
            raise BudgetIntegrityError(
                f"Budget log lock is unusable: {error}",
                field="path",
                value=str(self._path),
            ) from error
        try:
            current = self.current()
            expected_version = 1 if current is None else current.version + 1
            if entry.budget.version != expected_version:
                raise BudgetIntegrityError(
                    f"Budget version must be {expected_version}, got {entry.budget.version}",
                    field="version",
                    value=entry.budget.version,
                )
            line = json.dumps(entry.to_dict(), sort_keys=True, separators=(",", ":"))
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        finally:
            self._lock.release()

    def history(self) -> list[BudgetLogEntry]:
        if not self._path.exists():
            return []
        entries: list[BudgetLogEntry] = []
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = BudgetLogEntry.from_dict(json.loads(line))
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        InvalidOperation,
                        json.JSONDecodeError,
                    ) as error:
                        raise BudgetIntegrityError(
                            f"Budget log line {line_number} is malformed: {error}",
                            field="line",
                            value=line_number,
                        ) from error
                    expected_version = len(entries) + 1
                    if entry.budget.version != expected_version:
                        raise BudgetIntegrityError(
                            f"Budget log line {line_number} has version "
                            f"{entry.budget.version}; expected {expected_version}",
                            field="version",
                            value=entry.budget.version,
                        )
                    entries.append(entry)
        except (OSError, UnicodeDecodeError) as error:
            raise BudgetIntegrityError(
                f"Budget log is unreadable: {error}",
                field="path",
                value=str(self._path),
            ) from error
        return entries

    def current(self) -> RiskBudget | None:
        entries = self.history()
        if not entries:
            return None
        return entries[-1].budget
