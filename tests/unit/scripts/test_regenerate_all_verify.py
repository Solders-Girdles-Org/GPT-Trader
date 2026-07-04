from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from scripts.agents import regenerate_all

SAMPLE_GENERATORS: list[tuple[str, str, str]] = [
    ("fake_schema.py", "schemas", "Fake schema"),
    ("fake_testing.py", "testing", "Fake testing"),
]


def _ensure_committed_dirs(base: Path, generators: Sequence[tuple[str, str, str]]) -> None:
    for _, output_dir, _ in generators:
        (base / output_dir).mkdir(parents=True, exist_ok=True)


def _patch_git_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(regenerate_all.shutil, "which", lambda _: "/usr/bin/git")


def _patch_regenerate_all(monkeypatch: pytest.MonkeyPatch, success: bool = True) -> None:
    def fake_regenerate_all(
        *,
        verbose: bool,
        generators: Sequence[tuple[str, str, str]],
        output_root: Path,
        reasoning_validate: bool,
        reasoning_strict: bool,
    ) -> tuple[list[regenerate_all.GeneratorResult], bool]:
        for _, output_dir, _ in generators:
            (output_root / output_dir).mkdir(parents=True, exist_ok=True)

        results = [
            regenerate_all.GeneratorResult(
                script=script_name,
                success=success,
                duration=0.1,
                output_dir=output_dir,
                error="" if success else "generator failure",
            )
            for script_name, output_dir, _ in generators
        ]
        return results, success

    monkeypatch.setattr(regenerate_all, "regenerate_all", fake_regenerate_all)


def _init_git_repo(path: Path) -> None:
    if regenerate_all.shutil.which("git") is None:
        pytest.skip("git is required for regenerate_all diff tests")
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)


@pytest.mark.parametrize("generators", [SAMPLE_GENERATORS])
def test_verify_freshness_reports_all_up_to_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    generators: Sequence[tuple[str, str, str]],
) -> None:
    var_root = tmp_path / "var_agents"
    monkeypatch.setattr(regenerate_all, "VAR_AGENTS_DIR", var_root)
    _ensure_committed_dirs(var_root, generators)

    _patch_git_presence(monkeypatch)
    _patch_regenerate_all(monkeypatch, success=True)
    monkeypatch.setattr(regenerate_all, "_diff_directories", lambda *_: None)

    result = regenerate_all.verify_freshness(generators=generators)

    assert result == 0
    captured = capsys.readouterr()
    assert "All context files are up-to-date." in captured.err


def test_verify_freshness_reports_stale_and_limits_diff_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    generators = SAMPLE_GENERATORS
    var_root = tmp_path / "var_agents"
    monkeypatch.setattr(regenerate_all, "VAR_AGENTS_DIR", var_root)
    _ensure_committed_dirs(var_root, generators)

    _patch_git_presence(monkeypatch)
    _patch_regenerate_all(monkeypatch, success=True)

    diff_calls: list[str] = []

    def fake_diff(committed: Path, generated: Path) -> str | None:
        diff_calls.append(committed.name)
        return "diff output" if committed.name == generators[0][1] else None

    monkeypatch.setattr(regenerate_all, "_diff_directories", fake_diff)

    result = regenerate_all.verify_freshness(generators=generators)

    assert result == 1
    assert diff_calls == [generators[0][1], generators[1][1]]

    captured = capsys.readouterr()
    assert "STALE: Agent context files have changed!" in captured.err
    assert "Run 'uv run agent-regenerate' and commit the changes." in captured.err
    assert "diff output" in captured.err


def test_verify_freshness_bails_on_generator_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    generators = SAMPLE_GENERATORS
    var_root = tmp_path / "var_agents"
    monkeypatch.setattr(regenerate_all, "VAR_AGENTS_DIR", var_root)
    _ensure_committed_dirs(var_root, generators)

    _patch_git_presence(monkeypatch)
    _patch_regenerate_all(monkeypatch, success=False)

    diff_called = False

    def fake_diff(_: Path, __: Path) -> str | None:
        nonlocal diff_called
        diff_called = True
        return None

    monkeypatch.setattr(regenerate_all, "_diff_directories", fake_diff)

    result = regenerate_all.verify_freshness(generators=generators)

    assert result == 1
    assert not diff_called
    captured = capsys.readouterr()
    assert "Some generators failed. Cannot verify freshness." in captured.err


def test_diff_directories_ignores_git_ignored_health_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    (repo_root / ".gitignore").write_text(
        "var/agents/health/health_report.*\n",
        encoding="utf-8",
    )

    committed_dir = repo_root / "var" / "agents" / "health"
    generated_dir = tmp_path / "generated" / "health"
    committed_dir.mkdir(parents=True)
    generated_dir.mkdir(parents=True)

    (committed_dir / "agent_health_schema.json").write_text(
        '{"schema": "same"}\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", ".gitignore", "var/agents/health/agent_health_schema.json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    (generated_dir / "agent_health_schema.json").write_text(
        '{"schema": "same"}\n',
        encoding="utf-8",
    )
    (committed_dir / "health_report.json").write_text(
        '{"status": "local"}\n',
        encoding="utf-8",
    )
    (committed_dir / "health_report.txt").write_text(
        "local report\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(regenerate_all, "PROJECT_ROOT", repo_root)

    assert regenerate_all._diff_directories(committed_dir, generated_dir) is None


def test_diff_directories_still_reports_tracked_stale_health_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    (repo_root / ".gitignore").write_text(
        "var/agents/health/health_report.*\n",
        encoding="utf-8",
    )

    committed_dir = repo_root / "var" / "agents" / "health"
    generated_dir = tmp_path / "generated" / "health"
    committed_dir.mkdir(parents=True)
    generated_dir.mkdir(parents=True)

    (committed_dir / "agent_health_schema.json").write_text(
        '{"schema": "committed"}\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", ".gitignore", "var/agents/health/agent_health_schema.json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    (generated_dir / "agent_health_schema.json").write_text(
        '{"schema": "generated"}\n',
        encoding="utf-8",
    )
    (committed_dir / "health_report.json").write_text(
        '{"status": "local"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(regenerate_all, "PROJECT_ROOT", repo_root)

    diff_text = regenerate_all._diff_directories(committed_dir, generated_dir)

    assert diff_text is not None
    assert "agent_health_schema.json" in diff_text
    assert "health_report.json" not in diff_text


def test_diff_directories_ignores_git_ignored_generated_output_absent_from_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A generated output that is git-ignored and NOT committed must not read as
    # stale even though it only exists on the freshly generated side. This
    # regression-guards un-committing large regenerated inventories: the file is
    # present on the generated side but absent from a fresh checkout.
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    (repo_root / ".gitignore").write_text(
        "var/agents/testing/test_inventory.json\n",
        encoding="utf-8",
    )

    committed_dir = repo_root / "var" / "agents" / "testing"
    generated_dir = tmp_path / "generated" / "testing"
    committed_dir.mkdir(parents=True)
    generated_dir.mkdir(parents=True)

    # Committed side: only the tracked index; test_inventory.json is uncommitted.
    (committed_dir / "index.json").write_text('{"summary": 1}\n', encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore", "var/agents/testing/index.json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    # Generated side: the same index plus the git-ignored inventory.
    (generated_dir / "index.json").write_text('{"summary": 1}\n', encoding="utf-8")
    (generated_dir / "test_inventory.json").write_text('{"big": "data"}\n', encoding="utf-8")

    monkeypatch.setattr(regenerate_all, "PROJECT_ROOT", repo_root)

    assert regenerate_all._diff_directories(committed_dir, generated_dir) is None


def test_diff_directories_tolerates_absent_committed_dir_when_all_generated_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A resource whose committed surface is entirely gitignored optional_files
    # (testing, after #1130) has NO directory on a fresh checkout. Regenerated
    # files that are all git-ignored must not read as staleness; an unignored
    # generated file still must.
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    (repo_root / ".gitignore").write_text(
        "var/agents/testing/index.json\nvar/agents/testing/markers.json\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", ".gitignore"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    committed_dir = repo_root / "var" / "agents" / "testing"
    generated_dir = tmp_path / "generated" / "testing"
    generated_dir.mkdir(parents=True)
    (generated_dir / "index.json").write_text('{"summary": 1}\n', encoding="utf-8")
    (generated_dir / "markers.json").write_text('{"markers": {}}\n', encoding="utf-8")

    monkeypatch.setattr(regenerate_all, "PROJECT_ROOT", repo_root)

    assert regenerate_all._diff_directories(committed_dir, generated_dir) is None

    # An unignored generated file keeps the missing-committed-dir failure.
    (generated_dir / "new_report.json").write_text("{}\n", encoding="utf-8")
    diff_text = regenerate_all._diff_directories(committed_dir, generated_dir)
    assert diff_text is not None
    assert "Committed output missing" in diff_text
