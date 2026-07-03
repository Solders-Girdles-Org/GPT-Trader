from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode

AS_OF = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _candle(
    offset_hours: int,
    *,
    open_: str = "100",
    high: str = "100",
    low: str = "100",
    close: str = "100",
) -> dict[str, str]:
    return {
        "ts": (AS_OF + timedelta(hours=offset_hours)).isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": "1000",
    }


def _tournament_fixture() -> dict[str, list[dict[str, str]]]:
    return {
        "candles": [
            _candle(-8),
            _candle(-7),
            _candle(-6),
            _candle(-5),
            _candle(-4, open_="110", high="110", low="110", close="110"),
            _candle(-3, open_="90", high="90", low="90", close="90"),
            _candle(-2, open_="112", high="112", low="112", close="112"),
            _candle(-1, open_="112", high="113", low="112", close="112"),
            _candle(0, open_="132", high="132", low="132", close="132"),
        ]
    }


def _write_fixture(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _tournament_args(path: Path, *, output_format: str = "json") -> list[str]:
    return [
        "ideas",
        "replay",
        "tournament",
        "--file",
        str(path),
        "--symbol",
        "BTC-USD",
        "--granularity",
        "ONE_HOUR",
        "--proposers",
        "baseline-ma-2-4,baseline-ma-3-5",
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


def test_replay_tournament_json_ranks_registered_baseline_proposers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path / "candles.json", _tournament_fixture())

    exit_code, response = _run_json(capsys, _tournament_args(fixture))

    assert exit_code == 0
    assert response["command"] == "ideas replay tournament"
    data = response["data"]
    assert set(data) == {
        "granularity",
        "proposer_count",
        "rankings",
        "reports",
        "snapshots_evaluated",
        "source",
        "symbol",
    }
    assert data["proposer_count"] == 2
    assert data["rankings"][0]["proposer_id"] == "baseline-ma-3-5"
    assert data["rankings"][0]["average_return_r"] == "2"
    assert data["rankings"][0]["target_hit_rate"] == "1"
    assert data["rankings"][1]["proposer_id"] == "baseline-ma-2-4"
    assert [report["proposer_id"] for report in data["reports"]] == [
        "baseline-ma-2-4",
        "baseline-ma-3-5",
    ]


def test_replay_tournament_text_output_is_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path / "candles.json", _tournament_fixture())

    exit_code, output = _run_text(capsys, _tournament_args(fixture, output_format="text"))

    assert exit_code == 0
    assert "ideas replay tournament OK (BTC-USD ONE_HOUR, snapshots=3, proposers=2)" in output
    assert "RANK  PROPOSER_ID" in output
    assert "1  baseline-ma-3-5" in output
    assert "2  baseline-ma-2-4" in output


def _long_history_fixture() -> dict[str, list[dict[str, str]]]:
    """26 flat closes then a four-bar rise, long enough for the strategy floor."""
    closes = ["100"] * 26 + ["102", "104", "106", "108"]
    return {
        "candles": [
            _candle(index - len(closes), open_=close, high=close, low=close, close=close)
            for index, close in enumerate(closes)
        ]
    }


def test_replay_tournament_ranks_strategy_backed_beside_baseline_proposers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path / "long-candles.json", _long_history_fixture())
    argv = _tournament_args(fixture)
    argv[argv.index("--proposers") + 1] = "baseline-ma-2-4,strategy-baseline-spot"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 0
    data = response["data"]
    assert data["proposer_count"] == 2
    proposer_ids = {report["proposer_id"] for report in data["reports"]}
    assert proposer_ids == {"baseline-ma-2-4", "snapshot-strategy-baseline-spot"}
    # The shared window starts at the strategy floor, and both proposers trade.
    assert all(report["ideas_proposed"] >= 1 for report in data["reports"])


def test_replay_tournament_rejects_min_history_below_strategy_floor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path / "long-candles.json", _long_history_fixture())
    argv = _tournament_args(fixture)
    argv[argv.index("--proposers") + 1] = "baseline-ma-2-4,strategy-baseline-spot"
    argv.extend(["--min-history", "10"])

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert "--min-history must be at least 20" in response["errors"][0]["message"]


def test_replay_tournament_ranks_mean_reversion_strategy_beside_baseline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The dip sits one bar before the end so the shared replay window ending
    # at the dip is evaluated for the mean-reversion strategy proposer.
    closes = ["100"] * 28 + ["96", "100"]
    fixture = _write_fixture(
        tmp_path / "dip-candles.json",
        {
            "candles": [
                _candle(index - len(closes), open_=close, high=close, low=close, close=close)
                for index, close in enumerate(closes)
            ]
        },
    )
    argv = _tournament_args(fixture)
    argv[argv.index("--proposers") + 1] = "baseline-ma-2-4,strategy-mean-reversion"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 0
    data = response["data"]
    proposer_ids = {report["proposer_id"] for report in data["reports"]}
    assert proposer_ids == {"baseline-ma-2-4", "snapshot-strategy-mean-reversion"}
    mean_reversion_report = next(
        report
        for report in data["reports"]
        if report["proposer_id"] == "snapshot-strategy-mean-reversion"
    )
    assert mean_reversion_report["ideas_proposed"] >= 1


def test_replay_tournament_regime_switcher_floor_dominates_min_history(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path / "long-candles.json", _long_history_fixture())
    argv = _tournament_args(fixture)
    argv[argv.index("--proposers") + 1] = "baseline-ma-2-4,strategy-regime-switcher"
    argv.extend(["--min-history", "30"])

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    # The shared window floor is the regime switcher's regime-confirmation
    # floor (long-EMA 50 + min-regime-ticks 5 - 1 = 54), not the baseline's.
    assert "--min-history must be at least 54" in response["errors"][0]["message"]


def test_replay_tournament_rejects_unknown_proposer_id(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path / "candles.json", _tournament_fixture())
    argv = _tournament_args(fixture)
    argv[argv.index("--proposers") + 1] = "unknown"

    exit_code, response = _run_json(capsys, argv)

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "proposers"
