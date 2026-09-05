"""Fill evidence in the existing OrdersStore database; no separate store.

All methods join the owner's transaction. Order snapshots and individual fills
are different facts and must never be added together as if disjoint executions.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from gpt_trader.core.fill_accounting import (
    AccountingIntegrityError,
    FillFact,
    PositionBaseline,
    PositionProjection,
    aware_time,
    decimal_text,
    decimal_value,
    project_position,
    semantic_checksum,
)
from gpt_trader.persistence.orders_models import (
    VENUE_TERMINAL_ORDER_STATUSES,
    OrderRecord,
    OrderStatus,
)

if TYPE_CHECKING:
    from gpt_trader.persistence.orders_store import OrdersStore

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS accounting_schema (version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS fill_facts (order_id TEXT NOT NULL, fill_id TEXT NOT NULL, payload TEXT NOT NULL, checksum TEXT NOT NULL, PRIMARY KEY(order_id, fill_id))",
    "CREATE TABLE IF NOT EXISTS fill_observations (order_id TEXT NOT NULL, observation_key TEXT NOT NULL, payload TEXT NOT NULL, checksum TEXT NOT NULL, PRIMARY KEY(order_id, observation_key))",
    "CREATE TABLE IF NOT EXISTS accounting_baselines (symbol TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)",
)


class OrdersAccounting:
    """SQL operations owned by one OrdersStore and its local ledger scope."""

    def __init__(self, store: OrdersStore) -> None:
        self.store = store

    def initialize(self) -> None:
        connection = self.store._get_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing_tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            has_accounting = bool(
                existing_tables
                & {"accounting_schema", "fill_facts", "fill_observations", "accounting_baselines"}
            )
            if (
                has_accounting
                and not {
                    "accounting_schema",
                    "fill_facts",
                    "fill_observations",
                    "accounting_baselines",
                }
                <= existing_tables
            ):
                raise AccountingIntegrityError("Incomplete accounting schema")
            for statement in _SCHEMA:
                connection.execute(statement)
            versions = [
                row[0] for row in connection.execute("SELECT version FROM accounting_schema")
            ]
            if not versions and has_accounting:
                raise AccountingIntegrityError("Existing accounting database lacks schema marker")
            if not versions:
                connection.execute("INSERT INTO accounting_schema VALUES (1)")
            elif versions != [1]:
                raise AccountingIntegrityError("Unsupported accounting schema")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _read(row: Any) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload"])
            if (
                not isinstance(payload, dict)
                or not row["checksum"]
                or semantic_checksum(payload) != row["checksum"]
            ):
                raise ValueError("checksum mismatch")
            return payload
        except (TypeError, ValueError, KeyError) as error:
            raise AccountingIntegrityError("Accounting row checksum/shape mismatch") from error

    def _facts(self, order_id: str | None = None) -> list[FillFact]:
        connection = self.store._get_connection()
        rows = (
            connection.execute(
                "SELECT * FROM fill_facts WHERE order_id = ?", (order_id,)
            ).fetchall()
            if order_id is not None
            else connection.execute("SELECT * FROM fill_facts").fetchall()
        )
        facts = []
        for row in rows:
            fact = FillFact(**self._read(row))
            self._require_observed_time(fact.executed_at)
            if fact.order_id != row["order_id"] or fact.fill_id != row["fill_id"]:
                raise AccountingIntegrityError("Fill index conflicts with payload")
            facts.append(fact)
        return facts

    def record_observation(self, order: OrderRecord, *, source: str) -> None:
        quantity = decimal_value(order.filled_quantity)
        if quantity <= 0:
            return
        price = (
            decimal_value(order.average_fill_price, positive=True)
            if order.average_fill_price is not None
            else None
        )
        payload = {
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side.lower(),
            "quantity": str(quantity),
            "notional": str(quantity * price) if price is not None else None,
            "average_price": str(price) if price is not None else None,
            "observed_at": order.updated_at.isoformat(),
            "source": source,
        }
        checksum = semantic_checksum(payload)
        with self.store.transaction():
            connection = self.store._get_connection()
            connection.execute(
                "INSERT OR IGNORE INTO fill_observations VALUES (?, ?, ?, ?)",
                (order.order_id, checksum, json.dumps(payload), checksum),
            )
            self._observations()  # Validate existing rows as well, inside the writer.

    def _observations(self) -> list[dict[str, Any]]:
        rows = self.store._get_connection().execute("SELECT * FROM fill_observations").fetchall()
        values = []
        for row in rows:
            value = self._read(row)
            if value["order_id"] != row["order_id"] or row["observation_key"] != row["checksum"]:
                raise AccountingIntegrityError("Observation index conflicts with payload")
            decimal_value(value["quantity"], positive=True)
            if value["notional"] is not None:
                decimal_value(value["notional"], positive=True)
            aware_time(value["observed_at"])
            values.append(value)
        return values

    def record_fill(self, fact: FillFact) -> bool:
        self._require_observed_time(fact.executed_at)
        with self.store.transaction():
            connection = self.store._get_connection()
            existing = self.store.get_order(fact.order_id)
            if existing is None and fact.client_order_id:
                existing = self.store.get_order_by_client_order_id(fact.client_order_id)
            if existing is not None:
                if existing.order_id != fact.order_id:
                    if (
                        existing.order_id != existing.client_order_id
                        or existing.status is not OrderStatus.PENDING
                    ):
                        raise AccountingIntegrityError(
                            "Fill conflicts with acknowledged venue order ID"
                        )
                if not existing.checksum_is_valid():
                    raise AccountingIntegrityError("Order checksum mismatch during fill admission")
                if (
                    existing.symbol != fact.symbol
                    or existing.side.lower() != fact.side
                    or (fact.client_order_id and fact.client_order_id != existing.client_order_id)
                ):
                    raise AccountingIntegrityError("Fill conflicts with order identity")
            facts = self._facts(fact.order_id)
            duplicate = next(
                (
                    item
                    for item in facts
                    if (item.order_id, item.fill_id) == (fact.order_id, fact.fill_id)
                ),
                None,
            )
            if duplicate is not None:
                if duplicate != fact:
                    raise AccountingIntegrityError("Fill ID reused with different payload")
                return False
            baseline_row = connection.execute(
                "SELECT * FROM accounting_baselines WHERE symbol = ?", (fact.symbol,)
            ).fetchone()
            if baseline_row is not None:
                baseline = PositionBaseline(**self._read(baseline_row)["baseline"])
                if fact.order_id in baseline.covered_order_ids and aware_time(
                    fact.executed_at
                ) > aware_time(baseline.effective_at):
                    raise AccountingIntegrityError(
                        "Fill execution conflicts with explicit baseline coverage"
                    )
            same_order = [item for item in facts if item.order_id == fact.order_id]
            if any(item.symbol != fact.symbol or item.side != fact.side for item in same_order):
                raise AccountingIntegrityError("Order fill facts conflict")
            same_order.append(fact)
            quantity = sum((decimal_value(item.quantity) for item in same_order), Decimal(0))
            notional = sum(
                (decimal_value(item.quantity) * decimal_value(item.price) for item in same_order),
                Decimal(0),
            )
            if (
                existing is not None
                and (existing.metadata or {}).get("intent")
                and quantity > existing.quantity
            ):
                raise AccountingIntegrityError("Identified fills exceed admitted order")
            payload = fact.to_dict()
            connection.execute(
                "INSERT INTO fill_facts VALUES (?, ?, ?, ?)",
                (fact.order_id, fact.fill_id, json.dumps(payload), semantic_checksum(payload)),
            )
            if existing is not None and quantity > existing.filled_quantity:
                if existing.status in VENUE_TERMINAL_ORDER_STATUSES:
                    raise AccountingIntegrityError("Identified fills exceed terminal receipt")
                updated = replace(
                    existing,
                    order_id=fact.order_id,
                    filled_quantity=quantity,
                    average_fill_price=notional / quantity,
                    status=(
                        OrderStatus.FILLED
                        if quantity == existing.quantity
                        else OrderStatus.PARTIALLY_FILLED
                    ),
                    updated_at=datetime.now(timezone.utc),
                )
                result = self.store.upsert_by_client_id(updated, raise_on_error=True)
                if not result.success:
                    raise AccountingIntegrityError("Order fill update failed")
            return True

    @staticmethod
    def _require_observed_time(value: str) -> None:
        if aware_time(value) > datetime.now(timezone.utc):
            raise AccountingIntegrityError(
                "Future accounting evidence cannot describe current inventory"
            )

    @staticmethod
    def _receipt_binding(order: OrderRecord) -> dict[str, Any]:
        return {
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": decimal_text(order.quantity),
            "filled_quantity": decimal_text(order.filled_quantity),
            "average_fill_price": (
                decimal_text(order.average_fill_price)
                if order.average_fill_price is not None
                else None
            ),
            "status": order.status.value,
            "intent": (order.metadata or {}).get("intent"),
        }

    def record_baseline(self, baseline: PositionBaseline) -> None:
        self._require_observed_time(baseline.effective_at)
        with self.store.transaction():
            connection = self.store._get_connection()
            bindings = {}
            for order_id in baseline.covered_order_ids:
                order = self.store.get_order(order_id)
                if (
                    order is None
                    or order.symbol != baseline.symbol
                    or order.status not in VENUE_TERMINAL_ORDER_STATUSES
                    or not order.checksum_is_valid()
                ):
                    raise AccountingIntegrityError(
                        "Baseline can cover only a matching complete order receipt"
                    )
                bindings[order_id] = self._receipt_binding(order)
            payload = {"baseline": baseline.to_dict(), "covered_receipts": bindings}
            row = connection.execute(
                "SELECT * FROM accounting_baselines WHERE symbol = ?", (baseline.symbol,)
            ).fetchone()
            if row is not None:
                if semantic_checksum(self._read(row)) != semantic_checksum(payload):
                    raise AccountingIntegrityError("Opening baseline is immutable")
                return
            for fact in self._facts():
                if fact.order_id in baseline.covered_order_ids and aware_time(
                    fact.executed_at
                ) > aware_time(baseline.effective_at):
                    raise AccountingIntegrityError(
                        "Baseline conflicts with identified later execution"
                    )
            connection.execute(
                "INSERT INTO accounting_baselines VALUES (?, ?, ?)",
                (baseline.symbol, json.dumps(payload), semantic_checksum(payload)),
            )

    def projections(self) -> dict[str, PositionProjection]:
        with self.store.transaction(write=False):
            connection = self.store._get_connection()
            facts = self._facts()
            observations = self._observations()
            baselines: dict[str, PositionBaseline] = {}
            for row in connection.execute("SELECT * FROM accounting_baselines"):
                payload = self._read(row)
                baseline = PositionBaseline(**payload["baseline"])
                self._require_observed_time(baseline.effective_at)
                bindings = payload["covered_receipts"]
                if set(bindings) != set(baseline.covered_order_ids):
                    raise AccountingIntegrityError("Baseline receipt binding is incomplete")
                for order_id, binding in bindings.items():
                    order = self.store.get_order(order_id)
                    if order is None or semantic_checksum(
                        self._receipt_binding(order)
                    ) != semantic_checksum(binding):
                        raise AccountingIntegrityError("Baseline covered receipt changed")
                    for observation in observations:
                        if observation["order_id"] != order_id:
                            continue
                        observed_quantity = decimal_value(observation["quantity"])
                        bound_quantity = decimal_value(binding["filled_quantity"])
                        if (
                            observed_quantity > bound_quantity
                            or observation["symbol"] != baseline.symbol
                            or observation["side"] != binding["side"]
                        ):
                            raise AccountingIntegrityError(
                                "Observation conflicts with baseline receipt"
                            )
                        if (
                            observed_quantity == bound_quantity
                            and observation.get("average_price") is not None
                            and binding["average_fill_price"] is not None
                            and decimal_value(observation["average_price"])
                            != decimal_value(binding["average_fill_price"])
                        ):
                            raise AccountingIntegrityError(
                                "Observation notional differs from baseline receipt"
                            )
                if baseline.symbol != row["symbol"]:
                    raise AccountingIntegrityError("Baseline index conflicts with payload")
                baselines[baseline.symbol] = baseline
            # Existing order receipts may precede this schema or come from other writers.
            orders = [
                self.store._row_to_record(row) for row in connection.execute("SELECT * FROM orders")
            ]
            if any(not order.checksum_is_valid() for order in orders):
                raise AccountingIntegrityError("Order checksum mismatch in projection")
            symbols = (
                set(baselines)
                | {fact.symbol for fact in facts}
                | {
                    order.symbol
                    for order in orders
                    if order.filled_quantity > 0
                    or order.status in {OrderStatus.PENDING, OrderStatus.FAILED}
                }
            )
            result = {}
            for symbol in symbols:
                symbol_facts = [fact for fact in facts if fact.symbol == symbol]
                reasons = [
                    f"pending_submission_unresolved:{order.order_id}"
                    for order in orders
                    if order.symbol == symbol
                    and order.status in {OrderStatus.PENDING, OrderStatus.FAILED}
                ]
                order_ids = {item.order_id for item in symbol_facts} | {
                    order.order_id
                    for order in orders
                    if order.symbol == symbol and order.filled_quantity > 0
                }
                for order_id in order_ids:
                    position_baseline = baselines.get(symbol)
                    if (
                        position_baseline is not None
                        and order_id in position_baseline.covered_order_ids
                    ):
                        continue  # Explicit operator evidence, never inferred from creation_time.
                    recorded = [item for item in symbol_facts if item.order_id == order_id]
                    total = sum((decimal_value(item.quantity) for item in recorded), Decimal(0))
                    cost = sum(
                        (
                            decimal_value(item.quantity) * decimal_value(item.price)
                            for item in recorded
                        ),
                        Decimal(0),
                    )
                    evidence = [item for item in observations if item["order_id"] == order_id]
                    order = next((item for item in orders if item.order_id == order_id), None)
                    if order is not None:
                        if not order.checksum_is_valid():
                            raise AccountingIntegrityError("Order checksum mismatch in projection")
                        evidence.append(
                            {
                                "quantity": str(order.filled_quantity),
                                "average_price": (
                                    str(order.average_fill_price)
                                    if order.average_fill_price is not None
                                    else None
                                ),
                            }
                        )
                    if evidence:
                        latest_quantity = max(decimal_value(item["quantity"]) for item in evidence)
                        if total < latest_quantity:
                            reasons.append(f"identified_fills_missing:{order_id}")
                        elif (
                            total > latest_quantity
                            and order is not None
                            and order.status in VENUE_TERMINAL_ORDER_STATUSES
                        ):
                            reasons.append(f"fill_receipt_conflict:{order_id}")
                        for item in evidence:
                            if (
                                decimal_value(item["quantity"]) == total
                                and item.get("average_price") is not None
                                and total > 0
                                and decimal_value(item["average_price"]) != cost / total
                            ):
                                reasons.append(f"fill_notional_unverified:{order_id}")
                result[symbol] = project_position(
                    symbol, symbol_facts, baselines.get(symbol), coverage_reasons=tuple(reasons)
                )
            return result
