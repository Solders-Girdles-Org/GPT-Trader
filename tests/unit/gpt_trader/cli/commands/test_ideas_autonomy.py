from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def test_autonomy_show_reports_seeded_default_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(capsys, ["ideas", "autonomy", "show", *_root_args(root)])

    assert exit_code == 0
    assert response["data"]["mode"] == "human_approved_execution"
    assert response["data"]["source"] == "seeded_default"
    assert response["data"]["version"] is None
    assert not (root / "autonomy_state.jsonl").exists()


def test_autonomy_set_raises_level_and_show_reflects_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "autonomy",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--mode",
            "bounded_autonomy",
            "--reason",
            "Earned after the replay tournament",
        ],
    )
    assert exit_code == 0
    assert response["data"]["mode"] == "bounded_autonomy"
    assert response["data"]["version"] == 2
    assert response["data"]["actor_type"] == "human"

    exit_code, response = _run_json(capsys, ["ideas", "autonomy", "show", *_root_args(root)])
    assert exit_code == 0
    assert response["data"]["mode"] == "bounded_autonomy"
    assert response["data"]["source"] == "autonomy_state_log"
    assert response["data"]["actor_id"] == "rj"
    assert response["data"]["reason"] == "Earned after the replay tournament"


def test_autonomy_history_lists_every_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    _run_json(
        capsys,
        [
            "ideas",
            "autonomy",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--mode",
            "research_only",
            "--reason",
            "Pause approvals during incident review",
        ],
    )

    exit_code, response = _run_json(capsys, ["ideas", "autonomy", "history", *_root_args(root)])

    assert exit_code == 0
    assert response["data"]["count"] == 2
    modes = [entry["mode"] for entry in response["data"]["entries"]]
    assert modes == ["human_approved_execution", "research_only"]


def test_autonomy_set_requires_nonempty_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "autonomy",
            "set",
            *_root_args(root),
            "--mode",
            "research_only",
            "--reason",
            "   ",
        ],
    )

    assert exit_code != 0
    assert response["errors"][0]["code"] == CliErrorCode.MISSING_ARGUMENT.value


def test_autonomy_show_surfaces_fail_closed_resolution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    root.mkdir(parents=True)
    (root / "autonomy_state.jsonl").write_text("garbage\n", encoding="utf-8")

    exit_code, response = _run_json(capsys, ["ideas", "autonomy", "show", *_root_args(root)])

    assert exit_code == 0
    assert response["data"]["mode"] == "research_only"
    assert response["data"]["source"] == "fail_closed"
    assert "malformed" in response["data"]["error"]


def test_autonomy_history_fails_on_broken_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    root.mkdir(parents=True)
    (root / "autonomy_state.jsonl").write_text("garbage\n", encoding="utf-8")

    exit_code, response = _run_json(capsys, ["ideas", "autonomy", "history", *_root_args(root)])

    assert exit_code != 0
    assert response["errors"][0]["code"] == CliErrorCode.OPERATION_FAILED.value


def test_autonomy_set_refused_on_broken_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"
    root.mkdir(parents=True)
    (root / "autonomy_state.jsonl").write_text("garbage\n", encoding="utf-8")

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "autonomy",
            "set",
            *_root_args(root),
            "--mode",
            "research_only",
            "--reason",
            "Attempted change over a broken log",
        ],
    )

    assert exit_code != 0
    assert response["errors"][0]["code"] == CliErrorCode.OPERATION_FAILED.value
