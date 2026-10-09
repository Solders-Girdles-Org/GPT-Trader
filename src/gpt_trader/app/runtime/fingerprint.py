"""Helpers for computing, persisting, and comparing startup configuration fingerprints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gpt_trader.app.config.bot_config import BotConfig
from gpt_trader.app.runtime.settings import (
    RuntimeSettingsSnapshot,
    ensure_runtime_settings_snapshot,
)


@dataclass(frozen=True)
class StartupConfigFingerprint:
    """Immutable fingerprint payload describing the startup configuration."""

    digest: str
    payload: dict[str, Any]


def compute_startup_config_fingerprint(
    config_or_snapshot: BotConfig | RuntimeSettingsSnapshot,
) -> StartupConfigFingerprint:
    """Return a deterministic fingerprint for the provided configuration."""

    snapshot = ensure_runtime_settings_snapshot(config_or_snapshot)
    serialized = snapshot.serialize()
    payload = json.loads(serialized)["config"]
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return StartupConfigFingerprint(digest=digest, payload=payload)


def compare_startup_config_fingerprints(
    expected: StartupConfigFingerprint | None,
    actual: StartupConfigFingerprint | None,
) -> tuple[bool, str]:
    """Compare two fingerprints and return (match, reason)."""

    if expected is None:
        return False, "expected fingerprint missing"
    if actual is None:
        return False, "runtime fingerprint missing"
    if expected.digest == actual.digest:
        return True, "ok"
    return (
        False,
        ("config fingerprint mismatch: " f"expected={expected.digest} actual={actual.digest}"),
    )


def write_startup_config_fingerprint(path: Path, fingerprint: StartupConfigFingerprint) -> None:
    """Persist the fingerprint payload for later validation."""

    payload = {"digest": fingerprint.digest, "payload": fingerprint.payload}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, default=str)


def load_startup_config_fingerprint(path: Path) -> StartupConfigFingerprint | None:
    """Load a persisted fingerprint, returning None on failure."""

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    digest = data.get("digest")
    payload = data.get("payload")
    if not isinstance(digest, str) or not isinstance(payload, dict):
        return None
    return StartupConfigFingerprint(digest=digest, payload=payload)


__all__ = [
    "StartupConfigFingerprint",
    "compute_startup_config_fingerprint",
    "compare_startup_config_fingerprints",
    "write_startup_config_fingerprint",
    "load_startup_config_fingerprint",
]
