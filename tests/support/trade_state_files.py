"""Inspect legacy logical payloads and inject corruption into disposable test stores.

These helpers are deliberately test-only: supported application writes never
rewrite immutable events or historical record versions.
"""

from __future__ import annotations

import json
from pathlib import Path

from gpt_trader.features.trade_ideas.persistence import LOG_NAMES, StateRepository


def _repository(path: Path) -> StateRepository:
    return StateRepository(path.parents[2] if path.parent.parent.name == "records" else path.parent)


def read_state_file(path: Path, **kwargs: object) -> str:
    repository = _repository(path)
    if not repository.active:
        return path.read_text(encoding="utf-8")
    if path.name in LOG_NAMES:
        lines = repository.lines(path.name)
        return "\n".join(lines) + ("\n" if lines else "")
    payload = repository.record(path.parent.name, None if path.stem == "latest" else path.stem)
    if payload is None:
        raise FileNotFoundError(path)
    return payload


def state_file_exists(path: Path) -> bool:
    repository = _repository(path)
    if not repository.active:
        return path.exists()
    if path.name in LOG_NAMES:
        return bool(repository.lines(path.name))
    if path.parent.parent.name == "records":
        return (
            repository.record(path.parent.name, None if path.stem == "latest" else path.stem)
            is not None
        )
    return path.exists()


def corrupt_state_file(path: Path, payload: str, **kwargs: object) -> None:
    repository = _repository(path)
    if not repository.active:
        path.write_text(payload, encoding="utf-8")
        return
    with repository.transaction(write=True):
        connection = repository.connection
        if path.name in LOG_NAMES:
            connection.execute("DROP TRIGGER immutable_events_delete")
            connection.execute("DELETE FROM events WHERE stream=?", (path.name,))
            connection.execute(
                "CREATE TRIGGER immutable_events_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END"
            )
            for line in payload.splitlines():
                try:
                    decision = json.loads(line).get("decision_id")
                except (ValueError, AttributeError):
                    decision = None
                connection.execute(
                    "INSERT INTO events(stream,decision_id,payload) VALUES(?,?,?)",
                    (path.name, decision, line),
                )
        else:
            connection.execute("DROP TRIGGER immutable_versions_update")
            record_hash = path.stem
            if record_hash == "latest":
                record_hash = connection.execute(
                    "SELECT record_hash FROM latest WHERE decision_id=?", (path.parent.name,)
                ).fetchone()[0]
            connection.execute(
                "UPDATE versions SET payload=? WHERE decision_id=? AND record_hash=?",
                (payload, path.parent.name, record_hash),
            )
            connection.execute(
                "CREATE TRIGGER immutable_versions_update BEFORE UPDATE ON versions BEGIN SELECT RAISE(ABORT,'immutable record'); END"
            )
