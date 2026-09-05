"""Durable paper submission intents and broker receipts in the shared state store.

No broker calls occur here. A receipt is an observed fact, never authorization
for another submission. Missing receipts remain uncertain after a restart.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from gpt_trader.core.order_intent import OrderIntent
from gpt_trader.core.trading import OrderSide, OrderStatus, OrderType
from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.models import TradeIdea
from gpt_trader.features.trade_ideas.persistence import StateRepository

EXECUTION_STREAM = "paper_execution.jsonl"


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ExecutionJournalIntegrityError(ValidationError):
    """A durable intent/receipt conflicts with its immutable identity."""


class PaperReceiptConflictError(ValidationError):
    """An observed broker response does not match its submitted intent."""


@dataclass(frozen=True)
class ExecutionJournalEntry:
    decision_id: str
    record_hash: str
    intent: dict[str, Any]
    receipt: dict[str, Any] | None = None
    reconciled: bool = False


class ExecutionJournal:
    def __init__(self, repository: StateRepository) -> None:
        self.repository = repository

    def entries(self) -> dict[str, ExecutionJournalEntry]:
        with self.repository.transaction():
            if self.repository.active:
                lines = self.repository.lines(EXECUTION_STREAM)
            else:
                path = self.repository.root / EXECUTION_STREAM
                lines = path.read_text().splitlines() if path.exists() else []
            entries: dict[str, ExecutionJournalEntry] = {}
            simulated_ids: set[str] = set()
            try:
                for line in lines:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event["kind"] == "simulated_resolution":
                        self._validate_simulated_resolution(event)
                        operation_id = event["resolution"]["operation_id"]
                        if operation_id in simulated_ids or operation_id in entries:
                            raise ValueError("Duplicate simulated operation identity")
                        simulated_ids.add(operation_id)
                        continue
                    decision_id = event["decision_id"]
                    kind = event["kind"]
                    current = entries.get(decision_id)
                    if kind == "intent":
                        if current is not None or decision_id in simulated_ids:
                            raise ValueError("duplicate intent")
                        intent = event["intent"]
                        OrderIntent.from_dict(intent)
                        if intent["client_order_id"] != decision_id or intent["side"] not in {
                            "buy",
                            "sell",
                        }:
                            raise ValueError("invalid intent identity or side")
                        quantity = Decimal(intent["quantity"])
                        if not quantity.is_finite() or quantity <= 0:
                            raise ValueError("invalid intent quantity")
                        if (
                            not intent["symbol"]
                            or intent["order_type"] != "market"
                            or (
                                intent["reduce_only"] is not False
                                and "position_operation" not in intent
                            )
                        ):
                            raise ValueError("unsupported paper entry intent")
                        if event["intent_hash"] != payload_hash(intent):
                            raise ValueError("intent hash mismatch")
                        entries[decision_id] = ExecutionJournalEntry(
                            decision_id, event["record_hash"], intent
                        )
                    elif kind == "receipt":
                        if current is None or current.receipt is not None or current.reconciled:
                            raise ValueError("receipt without unique pending intent")
                        receipt = event["receipt"]
                        if event["intent_hash"] != payload_hash(current.intent) or event[
                            "receipt_hash"
                        ] != payload_hash(receipt):
                            raise ValueError("receipt hash mismatch")
                        self.validate_receipt(current, receipt)
                        entries[decision_id] = ExecutionJournalEntry(
                            decision_id, current.record_hash, current.intent, receipt
                        )
                    elif kind == "reconciled":
                        if (
                            current is None
                            or current.receipt is None
                            or current.receipt["status"] != "filled"
                            or current.reconciled
                        ):
                            raise ValueError("reconciliation without unique receipt")
                        if event["receipt_hash"] != payload_hash(current.receipt):
                            raise ValueError("reconciled receipt hash mismatch")
                        entries[decision_id] = ExecutionJournalEntry(
                            decision_id, current.record_hash, current.intent, current.receipt, True
                        )
                    else:
                        raise ValueError("unknown journal event")
            except (
                KeyError,
                TypeError,
                ValueError,
                ArithmeticError,
                PaperReceiptConflictError,
            ) as error:
                raise ExecutionJournalIntegrityError(
                    f"Invalid paper execution journal: {error}"
                ) from error
            return entries

    @staticmethod
    def validate_binding(entry: ExecutionJournalEntry, idea: TradeIdea) -> None:
        side = {"long": OrderSide.BUY, "short": OrderSide.SELL}.get(idea.direction.value)
        quantity = idea.sizing_recommendation.quantity
        if idea.position_operation is not None:
            side = {"long": OrderSide.SELL, "short": OrderSide.BUY}.get(idea.direction.value)
            if idea.position_operation.action == "close" and quantity is None:
                quantity = Decimal(entry.intent["quantity"])  # Frozen at serialized admission.
        if side is None or quantity is None:
            raise ExecutionJournalIntegrityError("Execution intent conflicts with idea")
        expected = OrderIntent(
            idea.decision_id,
            idea.instrument,
            side,
            OrderType.MARKET,
            quantity,
            reduce_only=idea.position_operation is not None,
            position_operation=idea.position_operation,
        )
        if (
            entry.record_hash != idea.record_hash()
            or OrderIntent.from_dict(entry.intent) != expected
        ):
            raise ExecutionJournalIntegrityError("Execution intent conflicts with idea")

    @staticmethod
    def validate_receipt(entry: ExecutionJournalEntry, receipt: dict[str, Any]) -> None:
        for field in ("client_order_id", "symbol", "side"):
            if receipt[field] != entry.intent[field]:
                raise PaperReceiptConflictError(f"Broker receipt {field} conflicts with intent")
        if receipt["decision_id"] != entry.decision_id or not receipt["order_id"]:
            raise PaperReceiptConflictError("Broker receipt identity missing or conflicting")
        if receipt["status"] not in {status.value.lower() for status in OrderStatus}:
            raise PaperReceiptConflictError("Unknown broker receipt status")
        quantity = Decimal(receipt["quantity"])
        requested = Decimal(entry.intent["quantity"])
        if not quantity.is_finite() or quantity < 0 or quantity > requested:
            raise PaperReceiptConflictError("Broker receipt quantity exceeds intent")
        if receipt["status"] == "filled" and quantity != requested:
            raise PaperReceiptConflictError("Terminal receipt must fill the complete intent")
        price = Decimal(receipt["price"]) if receipt["price"] is not None else None
        if receipt["status"] == "filled" and (price is None or not price.is_finite() or price <= 0):
            raise PaperReceiptConflictError("Filled receipt requires a finite positive price")
        timestamp = datetime.fromisoformat(receipt["filled_at"])
        if timestamp.utcoffset() is None:
            raise PaperReceiptConflictError("Receipt timestamp requires timezone")
        if "position_operation" in entry.intent:
            try:
                observed_at = datetime.fromisoformat(receipt["observed_at"])
                if observed_at.utcoffset() is None or timestamp > observed_at:
                    raise ValueError("execution cannot occur after observation")
            except (KeyError, TypeError, ValueError) as error:
                raise PaperReceiptConflictError(
                    "Targeted receipt requires valid observation time"
                ) from error

    def record_intent(self, decision_id: str, record_hash: str, intent: dict[str, Any]) -> None:
        with self.repository.transaction(write=True):
            if decision_id in self.entries():
                raise ExecutionJournalIntegrityError("Decision already has a durable intent")
            self.repository.append(
                EXECUTION_STREAM,
                json.dumps(
                    {
                        "kind": "intent",
                        "decision_id": decision_id,
                        "record_hash": record_hash,
                        "intent": intent,
                        "intent_hash": payload_hash(intent),
                    }
                ),
            )
            self.entries()  # Validate inside the writer transaction, before commit.

    def record_receipt(self, decision_id: str, receipt: dict[str, Any]) -> None:
        with self.repository.transaction(write=True):
            entry = self.entries().get(decision_id)
            if entry is None:
                raise ExecutionJournalIntegrityError("Receipt has no durable intent")
            if entry.receipt is not None:
                if entry.receipt != receipt:
                    raise ExecutionJournalIntegrityError("Conflicting receipt for the same intent")
                return
            self.validate_receipt(entry, receipt)
            self.repository.append(
                EXECUTION_STREAM,
                json.dumps(
                    {
                        "kind": "receipt",
                        "decision_id": decision_id,
                        "intent_hash": payload_hash(entry.intent),
                        "receipt_hash": payload_hash(receipt),
                        "receipt": receipt,
                    }
                ),
            )

    def mark_reconciled(self, decision_id: str) -> None:
        with self.repository.transaction(write=True):
            entry = self.entries()[decision_id]
            if entry.reconciled:
                return
            if entry.receipt is None or entry.receipt["status"] != "filled":
                raise ExecutionJournalIntegrityError(
                    "Cannot reconcile without a terminal fill receipt"
                )
            self.repository.append(
                EXECUTION_STREAM,
                json.dumps(
                    {
                        "kind": "reconciled",
                        "decision_id": decision_id,
                        "receipt_hash": payload_hash(entry.receipt),
                    }
                ),
            )

    @staticmethod
    def _validate_simulated_resolution(event: dict[str, Any]) -> None:
        from gpt_trader.core.order_intent import PositionOperation
        from gpt_trader.features.trade_ideas.closeout import CloseoutResolution

        payload = event["resolution"]
        PositionOperation("close", payload["target_decision_id"], payload["target_record_hash"])
        if event["checksum"] != payload_hash(payload) or not payload["operation_id"]:
            raise ExecutionJournalIntegrityError("Invalid simulated resolution identity/checksum")
        for field in ("quantity", "price"):
            value = Decimal(payload[field])
            if not value.is_finite() or value <= 0:
                raise ExecutionJournalIntegrityError("Invalid simulated resolution value")
        if payload["operation_id"] != f"simulated-close-{payload['target_record_hash']}":
            raise ExecutionJournalIntegrityError("Simulated close identity does not pin target")
        if datetime.fromisoformat(
            payload["observed_at"]
        ).utcoffset() is None or datetime.fromisoformat(
            payload["resolved_at"]
        ) > datetime.fromisoformat(
            payload["observed_at"]
        ):
            raise ExecutionJournalIntegrityError(
                "Simulated resolution cannot precede observation evidence"
            )
        if datetime.fromisoformat(payload["resolved_at"]).utcoffset() is None:
            raise ExecutionJournalIntegrityError("Simulated resolution requires timestamp")
        CloseoutResolution(payload["resolution"])
        if payload["source"] != "simulated_candle_resolution" or not payload["evidence"]:
            raise ExecutionJournalIntegrityError("Simulated resolution requires explicit evidence")

    def simulated_resolutions(self) -> tuple[dict[str, Any], ...]:
        with self.repository.transaction():
            self.entries()
            if self.repository.active:
                lines = self.repository.lines(EXECUTION_STREAM)
            else:
                path = self.repository.root / EXECUTION_STREAM
                lines = path.read_text().splitlines() if path.exists() else []
            records: dict[str, dict[str, Any]] = {}
            for line in lines:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event["kind"] != "simulated_resolution":
                    continue
                payload = event["resolution"]
                if payload["operation_id"] in records:
                    raise ExecutionJournalIntegrityError("Duplicate simulated resolution identity")
                records[payload["operation_id"]] = payload
            return tuple(records.values())

    def record_simulated_resolution(self, payload: dict[str, Any]) -> None:
        with self.repository.transaction(write=True):
            for existing in self.simulated_resolutions():
                if existing["operation_id"] == payload["operation_id"]:
                    if existing != payload:
                        raise ExecutionJournalIntegrityError("Conflicting simulated resolution")
                    return
            event = {
                "kind": "simulated_resolution",
                "resolution": payload,
                "checksum": payload_hash(payload),
            }
            self._validate_simulated_resolution(event)
            self.repository.append(EXECUTION_STREAM, json.dumps(event))
