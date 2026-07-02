from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode
from gpt_trader.features.trade_ideas import (
    ACTOR_ENV_VAR,
    DEFAULT_IDEAS_ROOT,
    IDEAS_ROOT_ENV_VAR,
    resolve_ideas_root,
    resolve_trade_idea_actor_id,
)


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def test_budget_show_seeds_defaults_and_budget_set_bumps_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(capsys, ["ideas", "budget", "show", *_root_args(root)])
    assert exit_code == 0
    assert response["data"]["version"] == 1
    assert response["data"]["max_loss_per_idea_pct"] == "5"
    assert response["data"]["headroom"]["daily_loss_used_pct"] == "0"
    assert response["data"]["headroom"]["daily_loss_headroom_pct"] == "10"
    assert response["data"]["headroom"]["open_notional"] == "0"

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "budget",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--max-loss-per-idea-pct",
            "2",
            "--reason",
            "Tighten canary budget",
        ],
    )
    assert exit_code == 0
    assert response["data"]["version"] == 2
    assert response["data"]["max_loss_per_idea_pct"] == "2"
    assert response["data"]["reason"] == "Tighten canary budget"


def test_budget_set_requires_at_least_one_field_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "budget",
            "set",
            *_root_args(root),
            "--actor",
            "rj",
            "--reason",
            "No changes",
        ],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.MISSING_ARGUMENT.value


def test_budget_set_rejects_non_finite_decimal_before_budget_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ideas"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "ideas",
                "budget",
                "set",
                *_root_args(root),
                "--actor",
                "rj",
                "--max-loss-per-idea-pct",
                "Infinity",
                "--reason",
                "Unsafe budget",
            ]
        )

    assert exc_info.value.code == 2
    assert "decimal value must be finite" in capsys.readouterr().err
    assert not (root / "risk_budget.jsonl").exists()


@pytest.mark.parametrize(
    ("flag", "expected_error"),
    [
        ("--max-loss-per-idea-pct", "decimal value must be non-negative"),
        ("--max-daily-loss-pct", "decimal value must be non-negative"),
        ("--max-open-notional-pct", "decimal value must be non-negative"),
        ("--gain-retention-floor-pct", "decimal value must be non-negative"),
        ("--max-concurrent-approved-tickets", "integer value must be non-negative"),
        ("--max-review-latency-hours", "integer value must be non-negative"),
    ],
)
def test_budget_set_rejects_negative_limit_flags_before_budget_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    expected_error: str,
) -> None:
    root = tmp_path / "ideas"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "ideas",
                "budget",
                "set",
                *_root_args(root),
                "--actor",
                "rj",
                flag,
                "-1",
                "--reason",
                "Unsafe budget",
            ]
        )

    assert exc_info.value.code == 2
    assert expected_error in capsys.readouterr().err
    assert not (root / "risk_budget.jsonl").exists()


def test_shared_root_and_actor_resolution_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_root = tmp_path / "configured"
    explicit_root = tmp_path / "explicit"
    monkeypatch.setenv(IDEAS_ROOT_ENV_VAR, str(configured_root))
    monkeypatch.setenv(ACTOR_ENV_VAR, "env-actor")

    assert resolve_ideas_root(explicit_root) == explicit_root
    assert resolve_ideas_root() == configured_root
    assert resolve_trade_idea_actor_id("flag-actor") == "flag-actor"
    assert resolve_trade_idea_actor_id(None) == "env-actor"

    monkeypatch.delenv(IDEAS_ROOT_ENV_VAR)
    monkeypatch.delenv(ACTOR_ENV_VAR)
    assert resolve_ideas_root() == DEFAULT_IDEAS_ROOT
