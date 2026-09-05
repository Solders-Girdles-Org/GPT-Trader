"""Identified execution facts and replayable local trade accounting.

This is a projection of a configured ledger, not evidence of venue inventory or
complete account history. Cumulative order snapshots are not individual fills.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


class AccountingIntegrityError(ValueError):
    """Persisted accounting evidence is malformed or contradictory."""


def semantic_checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def decimal_value(value: Any, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or (positive and result <= 0):
            raise ValueError("nonfinite or nonpositive value")
        return result
    except (ValueError, TypeError, ArithmeticError) as error:
        raise AccountingIntegrityError("Invalid accounting decimal") from error


def decimal_text(value: Decimal) -> str:
    """Canonical decimal spelling without context-dependent rounding."""
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def aware_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("timezone required")
        return result
    except (ValueError, TypeError, AttributeError) as error:
        raise AccountingIntegrityError("Invalid accounting timestamp") from error


@dataclass(frozen=True)
class FillFact:
    fill_id: str
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: str
    price: str
    executed_at: str
    fee: str | None = None
    fee_currency: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.fill_id
            or not self.order_id
            or not self.symbol
            or self.side not in {"buy", "sell"}
        ):
            raise AccountingIntegrityError("Fill requires identity, symbol and explicit side")
        object.__setattr__(
            self, "quantity", decimal_text(decimal_value(self.quantity, positive=True))
        )
        object.__setattr__(self, "price", decimal_text(decimal_value(self.price, positive=True)))
        object.__setattr__(
            self, "executed_at", aware_time(self.executed_at).astimezone(timezone.utc).isoformat()
        )
        if self.fee is not None:
            object.__setattr__(self, "fee", decimal_text(decimal_value(self.fee)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionBaseline:
    """Explicit opening inventory inclusive of executions at effective_at.

    A caller must supply evidence; a database being empty is not a flat account.
    Positive quantity is long, negative short. PnL is measured after this bound.
    """

    symbol: str
    effective_at: str
    quantity: str
    entry_price: str | None
    evidence: str
    actor_id: str
    covered_order_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.symbol or not self.evidence.strip() or not self.actor_id.strip():
            raise AccountingIntegrityError("Baseline requires symbol, evidence and actor identity")
        quantity = decimal_value(self.quantity)
        object.__setattr__(self, "quantity", decimal_text(quantity))
        object.__setattr__(
            self, "effective_at", aware_time(self.effective_at).astimezone(timezone.utc).isoformat()
        )
        object.__setattr__(self, "covered_order_ids", tuple(self.covered_order_ids))
        if any(not value for value in self.covered_order_ids) or len(
            set(self.covered_order_ids)
        ) != len(self.covered_order_ids):
            raise AccountingIntegrityError("Baseline covered order IDs must be unique and explicit")
        if quantity != 0:
            decimal_value(self.entry_price, positive=True)
        elif self.entry_price is not None:
            decimal_value(self.entry_price, positive=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionProjection:
    symbol: str
    quantity: Decimal | None
    entry_price: Decimal | None
    realized_pnl: Decimal | None
    reasons: tuple[str, ...]
    fill_count: int
    fees: tuple[tuple[str, Decimal], ...] = ()
    fee_coverage: str = "unknown"

    @property
    def complete(self) -> bool:
        return not self.reasons


def project_position(
    symbol: str,
    fills: list[FillFact],
    baseline: PositionBaseline | None,
    *,
    coverage_reasons: tuple[str, ...] = (),
) -> PositionProjection:
    if (baseline is not None and baseline.symbol != symbol) or any(
        fill.symbol != symbol for fill in fills
    ):
        raise AccountingIntegrityError("Projection input symbol mismatch")
    reasons = list(coverage_reasons)
    if baseline is None:
        reasons.append("opening_inventory_unknown")
        return PositionProjection(symbol, None, None, None, tuple(sorted(set(reasons))), len(fills))
    boundary = aware_time(baseline.effective_at)
    # The baseline includes all executions through its inclusive timestamp.
    relevant = sorted(
        (fill for fill in fills if aware_time(fill.executed_at) > boundary),
        key=lambda fill: (aware_time(fill.executed_at), fill.order_id, fill.fill_id),
    )
    by_time: dict[datetime, list[FillFact]] = {}
    for fill in relevant:
        by_time.setdefault(aware_time(fill.executed_at), []).append(fill)
    inventory = decimal_value(baseline.quantity)
    for group in by_time.values():
        sides = {fill.side for fill in group}
        signed = sum(
            (decimal_value(fill.quantity) * (1 if fill.side == "buy" else -1) for fill in group),
            Decimal(0),
        )
        if len(sides) > 1 or (
            inventory * signed < 0
            and abs(signed) > abs(inventory)
            and len({fill.price for fill in group}) > 1
        ):
            reasons.append("execution_order_ambiguous")
        inventory += signed
    if reasons:
        return PositionProjection(
            symbol, None, None, None, tuple(sorted(set(reasons))), len(relevant)
        )
    quantity = decimal_value(baseline.quantity)
    entry = decimal_value(baseline.entry_price) if baseline.entry_price is not None else Decimal(0)
    realized = Decimal(0)
    fees: dict[str, Decimal] = {}
    for fill in relevant:
        size = decimal_value(fill.quantity, positive=True)
        price = decimal_value(fill.price, positive=True)
        signed = size if fill.side == "buy" else -size
        if quantity == 0 or (quantity > 0) == (signed > 0):
            entry = (abs(quantity) * entry + size * price) / (abs(quantity) + size)
        else:
            closed = min(abs(quantity), size)
            realized += closed * (price - entry) * (1 if quantity > 0 else -1)
            if size > abs(quantity):
                entry = price  # Excess opens the opposite position at this execution price.
            elif size == abs(quantity):
                entry = Decimal(0)
        quantity += signed
        if fill.fee is not None:
            currency = fill.fee_currency or "unknown"
            fees[currency] = fees.get(currency, Decimal(0)) + decimal_value(fill.fee)
    return PositionProjection(
        symbol,
        quantity,
        entry,
        realized,
        (),
        len(relevant),
        tuple(sorted(fees.items())),
        (
            "complete"
            if all(fill.fee is not None and fill.fee_currency for fill in relevant)
            else "unknown"
        ),
    )
