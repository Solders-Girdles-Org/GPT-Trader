"""Offline, validated import and current-state export; never changes a source root.

An owner must quiesce all writers before selecting the destination as ideas root.
The importer cannot infer that an old process will not resume writing later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from gpt_trader.features.trade_ideas.models import TradeIdea
from gpt_trader.features.trade_ideas.persistence import LOG_NAMES, StateRepository
from gpt_trader.features.trade_ideas.service import TradeIdeaService


def legacy_files(root: Path) -> dict[str, bytes]:
    paths = [root / name for name in LOG_NAMES if (root / name).exists()]
    paths.extend((root / "records").glob("*/*.json"))
    if any(
        path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) for path in paths
    ):
        raise ValueError("Migration refuses symlinked or escaping state files")
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(paths)}


def digest(files: dict[str, bytes]) -> str:
    return hashlib.sha256(
        json.dumps(
            {key: hashlib.sha256(value).hexdigest() for key, value in sorted(files.items())},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def validate(root: Path) -> dict[str, int]:
    service = TradeIdeaService(root)
    repository = service._repository
    with repository.transaction():
        events = service.audit_log.verify()
        for event in events:
            service.load_record_version(event.decision_id, event.record_hash)
        views = service.list_views()
        for event in events:
            service.get(event.decision_id)
        budgets = service.budget_log.history()
        autonomy = service.autonomy_history()
        closeouts = service.closeout_log.read_records()
        for closeout in closeouts:
            service.load_record_version(closeout.decision_id, closeout.record_hash)
        checked_closeouts = service.query_closeout_records()
        if checked_closeouts.total_count != len(closeouts):
            raise ValueError("Closeout records include orphaned or duplicate attribution")
        if repository.active:
            if repository.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            if repository.connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("SQLite foreign key check failed")
        return {
            "ideas": len(views),
            "events": len(events),
            "budgets": len(budgets),
            "autonomy": len(autonomy),
            "closeouts": len(closeouts),
        }


def import_legacy(source: Path, destination: Path) -> dict[str, object]:
    if destination.exists():
        raise FileExistsError("Import destination must not exist")
    if StateRepository(source).active:
        raise ValueError("Source is already a SQLite store; use export first")
    before = legacy_files(source)
    expected = validate(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".trade-state-import-", dir=destination.parent))
    try:
        repository = StateRepository(scratch)
        with repository.transaction(write=True):
            connection = repository.connection
            for relative, payload in before.items():
                connection.execute("INSERT INTO import_files VALUES(?,?)", (relative, payload))
                if relative in LOG_NAMES:
                    for line in payload.decode().splitlines():
                        if line.strip():
                            repository.append(relative, line)
                elif relative.startswith("records/"):
                    path = Path(relative)
                    idea = TradeIdea.from_dict(json.loads(payload))
                    if idea.decision_id != path.parent.name:
                        raise ValueError(f"Record identity mismatch: {relative}")
                    record_hash = idea.record_hash()
                    if path.stem != "latest" and path.stem != record_hash:
                        raise ValueError(f"Record hash mismatch: {relative}")
                    connection.execute(
                        "INSERT OR IGNORE INTO versions VALUES(?,?,?)",
                        (idea.decision_id, record_hash, payload.decode()),
                    )
            for relative, payload in before.items():
                if relative.endswith("/latest.json"):
                    idea = TradeIdea.from_dict(json.loads(payload))
                    connection.execute(
                        "INSERT INTO latest VALUES(?,?)", (idea.decision_id, idea.record_hash())
                    )
        actual = validate(scratch)
        if actual != expected or legacy_files(source) != before:
            raise ValueError("Legacy generation changed during migration or parity failed")
        scratch.rename(destination)
        return {"source_digest": digest(before), "files": len(before), "counts": actual}
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def export_current(source: Path, destination: Path) -> dict[str, object]:
    if destination.exists():
        raise FileExistsError("Export destination must not exist")
    repository = StateRepository(source)
    if not repository.active:
        raise ValueError("Source has no SQLite trade state")
    destination.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".trade-state-export-", dir=destination.parent))
    try:
        with repository.transaction():
            # Validate and materialize one snapshot, including all writes since import.
            # A second repository connection would see a different generation.
            baseline = dict(repository.connection.execute("SELECT path,payload FROM import_files"))
            files: dict[str, bytes] = {}
            for decision_id, record_hash, payload in repository.connection.execute(
                "SELECT * FROM versions"
            ):
                idea = TradeIdea.from_dict(json.loads(payload))
                if idea.decision_id != decision_id or idea.record_hash() != record_hash:
                    raise ValueError("Export refused: record identity/hash mismatch")
                files[f"records/{decision_id}/{record_hash}.json"] = payload.encode()
            for decision_id, payload in repository.connection.execute(
                "SELECT l.decision_id,v.payload FROM latest l JOIN versions v USING(decision_id,record_hash)"
            ):
                idea = TradeIdea.from_dict(json.loads(payload))
                if idea.decision_id != decision_id:
                    raise ValueError("Export refused: current record identity mismatch")
                files[f"records/{decision_id}/latest.json"] = payload.encode()
            for stream in LOG_NAMES:
                lines = repository.lines(stream)
                if lines or stream in baseline:
                    files[stream] = (("\n".join(lines) + "\n") if lines else "").encode()
            for relative, payload in files.items():
                # Preserve exact imported bytes when the content has not changed.
                original = baseline.get(relative)
                if original is not None:
                    parse = (
                        (
                            lambda value: [
                                json.loads(line) for line in value.splitlines() if line.strip()
                            ]
                        )
                        if relative in LOG_NAMES
                        else json.loads
                    )
                    if parse(payload) == parse(original):
                        payload = original
                path = scratch / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
        counts = validate(scratch)
        actual = legacy_files(scratch)
        scratch.rename(destination)
        return {"digest": digest(actual), "files": len(actual), "counts": counts}
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("import", "export", "validate"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    args = parser.parse_args()
    result: dict[str, object]
    if args.operation == "validate":
        result = dict(validate(args.source))
    elif args.destination is None:
        parser.error("import/export requires a new destination")
    elif args.operation == "import":
        result = import_legacy(args.source, args.destination)
    else:
        result = export_current(args.source, args.destination)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
