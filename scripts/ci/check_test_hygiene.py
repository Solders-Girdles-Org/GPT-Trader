#!/usr/bin/env python3
"""Lightweight guardrails for test suite hygiene."""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from collections.abc import Sequence

# Repository root, derived from this file's location so allowlist existence
# checks stay correct even when scan() is pointed at a synthetic root (tests).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Increased from 240 to 400 to accommodate comprehensive test coverage.
# Comprehensive test files (e.g., testing all validation rules in a CI script)
# often require more lines to cover all edge cases properly.
# See docs/test_hygiene.md for the threshold/allowlist policy.
THRESHOLD = 400
UNIT_ALLOWED_PREFIXES = (
    "tests/unit/gpt_trader/",
    "tests/unit/scripts/",
    "tests/unit/support/",
)
INTEGRATION_TEST_PREFIX = "tests/integration/"
CONTRACT_TEST_PREFIX = "tests/contract/"
REAL_API_TEST_PREFIX = "tests/real_api/"
# Files exempt from the THRESHOLD line cap. Each entry is self-policing: once a
# file drops to <= THRESHOLD lines it must be removed from this map (CI enforces).
SIZE_ALLOWLIST_REASONS: dict[str, str] = {}
SIZE_ALLOWLIST: set[str] = set(SIZE_ALLOWLIST_REASONS)

# Unit-test files that legitimately contain `.mark.integration/contract/real_api`
# as string literals (e.g. tests that exercise this very hygiene checker) and so
# must not be reclassified out of `tests/unit/`.
MARKER_LITERAL_ALLOWLIST: dict[str, str] = {
    "tests/unit/scripts/test_check_test_hygiene.py": (
        "Tests this hygiene checker; embeds marker strings as fixtures, not real markers."
    ),
}
PATCH_ALLOWLIST: set[str] = set()

SLEEP_ALLOWLIST: set[str] = set()


def _is_patch_callable(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "patch"
    if isinstance(node, ast.Attribute):
        if node.attr == "patch":
            return True
        if node.attr in {"dict", "multiple", "object"}:
            return _is_patch_callable(node.value)
    return False


def _first_patch_call_line(text: str) -> int | None:
    if "patch" not in text:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_patch_callable(node.func):
            return getattr(node, "lineno", None)
    return None


def _has_time_sleep_call(text: str) -> bool:
    """Return True if the file contains a real time.sleep(...) call.

    This intentionally ignores occurrences inside strings/comments.
    """
    if "sleep" not in text:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "sleep":
            # time.sleep(...)
            if isinstance(func.value, ast.Name) and func.value.id == "time":
                return True
    return False


def scan(paths: Sequence[str]) -> int:
    root = pathlib.Path.cwd()
    test_files: list[pathlib.Path] = []
    python_files: list[pathlib.Path] = []
    if paths:
        for entry in paths:
            p = pathlib.Path(entry)
            if p.is_dir():
                test_files.extend(p.rglob("test_*.py"))
                python_files.extend(p.rglob("*.py"))
            elif p.name.startswith("test_") and p.suffix == ".py":
                test_files.append(p)
                python_files.append(p)
    else:
        test_files = list(pathlib.Path("tests").rglob("test_*.py"))
        python_files = list(pathlib.Path("tests").rglob("*.py"))

    problems: list[str] = []

    normalized = [path.resolve() for path in test_files]
    python_normalized = sorted({path.resolve() for path in python_files})

    # Track line counts for allowlisted files so the size allowlist stays honest:
    # an entry that no longer exceeds THRESHOLD (e.g. after a split) must be removed.
    size_allowlist_line_counts: dict[str, int] = {}

    for path in normalized:
        rel = path.relative_to(root)
        rel_str = rel.as_posix()
        text = path.read_text(encoding="utf-8")
        if not text:
            line_count = 0
        else:
            line_count = text.count("\n")
            if not text.endswith("\n"):
                line_count += 1

        if rel_str.startswith("tests/unit/") and not rel_str.startswith(UNIT_ALLOWED_PREFIXES):
            problems.append(
                f"{rel} is a unit test outside the supported layout. Place unit tests under `tests/unit/gpt_trader/` (or `tests/unit/scripts/`, `tests/unit/support/`)."
            )

        if rel_str.startswith(INTEGRATION_TEST_PREFIX) and ".mark.integration" not in text:
            problems.append(
                f"{rel} is under `tests/integration/` but is missing the `pytest.mark.integration` marker."
            )

        if rel_str.startswith(CONTRACT_TEST_PREFIX) and ".mark.contract" not in text:
            problems.append(
                f"{rel} is under `tests/contract/` but is missing the `pytest.mark.contract` marker."
            )

        if rel_str.startswith(REAL_API_TEST_PREFIX) and ".mark.real_api" not in text:
            problems.append(
                f"{rel} is under `tests/real_api/` but is missing the `pytest.mark.real_api` marker."
            )

        if (
            rel_str.startswith("tests/unit/")
            and (
                ".mark.integration" in text or ".mark.contract" in text or ".mark.real_api" in text
            )
            and rel_str not in MARKER_LITERAL_ALLOWLIST
        ):
            problems.append(
                f"{rel} is under `tests/unit/` but is marked integration/contract/real_api. Reclassify it under `tests/integration/`, `tests/contract/`, or `tests/real_api/`."
            )

        if rel_str.startswith(INTEGRATION_TEST_PREFIX) and ".mark.real_api" in text:
            problems.append(
                f"{rel} is under `tests/integration/` but is marked real_api. Move it to `tests/real_api/`."
            )

        if rel_str in SIZE_ALLOWLIST:
            size_allowlist_line_counts[rel_str] = line_count

        if line_count > THRESHOLD and rel_str not in SIZE_ALLOWLIST:
            problems.append(
                f"{rel} exceeds {THRESHOLD} lines ({line_count}). Split into smaller modules or add to the allowlist with justification."
            )

        if (
            _has_time_sleep_call(text)
            and "fake_clock" not in text
            and rel_str not in SLEEP_ALLOWLIST
        ):
            problems.append(
                f"{rel} calls time.sleep without using fake_clock fixture. Use fake_clock or justify with an explicit helper."
            )

    # Keep the size allowlist from accumulating stale exemptions. An entry is
    # stale if its file dropped to <= THRESHOLD lines (caught via the scanned
    # line counts) or if the file was deleted/renamed (never scanned, so checked
    # against _REPO_ROOT — stable even when scan() runs against a synthetic root).
    for entry, seen in sorted(size_allowlist_line_counts.items()):
        if seen <= THRESHOLD:
            problems.append(
                f"{entry} is on the size allowlist but is now {seen} lines (<= {THRESHOLD}). "
                "Remove the stale allowlist entry."
            )
    for entry in sorted(SIZE_ALLOWLIST - set(size_allowlist_line_counts)):
        if not (_REPO_ROOT / entry).exists():
            problems.append(
                f"{entry} is on the size allowlist but no longer exists. "
                "Remove the stale allowlist entry."
            )

    for path in python_normalized:
        rel = path.relative_to(root)
        rel_str = rel.as_posix()
        if not rel_str.startswith("tests/") or rel_str in PATCH_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        patch_line = _first_patch_call_line(text)
        if patch_line is not None:
            problems.append(
                f"{rel}:{patch_line} uses patch(...). Prefer pytest's monkeypatch fixture to avoid stacked patching."
            )

    if problems:
        print("Test hygiene issues detected:\n", file=sys.stderr)
        for item in problems:
            print(f"- {item}", file=sys.stderr)
        return 1

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check test hygiene constraints.")
    parser.add_argument("paths", nargs="*", help="Optional subset of files or directories to scan.")
    args = parser.parse_args(argv)
    return scan(args.paths)


if __name__ == "__main__":
    raise SystemExit(main())
