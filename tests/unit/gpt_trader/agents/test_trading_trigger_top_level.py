"""RJ-1143: the trigger fails closed at the top of the tree.

A path whose top-level directory or root file no one has reviewed is flagged,
as are root files that decide what other code runs. Tracked symlinks and
submodules must be named in REVIEWED_LINKS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from gpt_trader.agents import trading_trigger as tt

REPO = Path(tt.__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "path",
    [
        "newdir/x.py",
        "NewDir/run.sh",
        "run.py",
        "src/other_pkg/__init__.py",
        "src/settings.yaml",
        "docs/tools/run.py",
        "conftest.py",
        "pytest.ini",
        "pyproject.toml",
        ".coveragerc",
        ".pre-commit-config.yaml",
        # A reviewed root file's name doesn't cover a directory of that name.
        "LICENSE/run.py",
        "uv.lock/run.py",
        "src",
    ],
)
def test_unreviewed_top_level_and_run_config_paths_hit(path: str) -> None:
    assert tt.trading_paths([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        ".github/actions/setup-uv/action.yml",
        "docs/agents/new_note.md",
        "README.md",
        "newdir/README.md",
        ".gitignore",
        ".python-version",
        "LICENSE",
    ],
)
def test_reviewed_top_level_paths_miss(path: str) -> None:
    assert tt.trading_paths([path]) == []


def test_a_never_seen_path_is_flagged() -> None:
    # RJ-1143: a path in no reviewed top-level directory is flagged by default.
    paths = [f"zz_{uuid4().hex}/run.py", f"{uuid4().hex}.py", f"src/{uuid4().hex}.yaml"]
    assert tt.trading_paths(paths) == sorted(paths)


def test_no_unreviewed_symlink_or_submodule() -> None:
    assert tt.unreviewed_links(REPO) == []
    for path, reason in tt.REVIEWED_LINKS.items():
        assert (REPO / path).is_symlink() and reason.strip(), path


def test_self_check_flags_a_new_symlink(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/guide.md").symlink_to("../src/run.py")
    subprocess.run(["git", "add", "docs/guide.md"], cwd=tmp_path, check=True)
    assert tt.unreviewed_links(tmp_path) == ["docs/guide.md"]


def test_every_top_level_entry_is_reviewed_or_flagged() -> None:
    tops = {tt._top_level(f) for f in tt.tracked_files(REPO)}
    for name in tops:
        assert name in tt.TOP_LEVEL_REVIEWED or tt._flagged(name) or name.endswith(".md"), name
    assert all(reason.strip() for reason in tt.TOP_LEVEL_REVIEWED.values())
    stale = [name for name in tt.TOP_LEVEL_REVIEWED if name not in tops]
    assert stale == []
