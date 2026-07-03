"""CLI tests for ``ideas replay strategy``: live-trade strategies over history.

Replay-runner scoring is pinned in the trade_ideas replay suite; these tests
cover the CLI adapter: strategy selection, the fixed-configuration min-history
floor, and the read-only replay envelope.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode

AS_OF = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
# 26 flat closes then a four-bar rise: once the rise enters the window, the
# strategy's 5/20 MA crossover and bullish trend clear the entry gate for the
# snapshots evaluated after the default 23-candle minimum history.
RISING_CLOSES = ["100"] * 26 + ["102", "104", "106", "108"]
FLAT_CLOSES = ["100"] * 30


def _fixture_payload(closes: list[str]) -> dict[str, Any]:
    return {
        "candles": [
            {
                "ts": (AS_OF + timedelta(hours=index - len(closes))).isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": "1000",
            }
            for index, close in enumerate(closes)
        ]
    }


def _write_fixture(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _strategy_args(path: Path, *, extra: list[str] | None = None) -> list[str]:
    return [
        "ideas",
        "replay",
        "strategy",
        "--file",
        str(path),
        "--symbol",
        "BTC-USD",
        "--granularity",
        "ONE_HOUR",
        "--strategy",
        "baseline-spot",
        "--format",
        "json",
        *(extra or []),
    ]


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def test_replay_strategy_json_output_returns_replay_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _write_fixture(tmp_path / "candles.json", _fixture_payload(RISING_CLOSES))

    exit_code, response = _run_json(capsys, _strategy_args(fixture))

    assert exit_code == 0
    assert response["command"] == "ideas replay strategy"
    data = response["data"]
    assert data["proposer_id"] == "snapshot-strategy-baseline-spot"
    assert data["snapshots_evaluated"] == len(RISING_CLOSES) - 23
    assert data["ideas_proposed"] >= 1


def test_replay_strategy_no_idea_replay_is_successful_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _write_fixture(tmp_path / "flat.json", _fixture_payload(FLAT_CLOSES))

    exit_code, response = _run_json(capsys, _strategy_args(fixture))

    assert exit_code == 0
    assert response["metadata"]["was_noop"] is True
    assert response["data"]["ideas_proposed"] == 0


def test_replay_strategy_rejects_min_history_below_strategy_requirements(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _write_fixture(tmp_path / "candles.json", _fixture_payload(RISING_CLOSES))

    exit_code, response = _run_json(capsys, _strategy_args(fixture, extra=["--min-history", "5"]))

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "min_history"
    assert "--min-history must be at least 23" in response["errors"][0]["message"]


def test_replay_strategy_help_documents_read_only_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["ideas", "replay", "strategy", "--help"])

    assert excinfo.value.code == 0
    output = capsys.readouterr().out
    assert "broker-free and read-only" in output
    assert "--strategy" in output
    assert "baseline-spot" in output
    assert "--price-precision" in output
