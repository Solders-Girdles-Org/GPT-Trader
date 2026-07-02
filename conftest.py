"""Top-level pytest plugin registration."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

# Keep repo-root scripts/ (CLI helpers like scripts/maintenance/test_legacy_triage.py)
# out of collection without a basename pattern in norecursedirs, which would also
# shadow tests/unit/scripts and tests/integration/scripts (see pytest.ini).
collect_ignore = ["scripts"]


def pytest_configure(config: Any) -> None:  # pragma: no cover
    """Register optional warning filters.

    Avoids hard dependencies in `pytest.ini` warning categories (which are imported eagerly
    and can fail when a dev-only dependency isn't installed).
    """
    try:
        from pytest_benchmark.logger import PytestBenchmarkWarning
    except Exception:
        return

    warnings.filterwarnings("ignore", category=PytestBenchmarkWarning)


def _load_legacy_test_triage(rootpath: Path) -> dict[str, dict[str, Any]]:
    triage_path = rootpath / "tests" / "_triage" / "legacy_tests.yaml"
    if not triage_path.exists():
        return {}
    try:
        import yaml
    except Exception:
        return {}

    try:
        payload = yaml.safe_load(triage_path.read_text()) or {}
    except Exception:
        return {}

    tests = payload.get("tests")
    if not isinstance(tests, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for raw_path, entry in tests.items():
        if not isinstance(raw_path, str) or not raw_path:
            continue
        if not isinstance(entry, dict):
            continue
        normalized[raw_path.replace("\\", "/")] = dict(entry)
    return normalized


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:  # pragma: no cover
    """Apply directory + triage markers.

    - Directory markers: keep selection consistent even if a file forgets decorators
    - Triage markers: track tests slated for deletion vs modernization without editing
      each test file immediately
    """
    try:
        import pytest
    except Exception:
        return

    root = Path(str(config.rootpath)).resolve()
    triage = _load_legacy_test_triage(root)

    for item in items:
        try:
            rel = Path(str(item.fspath)).resolve().relative_to(root)
        except Exception:
            continue

        rel_path = rel.as_posix()

        if rel_path.startswith("tests/integration/"):
            item.add_marker(pytest.mark.integration)
        elif rel_path.startswith("tests/unit/"):
            item.add_marker(pytest.mark.unit)
        elif rel_path.startswith("tests/property/"):
            item.add_marker(pytest.mark.property)
        elif rel_path.startswith("tests/contract/"):
            item.add_marker(pytest.mark.contract)
        elif rel_path.startswith("tests/real_api/"):
            item.add_marker(pytest.mark.real_api)

        if not triage:
            continue

        entry = triage.get(rel_path)
        if not entry:
            continue

        status = str(entry.get("status") or "").strip().lower()
        if status in {"done", "completed"}:
            continue

        action = entry.get("action") or ""
        action = str(action).strip().lower()
        if not action:
            legacy_action = str(entry.get("status") or "").strip().lower()
            if legacy_action in {"delete", "modernize"}:
                action = legacy_action
        if action == "delete":
            item.add_marker(pytest.mark.legacy_delete)
        elif action == "modernize":
            item.add_marker(pytest.mark.legacy_modernize)
