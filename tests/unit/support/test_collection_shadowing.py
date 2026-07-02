"""Meta-guard: no test-bearing directory may be shadowed from collection.

``norecursedirs`` patterns match directory *basenames* anywhere in the tree, so
a pattern meant to exclude a repo-root directory (e.g. ``scripts``) also stops
pytest from recursing into a same-named directory under ``tests/`` (e.g.
``tests/unit/scripts``). ``pytest tests/unit`` then skips those tests entirely
while an explicit ``pytest tests/unit/scripts`` still collects them, so a
required CI lane can false-pass on code its own guard tests reject (PR #1145:
the Linux core lane reported green while the Windows lane failed
``test_cross_slice_allowlist_is_frozen_topology`` on the same commit).

This guard protects every directory under ``tests/`` except its own ancestors:
if ``tests``, ``unit``, or ``support`` were ever added to ``norecursedirs``,
the guard itself would be shadowed and could not fire. It lives in
``tests/unit/support/`` because the test-hygiene gate requires that layout;
do not move it into ``tests/unit/scripts/``, the directory it exists to
protect.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest


def _contains_test_files(directory: Path, file_patterns: list[str]) -> bool:
    return any(
        path.is_file() and any(fnmatch.fnmatch(path.name, pattern) for pattern in file_patterns)
        for path in directory.rglob("*")
    )


def test_no_test_directory_matches_norecursedirs(pytestconfig: pytest.Config) -> None:
    dir_patterns = pytestconfig.getini("norecursedirs")
    file_patterns = pytestconfig.getini("python_files")
    tests_root = pytestconfig.rootpath / "tests"
    assert tests_root.is_dir()

    shadowed = sorted(
        str(path.relative_to(pytestconfig.rootpath))
        for path in tests_root.rglob("*")
        if path.is_dir()
        and any(fnmatch.fnmatch(path.name, pattern) for pattern in dir_patterns)
        and _contains_test_files(path, file_patterns)
    )

    assert not shadowed, (
        "norecursedirs patterns "
        f"{dir_patterns!r} shadow test directories {shadowed} from recursive "
        "collection: `pytest tests/...` silently skips them while explicit-path "
        "lanes still run them. Rename the directory or narrow the pattern "
        "(repo-root dirs can be excluded via collect_ignore in the root "
        "conftest.py instead)."
    )
