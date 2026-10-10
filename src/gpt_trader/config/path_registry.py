"""Centralized filesystem paths for GPT-Trader runtime artifacts."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "PROJECT_ROOT",
    "VAR_DIR",
    "LOG_DIR",
    "RESULTS_DIR",
    "RUNTIME_DATA_DIR",
    "COINBASE_TRADER_RUNTIME_DIR",
    "DEFAULT_EVENT_STORE_DIR",
    "USER_CONFIG_DIR",
    "USER_SECRETS_DIR",
    "ensure_directories",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _project_root()
VAR_DIR = PROJECT_ROOT / "var"
LOG_DIR = VAR_DIR / "logs"
RESULTS_DIR = VAR_DIR / "results"
RUNTIME_DATA_DIR = PROJECT_ROOT / "runtime_data"
COINBASE_TRADER_RUNTIME_DIR = RUNTIME_DATA_DIR
DEFAULT_EVENT_STORE_DIR = COINBASE_TRADER_RUNTIME_DIR / "shared"

# User-global artifacts are explicit exceptions to repo-local runtime ownership.
# Do not add them to ensure_directories(); callers create them only when needed.
USER_CONFIG_DIR = Path.home() / ".gpt_trader"
USER_SECRETS_DIR = USER_CONFIG_DIR / "secrets"


def ensure_directories(paths: Iterable[Path] | None = None) -> None:
    targets = list(
        paths
        or (
            VAR_DIR,
            LOG_DIR,
            RESULTS_DIR,
            RUNTIME_DATA_DIR,
            COINBASE_TRADER_RUNTIME_DIR,
        )
    )
    for path in targets:
        path.mkdir(parents=True, exist_ok=True)
