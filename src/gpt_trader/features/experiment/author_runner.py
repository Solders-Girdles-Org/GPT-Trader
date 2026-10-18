"""Replayable portfolio author plumbing. Scratch smoke output is never evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from gpt_trader.core.fill_accounting import semantic_checksum
from gpt_trader.features.experiment.engine import advance
from gpt_trader.features.experiment.inputs import ExperimentInput, ManagerSettings
from gpt_trader.features.experiment.ledger import ExperimentIntegrityError, encode, initial_state
from gpt_trader.features.experiment.rulebook_author import (
    MAX_DYNAMIC_BYTES,
    AuthorSettings,
    author_turn,
    build_author_request,
    validate_portfolio,
)
from gpt_trader.features.experiment.trade_manager import (
    DecisionTransportProtocol,
    TransportError,
    TransportReply,
    TransportUnavailableError,
)


class _ReplayReply:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.model = record["model"]

    def decide(self, request: dict[str, Any]) -> TransportReply:
        from decimal import Decimal

        if request != self.record["request"]:
            raise ExperimentIntegrityError("Rebuilt author request differs from journal")
        raw = self.record["reply"]
        if raw is None:
            raise TransportError(self.record["error"])
        fields = {k: v for k, v in raw.items() if k != "cost_usd"}
        for name in ("input_usd_per_million", "output_usd_per_million"):
            fields[name] = Decimal(fields[name])
        return TransportReply(**fields)


def _source(source: ExperimentInput, schedule: list[dict[str, Any]]) -> ExperimentInput:
    return replace(source, payload={**source.payload, "rulebooks": schedule})


def run_author_smoke(
    sources: list[ExperimentInput],
    root: Path,
    manager: ManagerSettings,
    transport: DecisionTransportProtocol | None = None,
    *,
    settings: AuthorSettings = AuthorSettings(),
    max_turns: int = 3,
) -> dict[str, Any]:
    """A few author turns and intervening simulated bars, on known inputs only.

    Serializes the entire portfolio per bar. Committed turns replay without a
    call; a call interrupted before commit is counted by the external ledger
    and may be reasked. A resume must retain that ledger and cap. No reports,
    reading, benchmark result or publication are produced here.
    """
    validate_portfolio(sources)
    if type(max_turns) is not int or not 1 <= max_turns <= 3:
        raise ValueError("Smoke permits one to three author turns")
    sources = sorted(sources, key=lambda s: s.symbol)
    if "runtime_data" in root.resolve().parts:
        raise ValueError("Smoke belongs in scratch, outside runtime_data")
    if transport is not None and transport.model != manager.model:
        raise ValueError("Transport model differs from author settings")
    ledger = getattr(transport, "ledger", None)
    binding = {
        "schema": "author-smoke/1",
        "not_evidence": True,
        "sources": [s.identity for s in sources],
        "manager": manager.to_dict(),
        "settings": asdict(settings),
        "max_turns": max_turns,
        "ledger": None
        if ledger is None
        else {"path": str(ledger.path.resolve()), "cap": str(ledger.cap_usd)},
    }
    root.mkdir(parents=True, exist_ok=True)
    database = root / "author.sqlite3"
    new = not database.exists()
    if new and any(root.iterdir()):
        raise ValueError("New smoke requires an empty scratch directory")
    with sqlite3.connect(database, timeout=30) as connection:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (payload TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS bars (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)"
        )
        rows = connection.execute("SELECT payload FROM metadata").fetchall()
        if not rows and not new:
            raise ExperimentIntegrityError("Author journal metadata missing")
        if not rows:
            connection.execute("INSERT INTO metadata VALUES (?)", (encode(binding),))
        elif len(rows) != 1 or json.loads(rows[0][0]) != binding:
            raise ExperimentIntegrityError("Author smoke input/settings/ledger binding changed")
        connection.commit()
        (root / "NOT-EVIDENCE.txt").write_text(
            "Plumbing smoke only. Known bars; not forward or performance evidence.\n"
        )
        states = {s.symbol: initial_state(s) for s in sources}
        schedules = {s.symbol: list(s.payload["rulebooks"]) for s in sources}
        current = {symbol: schedule[0]["rules"] for symbol, schedule in schedules.items()}
        turns = []
        first = sources[0]
        end = min(
            len(first.candles), first.scored_index + (max_turns - 1) * settings.cadence_bars + 1
        )
        previous = semantic_checksum(binding)
        # Validate the entire stored chain before any new paid request. A gap or
        # corrupt later row must not be mistaken for an uncommitted turn.
        stored = connection.execute(
            "SELECT sequence, payload, checksum FROM bars ORDER BY sequence"
        ).fetchall()
        chain = previous
        for index, (saved_sequence, payload, checksum) in enumerate(stored):
            saved_event = json.loads(payload)
            if (
                saved_sequence != index
                or saved_event["sequence"] != index
                or saved_event["previous"] != chain
                or semantic_checksum(saved_event) != checksum
            ):
                raise ExperimentIntegrityError("Author journal replay chain is corrupt")
            chain = checksum
        if len(stored) > end:
            raise ExperimentIntegrityError("Author journal has extra bars")
        for sequence in range(end):
            connection.execute("BEGIN IMMEDIATE")
            saved = connection.execute(
                "SELECT payload, checksum FROM bars WHERE sequence=?", (sequence,)
            ).fetchone()
            turn = None
            if (
                sequence >= first.scored_index
                and (sequence - first.scored_index) % settings.cadence_bars == 0
            ):
                # Advance provisionally to observe this bar's fills/mark, without
                # sending its proposed entry/exit to the author. Recompute below
                # from pre-bar state with the newly authored rules at this close.
                accounts = {}
                for source in sources:
                    marked = advance(
                        _source(source, schedules[source.symbol]), sequence, states[source.symbol]
                    )["state"]
                    accounts[source.symbol] = {
                        k: marked[k] for k in ("cash", "quantity", "position", "halted")
                    }
                request = build_author_request(sources, sequence, accounts, current, settings)
                dynamic = {"accounts": accounts, "rulebooks": current}
                if len(encode(dynamic).encode()) > MAX_DYNAMIC_BYTES:
                    raise ValueError("Author dynamic context exceeds offline spend bound")
                replay = json.loads(saved[0])["turn"] if saved else None
                chosen = _ReplayReply(replay) if replay else transport
                if chosen is None:
                    raise TransportUnavailableError(
                        "Author turn requires an explicitly injected transport"
                    )
                turn = author_turn(request, chosen)
                current = turn["rulebooks"]
                turns.append(turn)
                if turn["accepted"]:
                    for source in sources:
                        schedule = schedules[source.symbol]
                        item = {
                            "effective_from": source.payload["candles"][sequence]["ts"],
                            "rules": current[source.symbol],
                        }
                        # At first scored close v2 replaces v1's evaluation, but
                        # the journal retains both starting and authored versions.
                        schedule.append(item)
            events = {
                s.symbol: advance(_source(s, schedules[s.symbol]), sequence, states[s.symbol])
                for s in sources
            }
            event = {"sequence": sequence, "previous": previous, "turn": turn, "events": events}
            checksum = semantic_checksum(event)
            if saved:
                if json.loads(saved[0]) != event or saved[1] != checksum:
                    raise ExperimentIntegrityError("Author journal replay differs")
            else:
                connection.execute(
                    "INSERT INTO bars VALUES (?, ?, ?)", (sequence, encode(event), checksum)
                )
            connection.commit()
            states = {symbol: e["state"] for symbol, e in events.items()}
            previous = checksum
        if connection.execute("SELECT count(*) FROM bars").fetchone()[0] != end:
            raise ExperimentIntegrityError("Author journal has extra bars")
    return {
        "not_evidence": True,
        "turns": len(turns),
        "accepted": sum(t["accepted"] for t in turns),
        "journal": str(database),
        "rulebooks": current,
    }
