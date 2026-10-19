"""Replayable portfolio author runs. Scratch smoke output is never evidence.

One journal holds one portfolio in one cost case: every bar commits the author
turn (if due) and all coins' simulated events atomically. The smoke stops after
one to three turns on known bars; the full run covers every bar of a frozen
window and binds its input payloads so it can be inspected without them.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from gpt_trader.core.fill_accounting import semantic_checksum
from gpt_trader.features.experiment.engine import advance
from gpt_trader.features.experiment.inputs import (
    ENGINE_VERSION,
    ExperimentInput,
    ManagerSettings,
    implementation_digest,
    parse_input,
)
from gpt_trader.features.experiment.ledger import (
    ExperimentIntegrityError,
    encode,
    initial_state,
    reconcile,
)
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
from gpt_trader.features.experiment.valuation import end_valuation

DATABASE = "author.sqlite3"
RUN_SCHEMA = "author-run/1"


class _ReplayReply:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.model = record["model"]

    def decide(self, request: dict[str, Any]) -> TransportReply:
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


def _ledger_binding(transport: DecisionTransportProtocol | None) -> dict[str, str] | None:
    ledger = getattr(transport, "ledger", None)
    if ledger is None:
        return None
    return {"path": str(ledger.path.resolve()), "cap": str(ledger.cap_usd)}


@dataclass
class _Progress:
    """Everything a journal pass rebuilds, bar by bar."""

    sources: list[ExperimentInput]
    schedules: dict[str, list[dict[str, Any]]]
    current: dict[str, dict[str, Any]]
    processed: int = 0
    turns: list[dict[str, Any]] = field(default_factory=list)
    events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def _journal(
    connection: sqlite3.Connection,
    binding: dict[str, Any],
    sources: list[ExperimentInput],
    settings: AuthorSettings,
    transport: DecisionTransportProtocol | None,
    end: int,
    *,
    write: bool,
) -> _Progress:
    """Replay every committed bar, then (when writing) advance new bars up to ``end``.

    A committed turn is always rebuilt from its record and never sent again; a
    read-only pass stops at the last committed bar.
    """
    states = {s.symbol: initial_state(s) for s in sources}
    schedules = {s.symbol: list(s.payload["rulebooks"]) for s in sources}
    progress = _Progress(
        sources,
        schedules,
        {symbol: schedule[0]["rules"] for symbol, schedule in schedules.items()},
        events={s.symbol: [] for s in sources},
    )
    first = sources[0]
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
    limit = end if write else len(stored)
    if len(stored) > limit:
        raise ExperimentIntegrityError("Author journal has extra bars")
    for sequence in range(limit):
        saved = stored[sequence] if sequence < len(stored) else None
        if write:
            connection.execute("BEGIN IMMEDIATE")
            # Another writer must not have committed this bar since the chain
            # was read; the write lock is held through the turn and its commit.
            if (
                saved is None
                and connection.execute(
                    "SELECT 1 FROM bars WHERE sequence=?", (sequence,)
                ).fetchone()
            ):
                raise ExperimentIntegrityError("Author journal advanced concurrently")
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
            request = build_author_request(sources, sequence, accounts, progress.current, settings)
            dynamic = {"accounts": accounts, "rulebooks": progress.current}
            if len(encode(dynamic).encode()) > MAX_DYNAMIC_BYTES:
                raise ValueError("Author dynamic context exceeds offline spend bound")
            if saved is not None:
                replay = json.loads(saved[1])["turn"]
                if replay is None:
                    raise ExperimentIntegrityError("Author journal lacks a due turn's record")
                chosen: DecisionTransportProtocol | None = _ReplayReply(replay)
            else:
                chosen = transport
            if chosen is None:
                raise TransportUnavailableError(
                    "Author turn requires an explicitly injected transport"
                )
            turn = author_turn(request, chosen)
            progress.current = turn["rulebooks"]
            progress.turns.append(turn)
            if turn["accepted"]:
                for source in sources:
                    # At first scored close v2 replaces v1's evaluation, but
                    # the journal retains both starting and authored versions.
                    schedules[source.symbol].append(
                        {
                            "effective_from": source.payload["candles"][sequence]["ts"],
                            "rules": progress.current[source.symbol],
                        }
                    )
        events = {
            s.symbol: advance(_source(s, schedules[s.symbol]), sequence, states[s.symbol])
            for s in sources
        }
        event = {"sequence": sequence, "previous": previous, "turn": turn, "events": events}
        checksum = semantic_checksum(event)
        if saved is not None:
            if json.loads(saved[1]) != event or saved[2] != checksum:
                raise ExperimentIntegrityError("Author journal replay differs")
        else:
            connection.execute(
                "INSERT INTO bars VALUES (?, ?, ?)", (sequence, encode(event), checksum)
            )
        if write:
            connection.commit()
        for symbol, item in events.items():
            progress.events[symbol].append(item)
        states = {symbol: e["state"] for symbol, e in events.items()}
        previous = checksum
        progress.processed = sequence + 1
    return progress


def _open(root: Path, binding: dict[str, Any], *, immutable: bool) -> sqlite3.Connection:
    """Create or reopen a journal whose metadata must equal ``binding``."""
    root.mkdir(parents=True, exist_ok=True)
    database = root / DATABASE
    new = not database.exists()
    if new and any(root.iterdir()):
        raise ValueError("New author run requires an empty directory")
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (payload TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS bars (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)"
        )
        if new and immutable:
            for table in ("metadata", "bars"):
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER {table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable author evidence'); END"
                    )
        rows = connection.execute("SELECT payload FROM metadata").fetchall()
        if not rows and not new:
            raise ExperimentIntegrityError("Author journal metadata missing")
        if not rows:
            connection.execute("INSERT INTO metadata VALUES (?)", (encode(binding),))
        elif len(rows) != 1 or json.loads(rows[0][0]) != binding:
            raise ExperimentIntegrityError("Author input/settings/source/ledger binding changed")
        connection.commit()
        return connection
    except BaseException:
        connection.close()
        raise


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
    binding = {
        "schema": "author-smoke/1",
        "not_evidence": True,
        "sources": [s.identity for s in sources],
        "manager": manager.to_dict(),
        "settings": asdict(settings),
        "max_turns": max_turns,
        "ledger": _ledger_binding(transport),
    }
    first = sources[0]
    end = min(len(first.candles), first.scored_index + (max_turns - 1) * settings.cadence_bars + 1)
    with closing(_open(root, binding, immutable=False)) as connection:
        (root / "NOT-EVIDENCE.txt").write_text(
            "Plumbing smoke only. Known bars; not forward or performance evidence.\n"
        )
        progress = _journal(connection, binding, sources, settings, transport, end, write=True)
        if connection.execute("SELECT count(*) FROM bars").fetchone()[0] != end:
            raise ExperimentIntegrityError("Author journal has extra bars")
    return {
        "not_evidence": True,
        "turns": len(progress.turns),
        "accepted": sum(t["accepted"] for t in progress.turns),
        "journal": str(root / DATABASE),
        "rulebooks": progress.current,
    }


def _run_binding(
    sources: list[ExperimentInput],
    manager: ManagerSettings,
    settings: AuthorSettings,
    ledger: dict[str, str] | None,
) -> dict[str, Any]:
    return {
        "schema": RUN_SCHEMA,
        "engine": ENGINE_VERSION,
        "implementation_sha256": implementation_digest(),
        "sources": [s.identity for s in sources],
        "inputs": [s.payload for s in sources],
        "manager": manager.to_dict(),
        "settings": asdict(settings),
        "ledger": ledger,
    }


def run_author(
    sources: list[ExperimentInput],
    root: Path,
    manager: ManagerSettings,
    transport: DecisionTransportProtocol | None = None,
    *,
    settings: AuthorSettings = AuthorSettings(),
    max_bars: int | None = None,
) -> dict[str, Any]:
    """Create or resume the full-window author run of one portfolio in one cost case.

    Commits at most ``max_bars`` new bars. The journal binds every input
    payload, the source digest, the settings and the spend ledger's path and
    cap; any change refuses to resume. Committed turns replay without a call.
    """
    validate_portfolio(sources)
    if max_bars is not None and (type(max_bars) is not int or max_bars < 1):
        raise ValueError("max_bars must be a positive integer")
    sources = sorted(sources, key=lambda s: s.symbol)
    if transport is not None and transport.model != manager.model:
        raise ValueError("Transport model differs from author settings")
    binding = _run_binding(sources, manager, settings, _ledger_binding(transport))
    total = len(sources[0].candles)
    with closing(_open(root, binding, immutable=True)) as connection:
        done = connection.execute("SELECT count(*) FROM bars").fetchone()[0]
        end = total if max_bars is None else min(total, done + max_bars)
        _journal(connection, binding, sources, settings, transport, end, write=True)
    return inspect_author(root)


def _outcome(turn: dict[str, Any]) -> str:
    if turn["accepted"]:
        return "accepted"
    # No reply means the call itself failed; a reply that did not validate
    # (refused, truncated, malformed or out of range) was rejected.
    return "failed" if turn["reply"] is None else "rejected"


def inspect_author(root: Path) -> dict[str, Any]:
    """Read a full author run's journal without writing, and report every coin.

    Rebuilds each input from the journal, refuses a source digest or engine
    other than the one bound, replays every bar and turn, and reconciles each
    coin's account independently from its fills.
    """
    database = root / DATABASE
    if not database.is_file():
        raise ValueError(f"No author journal at {database}")
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("BEGIN")
        rows = connection.execute("SELECT payload FROM metadata").fetchall()
        if len(rows) != 1:
            raise ExperimentIntegrityError("Author journal metadata missing")
        saved = json.loads(rows[0][0])
        if saved.get("schema") != RUN_SCHEMA:
            raise ValueError("Only a full author run can be inspected; a smoke is not evidence")
        sources = [parse_input(payload) for payload in saved["inputs"]]
        manager = ManagerSettings(**saved["manager"])
        settings = AuthorSettings(**saved["settings"])
        binding = _run_binding(sources, manager, settings, saved["ledger"])
        if binding != saved:
            raise ExperimentIntegrityError("Experiment metadata binding failed")
        progress = _journal(connection, saved, sources, settings, None, 0, write=False)
    total = len(sources[0].candles)
    turns = progress.turns
    usage = [turn["reply"] for turn in turns if turn["reply"]]
    reports = []
    for source in sources:
        events = progress.events[source.symbol]
        scheduled = _source(source, progress.schedules[source.symbol])
        account = reconcile(scheduled, events)
        state = events[-1]["state"] if events else initial_state(source)
        reports.append(
            {
                "symbol": source.symbol,
                "identity": source.identity,
                "source": source.payload["source"],
                "benchmark": source.benchmark,
                "implementation_sha256": saved["implementation_sha256"],
                "scored_from": source.payload["scored_from"],
                "settings": source.settings.to_dict(),
                "processed_bars": len(events),
                "total_bars": total,
                "complete": len(events) == total,
                "halted": state["halted"],
                "position": state["position"],
                "account": account,
                "end_valuation": end_valuation(scheduled, events, account),
                "rulebook": {
                    "starting": source.payload["rulebooks"][0]["rules"],
                    "version": state["rulebook"]["version"],
                    "activations": [
                        event["rulebook_activation"]
                        for event in events
                        if event.get("rulebook_activation")
                    ],
                },
            }
        )
    return {
        "schema": RUN_SCHEMA,
        "journal": str(database),
        "engine": saved["engine"],
        "implementation_sha256": saved["implementation_sha256"],
        "processed_bars": progress.processed,
        "total_bars": total,
        "complete": progress.processed == total,
        "manager": saved["manager"],
        "settings": saved["settings"],
        "ledger": saved["ledger"],
        "author": {
            "turns": len(turns),
            "outcomes": {
                name: sum(_outcome(turn) == name for turn in turns)
                for name in ("accepted", "rejected", "failed")
            },
            "models": sorted({turn["reply"]["model"] for turn in turns if turn["reply"]}),
            "input_tokens": sum(item["input_tokens"] for item in usage),
            "output_tokens": sum(item["output_tokens"] for item in usage),
            "cost_usd": str(sum((Decimal(item["cost_usd"]) for item in usage), Decimal(0))),
            "errors": [turn["error"] for turn in turns if turn["error"]],
        },
        "symbols": reports,
        "evidence_class": (
            "historical simulation with author turns replayed from the journal; "
            "not trading-readiness evidence"
        ),
    }
