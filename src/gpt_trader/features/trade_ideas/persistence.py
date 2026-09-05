"""One local transaction boundary for records, workflow and admission controls.

Legacy files are import/export contracts, never a second writable projection.
Reads of an unmigrated root remain available; its first write requires migration.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from functools import wraps
from pathlib import Path
from threading import local
from typing import Any, ParamSpec, TypeVar, cast

from gpt_trader.errors import ValidationError

DATABASE_NAME = "trade_state.sqlite3"
LOG_NAMES = (
    "audit.jsonl",
    "risk_budget.jsonl",
    "autonomy_state.jsonl",
    "closeout_attributions.jsonl",
)
P = ParamSpec("P")
R = TypeVar("R")


class StateMigrationRequired(ValidationError):
    """Legacy state must be imported with its writers quiesced before mutation."""


class StateRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / DATABASE_NAME
        self._local = local()

    @property
    def active(self) -> bool:
        return self.path.exists()

    @property
    def connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            raise RuntimeError("State access requires a transaction")
        return cast(sqlite3.Connection, connection)

    def legacy_present(self) -> bool:
        return (
            any((self.root / name).exists() for name in LOG_NAMES)
            or (self.root / "records").exists()
        )

    def initialize(self) -> None:
        if self.active:
            return
        if self.legacy_present():
            raise StateMigrationRequired(
                "Legacy trade state requires validated migration before writes; quiesce all writers "
                "and use python -m gpt_trader.features.trade_ideas.migration import.",
                field="root",
                value=str(self.root),
            )
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=".trade-state-", suffix=".sqlite3", dir=self.root
        )
        os.close(descriptor)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary, timeout=30)) as connection:
                connection.executescript(SCHEMA)
            try:
                os.link(temporary, self.path)
            except FileExistsError:
                pass  # Another initializer published a complete schema first.
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[None]:
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            if write and not self._local.write:
                raise RuntimeError("Cannot promote a read snapshot to a write transaction")
            yield
            return
        if write:
            self.initialize()
        if not self.active:
            yield  # read-only compatibility, including genuinely empty roots
            return
        uri = self.path.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro")
        connection = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != 1:
                raise RuntimeError(f"Unsupported trade state schema: {version}")
            self._local.connection = connection
            self._local.write = write
            yield
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._local.connection = None
            connection.close()

    def records(self) -> list[str]:
        with self.transaction():
            return [
                row[0]
                for row in self.connection.execute(
                    "SELECT decision_id FROM latest ORDER BY decision_id"
                )
            ]

    def denial_autonomy_is_safe(self, before: list[str]) -> bool:
        after = self.lines("autonomy_state.jsonl")
        if after[: len(before)] != before:
            return False
        if len(after) == len(before):
            return True
        ranks = {"research_only": 0, "human_approved_execution": 1, "bounded_autonomy": 2}
        try:
            prior_rank = ranks[json.loads(before[-1])["mode"]] if before else 1
            for line in after[len(before) :]:
                entry = json.loads(line)
                rank = ranks[entry["mode"]]
                if entry["actor_type"] != "system" or rank > prior_rank:
                    return False
                prior_rank = rank
        except (ValueError, KeyError, TypeError):
            return False
        return True

    def admission_revision(self) -> tuple[object, ...]:
        """Detect writes outside the autonomy log before allowing denial to commit."""
        connection = self.connection
        return (
            tuple(
                connection.execute(
                    "SELECT decision_id,record_hash FROM latest ORDER BY decision_id"
                )
            ),
            connection.execute("SELECT count(*) FROM versions").fetchone()[0],
            tuple(
                connection.execute(
                    "SELECT sequence FROM events WHERE stream != 'autonomy_state.jsonl' ORDER BY sequence"
                )
            ),
        )

    def latest_hash(self, decision_id: str) -> str | None:
        with self.transaction():
            row = self.connection.execute(
                "SELECT record_hash FROM latest WHERE decision_id=?", (decision_id,)
            ).fetchone()
            return row[0] if row else None

    def record(self, decision_id: str, record_hash: str | None = None) -> str | None:
        with self.transaction():
            if record_hash is None:
                row = self.connection.execute(
                    "SELECT r.payload FROM versions r JOIN latest l USING(decision_id,record_hash) WHERE l.decision_id=?",
                    (decision_id,),
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT payload FROM versions WHERE decision_id=? AND record_hash=?",
                    (decision_id, record_hash),
                ).fetchone()
            return row[0] if row else None

    def save_record(self, decision_id: str, record_hash: str, payload: str) -> None:
        with self.transaction(write=True):
            prior = self.connection.execute(
                "SELECT payload FROM versions WHERE decision_id=? AND record_hash=?",
                (decision_id, record_hash),
            ).fetchone()
            if prior is not None and json.loads(prior[0]) != json.loads(payload):
                raise ValueError("Record hash collision or changed immutable version")
            self.connection.execute(
                "INSERT OR IGNORE INTO versions VALUES(?,?,?)", (decision_id, record_hash, payload)
            )
            self.connection.execute(
                "INSERT INTO latest VALUES(?,?) ON CONFLICT(decision_id) DO UPDATE SET record_hash=excluded.record_hash",
                (decision_id, record_hash),
            )

    def lines(self, stream: str, decision_id: str | None = None) -> list[str]:
        with self.transaction():
            query = "SELECT payload FROM events WHERE stream=?"
            args: tuple[str, ...] = (stream,)
            if decision_id is not None:
                query += " AND decision_id=?"
                args += (decision_id,)
            return [row[0] for row in self.connection.execute(query + " ORDER BY sequence", args)]

    def append(self, stream: str, payload: str) -> None:
        value = json.loads(payload)
        with self.transaction(write=True):
            self.connection.execute(
                "INSERT INTO events(stream,decision_id,payload) VALUES(?,?,?)",
                (stream, value.get("decision_id"), payload),
            )


SCHEMA = """

CREATE TABLE IF NOT EXISTS versions(decision_id TEXT NOT NULL, record_hash TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(decision_id,record_hash));
CREATE TABLE IF NOT EXISTS latest(decision_id TEXT PRIMARY KEY, record_hash TEXT NOT NULL, FOREIGN KEY(decision_id,record_hash) REFERENCES versions(decision_id,record_hash));
CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY, stream TEXT NOT NULL, decision_id TEXT, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_decision ON events(stream,decision_id,sequence);
CREATE TRIGGER IF NOT EXISTS immutable_versions_update BEFORE UPDATE ON versions BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_versions_delete BEFORE DELETE ON versions BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END;
CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END;
CREATE TABLE IF NOT EXISTS import_files(path TEXT PRIMARY KEY, payload BLOB NOT NULL);
PRAGMA user_version=1;
"""


def state_transaction(
    *, write: bool = False, commit_denial: bool = False
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Wrap a service operation; nested domain calls share the owning transaction."""

    def decorate(method: Callable[P, R]) -> Callable[P, R]:
        @wraps(method)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            owner: Any = args[0]
            # A denied admission can still commit a mandatory, audited down-ratchet.
            # This option is confined to decision operations, never proposal batches.
            from gpt_trader.features.trade_ideas.policy import PolicyViolationError

            failure: PolicyViolationError | None = None
            with owner._repository.transaction(write=write):
                before = owner._repository.admission_revision() if commit_denial else None
                autonomy_before = (
                    owner._repository.lines("autonomy_state.jsonl") if commit_denial else []
                )
                try:
                    return method(*args, **kwargs)
                except PolicyViolationError as error:
                    if (
                        not commit_denial
                        or owner._repository.admission_revision() != before
                        or not owner._repository.denial_autonomy_is_safe(autonomy_before)
                    ):
                        raise
                    failure = error
            assert failure is not None
            raise failure

        return wrapped

    return decorate
