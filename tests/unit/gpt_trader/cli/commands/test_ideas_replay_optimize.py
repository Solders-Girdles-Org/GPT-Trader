from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli

AS_OF = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _candle(
    offset_hours: int,
    *,
    open_: str = "101",
    high: str = "102",
    low: str = "100",
    close: str = "101",
    volume: str = "1000",
) -> dict[str, str]:
    return {
        "ts": (AS_OF + timedelta(hours=offset_hours)).isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _optimize_replay_fixture() -> dict[str, list[dict[str, str]]]:
    return {
        "candles": [
            _candle(-6, open_="100", high="100", low="100", close="100"),
            _candle(-5, open_="100", high="100", low="100", close="100"),
            _candle(-4, open_="90", high="90", low="90", close="90"),
            _candle(-3, open_="100", high="100", low="100", close="100"),
            _candle(-2, open_="105", high="105", low="105", close="105"),
            _candle(-1, open_="110", high="110", low="110", close="110"),
            _candle(0, open_="110", high="112", low="109", close="111"),
            _candle(1, open_="111", high="130", low="111", close="126"),
        ]
    }


def _optimize_study_fixture() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "candidate_id": "fast",
                "parameters": {
                    "short_ma_period": 2,
                    "long_ma_period": 4,
                    "crossover_lookback": 1,
                    "expiry_hours": 3,
                },
                "objective_value": 0.10,
            },
            {
                "candidate_id": "slow",
                "parameters": {
                    "short_ma_period": 3,
                    "long_ma_period": 5,
                    "crossover_lookback": 1,
                    "expiry_hours": 3,
                },
                "objective_value": 0.20,
            },
        ]
    }


def _write_fixture(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _baseline_args(path: Path, *, output_format: str = "json") -> list[str]:
    return [
        "ideas",
        "replay",
        "baseline",
        "--file",
        str(path),
        "--symbol",
        "BTC-USD",
        "--granularity",
        "ONE_HOUR",
        "--short-window",
        "2",
        "--long-window",
        "4",
        "--crossover-lookback",
        "1",
        "--expiry-hours",
        "3",
        "--format",
        output_format,
    ]


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _run_text(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, output


def test_replay_baseline_json_ranks_optimize_sourced_candidates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path / "candles.json", _optimize_replay_fixture())
    study = _write_fixture(tmp_path / "study.json", _optimize_study_fixture())
    argv = _baseline_args(fixture)
    argv.extend(
        [
            "--from-optimize-study",
            str(study),
            "--optimize-objective",
            "average-return-r",
        ]
    )

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 0
    assert response["command"] == "ideas replay baseline"
    assert response["metadata"]["was_noop"] is False
    data = response["data"]
    assert data["candidate_count"] == 2
    assert data["objective"] == "average-return-r"
    assert data["rankings"][0]["candidate_id"] == "slow"
    assert data["rankings"][0]["proposer_id"] == "baseline-ma-3-5"
    assert data["rankings"][0]["report"]["target_hits"] == 1
    assert data["rankings"][0]["report"]["average_return_r"] == "2"


def test_replay_baseline_text_renders_optimize_candidate_table(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path / "candles.json", _optimize_replay_fixture())
    study = _write_fixture(tmp_path / "study.json", _optimize_study_fixture())
    argv = _baseline_args(fixture, output_format="text")
    argv.extend(["--from-optimize-study", str(study)])

    exit_code, output = _run_text(capsys, argv)

    assert exit_code == 0
    assert "ideas replay baseline OK (BTC-USD ONE_HOUR, candidates=2" in output
    assert "RANK  CANDIDATE_ID  PROPOSER_ID" in output
    assert "1  slow  baseline-ma-3-5" in output
