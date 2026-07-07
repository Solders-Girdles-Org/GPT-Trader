from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _replay_payload() -> dict[str, Any]:
    return {
        "proposer_id": "baseline-ma-10-50",
        "symbol": "BTC-USD",
        "granularity": "ONE_HOUR",
        "source": "fixture:candles",
        "snapshots_evaluated": 24,
        "ideas_proposed": 3,
        "resolved_ideas": 2,
        "target_hit_rate": "0.5",
        "stop_hit_rate": "0.5",
        "average_return_r": "0.1",
        "eligibility_pass_rate": "1",
    }


def test_scorecard_empty_store_scores_all_gates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"

    exit_code, response = _run_json(
        capsys,
        ["ideas", "scorecard", "--ideas-root", str(root), "--format", "json"],
    )

    assert exit_code == 0
    assert response["success"] is True
    assert response["metadata"]["was_noop"] is True
    data = response["data"]
    assert data["evidence"] == "wall-clock"
    assert set(data["gates"]) == {
        "track_record_depth",
        "eligibility_pass_rate",
        "attribution_coverage",
        "risk_calibration",
        "expectancy",
        "benchmark_edge",
        "max_drawdown_from_peak",
    }
    assert set(data["loop_health"]) == {
        "proposals_flowing",
        "attribution_coverage",
        "audit_integrity",
    }
    assert data["overall"]["promotable"] is False
    assert data["gates"]["track_record_depth"]["status"] == "fail"
    assert data["gates"]["max_drawdown_from_peak"]["status"] == "not_yet_measurable"
    assert "replay_evidence" not in data
    # The command is read-only: an empty root stays empty.
    assert not root.exists() or not any(root.rglob("*"))


def test_scorecard_text_format_renders_gate_sections(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"

    exit_code = cli.main(["ideas", "scorecard", "--ideas-root", str(root), "--format", "text"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "✓ ideas scorecard OK" in output
    assert "Observation window" in output
    assert "Stage 1 -> 2 gates (wall-clock)" in output
    assert "Loop health" in output


def test_scorecard_attaches_replay_evidence_from_raw_and_enveloped_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    raw_path = tmp_path / "replay-raw.json"
    raw_path.write_text(json.dumps(_replay_payload()), encoding="utf-8")
    enveloped_path = tmp_path / "replay-enveloped.json"
    enveloped_path.write_text(
        json.dumps({"success": True, "data": _replay_payload()}),
        encoding="utf-8",
    )

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "scorecard",
            "--ideas-root",
            str(root),
            "--replay-report",
            str(raw_path),
            "--replay-report",
            str(enveloped_path),
            "--format",
            "json",
        ],
    )

    assert exit_code == 0
    data = response["data"]
    assert len(data["replay_evidence"]) == 2
    for evidence in data["replay_evidence"]:
        assert evidence["evidence"] == "replay-derived"
        assert evidence["calibration"][0]["proposer_id"] == "baseline-ma-10-50"
    # Replay evidence rides alongside; the wall-clock gates stay untouched.
    assert data["evidence"] == "wall-clock"
    assert data["gates"]["benchmark_edge"]["status"] == "not_yet_measurable"


def test_scorecard_writes_output_dir_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    artifact_dir = tmp_path / "artifacts"

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "scorecard",
            "--ideas-root",
            str(root),
            "--output-dir",
            str(artifact_dir),
            "--format",
            "json",
        ],
    )

    assert exit_code == 0
    artifact_path = Path(response["data"]["artifact_path"])
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "gpt-trader.trade_ideas.scorecard.v1"


def test_scorecard_rejects_unreadable_replay_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    bad_path = tmp_path / "not-a-report.json"
    bad_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    exit_code = cli.main(
        [
            "ideas",
            "scorecard",
            "--ideas-root",
            str(root),
            "--replay-report",
            str(bad_path),
            "--format",
            "json",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code != 0
    response = json.loads(output)
    assert response["success"] is False
