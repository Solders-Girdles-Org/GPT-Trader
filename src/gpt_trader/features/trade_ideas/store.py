"""Versioned persistence for trade-idea records.

Every saved version is kept under its record hash so audit events can always
be resolved back to the exact record content they acted on; ``latest.json``
tracks the current version for convenience.
"""

from __future__ import annotations

import json
from pathlib import Path

from gpt_trader.features.trade_ideas.models import TradeIdea
from gpt_trader.features.trade_ideas.persistence import StateRepository


class TradeIdeaStore:
    """Filesystem store: one directory per decision, one file per version."""

    def __init__(self, root: Path, *, repository: StateRepository | None = None) -> None:
        self._root = root
        self._repository = repository or StateRepository(root.parent)

    @property
    def root(self) -> Path:
        return self._root

    def _decision_dir(self, decision_id: str) -> Path:
        return self._root / decision_id

    def exists(self, decision_id: str) -> bool:
        """Return whether a latest record already exists for this decision."""
        if self._repository.active:
            return self._repository.record(decision_id) is not None
        return (self._decision_dir(decision_id) / "latest.json").exists()

    def save(self, idea: TradeIdea) -> str:
        """Persist a record version; returns its record hash."""
        record_hash = idea.record_hash()
        if self._repository.active:
            self._repository.save_record(
                idea.decision_id, record_hash, json.dumps(idea.to_dict(), sort_keys=True, indent=2)
            )
            return record_hash
        directory = self._decision_dir(idea.decision_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(idea.to_dict(), sort_keys=True, indent=2)
        (directory / f"{record_hash}.json").write_text(payload, encoding="utf-8")
        (directory / "latest.json").write_text(payload, encoding="utf-8")
        return record_hash

    def load_latest(self, decision_id: str) -> TradeIdea | None:
        if self._repository.active:
            payload = self._repository.record(decision_id)
            return TradeIdea.from_dict(json.loads(payload)) if payload is not None else None
        path = self._decision_dir(decision_id) / "latest.json"
        if not path.exists():
            return None
        return TradeIdea.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def load_version(self, decision_id: str, record_hash: str) -> TradeIdea | None:
        if self._repository.active:
            payload = self._repository.record(decision_id, record_hash)
            return TradeIdea.from_dict(json.loads(payload)) if payload is not None else None
        path = self._decision_dir(decision_id) / f"{record_hash}.json"
        if not path.exists():
            return None
        return TradeIdea.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_decision_ids(self) -> list[str]:
        if self._repository.active:
            return self._repository.records()
        if not self._root.exists():
            return []
        return sorted(
            entry.name
            for entry in self._root.iterdir()
            if entry.is_dir() and (entry / "latest.json").exists()
        )
