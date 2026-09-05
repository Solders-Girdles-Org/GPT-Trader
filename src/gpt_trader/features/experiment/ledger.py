"""One transactional journal, with reports reconciled independently from identified fills."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from gpt_trader.core.fill_accounting import (
    FillFact,
    PositionBaseline,
    project_position,
    semantic_checksum,
)
from gpt_trader.features.experiment.inputs import (
    ENGINE_VERSION,
    ExperimentInput,
    implementation_digest,
    parse_input,
)


class ExperimentIntegrityError(ValueError):
    """A run cannot continue from conflicting or incomplete evidence."""


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def open_run(root: Path, source: ExperimentInput | None = None) -> sqlite3.Connection:
    """Open only a designated experiment root; never adopt a runtime store."""
    database = root / "experiment.sqlite3"
    new = not database.exists()
    if new:
        if source is None:
            raise ValueError("Experiment does not exist; create it with an input file")
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise ValueError("New experiment requires an empty directory")
        descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    connection = sqlite3.connect(database, isolation_level=None, timeout=30)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        if new:
            connection.execute(
                "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)"
            )
            for table in ("metadata", "events"):
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER {table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable experiment evidence'); END"
                    )
        row = connection.execute("SELECT payload FROM metadata WHERE id=1").fetchone()
        if row is None:
            if source is None or not new:
                raise ExperimentIntegrityError("Experiment metadata missing")
            connection.execute(
                "INSERT INTO metadata VALUES (1, ?)",
                (
                    encode(
                        {
                            "engine": ENGINE_VERSION,
                            "implementation_sha256": implementation_digest(),
                            "identity": source.identity,
                            "input": source.payload,
                        }
                    ),
                ),
            )
        else:
            saved = json.loads(row[0])
            if saved["engine"] != ENGINE_VERSION or (
                source and saved["identity"] != source.identity
            ):
                raise ExperimentIntegrityError(
                    "Input, settings or engine changed; use a new experiment directory"
                )
        connection.commit()
        return connection
    except BaseException:
        connection.rollback()
        connection.close()
        raise


def read_source(connection: sqlite3.Connection) -> ExperimentInput:
    row = connection.execute("SELECT payload FROM metadata WHERE id=1").fetchone()
    if row is None:
        raise ExperimentIntegrityError("Missing experiment metadata")
    saved = json.loads(row[0])
    source = parse_input(saved["input"])
    if saved["engine"] != ENGINE_VERSION or source.identity != saved["identity"]:
        raise ExperimentIntegrityError("Experiment metadata binding failed")
    return source


def read_events(connection: sqlite3.Connection, source: ExperimentInput) -> list[dict[str, Any]]:
    from gpt_trader.features.experiment.engine import advance

    previous = source.identity
    events: list[dict[str, Any]] = []
    for sequence, payload, checksum in connection.execute(
        "SELECT sequence, payload, checksum FROM events ORDER BY sequence"
    ):
        event = json.loads(payload)
        if sequence >= len(source.candles) or (
            sequence != len(events)
            or event["sequence"] != sequence
            or event["previous"] != previous
        ):
            raise ExperimentIntegrityError("Experiment checkpoint or hash chain is inconsistent")
        if (
            semantic_checksum(event) != checksum
            or event["bar"] != source.payload["candles"][sequence]
        ):
            raise ExperimentIntegrityError("Experiment observation checksum mismatch")
        expected = advance(
            source, sequence, events[-1]["state"] if events else initial_state(source)
        )
        expected["previous"] = previous
        if event != expected:
            raise ExperimentIntegrityError(
                "Recorded decision or account state disagrees with deterministic replay"
            )
        events.append(event)
        previous = checksum
    return events


def initial_state(source: ExperimentInput) -> dict[str, Any]:
    return {
        "cash": str(source.settings.initial_cash),
        "quantity": "0",
        "cost_basis": "0",
        "realized_net": "0",
        "fees": "0",
        "peak_equity": str(source.settings.initial_cash),
        "halted": False,
        "position": None,
        "pending": None,
    }


def reconcile(source: ExperimentInput, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive cash from fill debits/credits and position/P&L using the shared reducer."""
    state = events[-1]["state"] if events else initial_state(source)
    fills = [FillFact(**fill) for event in events for fill in event["fills"]]
    if len({fill.fill_id for fill in fills}) != len(fills):
        raise ExperimentIntegrityError("Duplicate simulated fill identity")
    cash = source.settings.initial_cash
    fees = Decimal(0)
    entry_fees = Decimal(0)
    realized_fees = Decimal(0)
    for fill in fills:
        if fill.fee is None or fill.fee_currency != "USD" or Decimal(fill.fee) < 0:
            raise ExperimentIntegrityError("Missing or invalid simulated fee")
        notional = Decimal(fill.quantity) * Decimal(fill.price)
        cash += notional * (-1 if fill.side == "buy" else 1) - Decimal(fill.fee)
        fees += Decimal(fill.fee)
        if fill.side == "buy":
            entry_fees += Decimal(fill.fee)
        else:
            realized_fees += entry_fees + Decimal(fill.fee)
            entry_fees = Decimal(0)
    projection = project_position(
        source.symbol,
        fills,
        PositionBaseline(
            source.symbol,
            (source.candles[0].ts - timedelta(seconds=1)).isoformat(),
            "0",
            None,
            "Explicit flat synthetic opening inventory",
            "local-experiment",
        ),
    )
    if not projection.complete or projection.quantity is None or projection.realized_pnl is None:
        raise ExperimentIntegrityError(f"Incomplete position reconciliation: {projection.reasons}")
    mark = source.candles[len(events) - 1].close if events else Decimal(0)
    equity = cash + projection.quantity * mark
    unrealized = projection.quantity * mark - Decimal(state["cost_basis"])
    projected_net = (
        projection.realized_pnl
        + projection.quantity * (mark - (projection.entry_price or Decimal(0)))
        - fees
    )
    if (
        cash != Decimal(state["cash"])
        or projection.quantity != Decimal(state["quantity"])
        or fees != Decimal(state["fees"])
        or Decimal(state["cost_basis"])
        != projection.quantity * (projection.entry_price or Decimal(0)) + entry_fees
        or Decimal(state["realized_net"]) != projection.realized_pnl - realized_fees
        or cash < 0
        or projection.quantity < 0
        or projected_net != equity - source.settings.initial_cash
        or Decimal(state["realized_net"]) + unrealized != projected_net
    ):
        raise ExperimentIntegrityError(
            "Cash, inventory, fees or net P&L failed independent reconciliation"
        )
    return {
        "cash": str(cash),
        "quantity": str(projection.quantity),
        "mark": str(mark),
        "equity": str(equity),
        "realized_net": state["realized_net"],
        "unrealized_net": str(unrealized),
        "net_pnl": str(projected_net),
        "fees": str(fees),
        "fill_count": len(fills),
        "reconciled": True,
    }


def inspect_run(root: Path) -> dict[str, Any]:
    """Read a consistent snapshot without creating files or advancing the run."""
    database = root / "experiment.sqlite3"
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        source = read_source(connection)
        events = read_events(connection, source)
        report = reconcile(source, events)
        state = events[-1]["state"] if events else initial_state(source)
        return {
            "engine": ENGINE_VERSION,
            "implementation_sha256": implementation_digest(),
            "identity": source.identity,
            "source": source.payload["source"],
            "symbol": source.symbol,
            "settings": source.settings.to_dict(),
            "processed_bars": len(events),
            "total_bars": len(source.candles),
            "complete": len(events) == len(source.candles),
            "halted": state["halted"],
            "pending_decision": state["pending"],
            "position": state["position"],
            "last_decision": events[-1]["decision"] if events else "not started",
            "decision_counts": {
                reason: sum(event["decision"] == reason for event in events)
                for reason in sorted({event["decision"] for event in events})
            },
            "account": report,
            "activity": [
                {
                    "sequence": event["sequence"],
                    "bar_time": event["bar"]["ts"],
                    "decision": event["decision"],
                    "actions": event["actions"],
                    "fills": event["fills"],
                    "thesis": event["proposal"]["thesis"] if event["proposal"] else None,
                }
                for event in events
                if event["fills"] or event["proposal"] or event["actions"]
            ],
            "evidence_class": "deterministic historical simulation; no AI decisions or trading-readiness evidence",
        }
