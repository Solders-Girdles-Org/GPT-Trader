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
# snapshots evaluated after the default 20-candle warm-up floor.
RISING_CLOSES = ["100"] * 26 + ["102", "104", "106", "108"]
FLAT_CLOSES = ["100"] * 30
# The dip sits one bar before the end so the replay window ending at the dip
# is evaluated (windows never include the fixture's final candle).
MEAN_REVERSION_DIP_CLOSES = ["100"] * 28 + ["96", "100"]
# Ultra-quiet damped oscillation, then the same dip: the regime detector
# confirms SIDEWAYS_QUIET at candle 54 (long-EMA 50 + min-regime-ticks 5 - 1)
# and the window ending at the dip routes to the mean-reversion delegate.
REGIME_SWITCHER_DIP_CLOSES = [
    f"{100 + (1 if i % 2 == 0 else -1) * 0.05 * (0.995 ** i):.4f}" for i in range(58)
] + ["96", "100"]


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


def _strategy_args(
    path: Path,
    *,
    strategy: str = "baseline-spot",
    extra: list[str] | None = None,
) -> list[str]:
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
        strategy,
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
    # Default min-history is the strategy's live warm-up floor (20), so the
    # replay evaluates every snapshot the live strategy would have evaluated.
    assert data["snapshots_evaluated"] == len(RISING_CLOSES) - 20
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
    assert "--min-history must be at least 20" in response["errors"][0]["message"]


def test_replay_strategy_mean_reversion_uses_its_own_warmup_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _write_fixture(tmp_path / "dip.json", _fixture_payload(MEAN_REVERSION_DIP_CLOSES))

    exit_code, response = _run_json(capsys, _strategy_args(fixture, strategy="mean-reversion"))

    assert exit_code == 0
    data = response["data"]
    assert data["proposer_id"] == "snapshot-strategy-mean-reversion"
    # Default min-history is the Z-Score lookback window (20).
    assert data["snapshots_evaluated"] == len(MEAN_REVERSION_DIP_CLOSES) - 20
    assert data["ideas_proposed"] >= 1


def test_replay_strategy_regime_switcher_uses_the_detector_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _write_fixture(
        tmp_path / "sideways-dip.json", _fixture_payload(REGIME_SWITCHER_DIP_CLOSES)
    )

    exit_code, response = _run_json(capsys, _strategy_args(fixture, strategy="regime-switcher"))

    assert exit_code == 0
    data = response["data"]
    assert data["proposer_id"] == "snapshot-strategy-regime-switcher"
    # Default min-history is the detector's regime-confirmation floor
    # (long-EMA 50 + min-regime-ticks 5 - 1 = 54).
    assert data["snapshots_evaluated"] == len(REGIME_SWITCHER_DIP_CLOSES) - 54
    assert data["ideas_proposed"] >= 1


def test_replay_strategy_rejects_min_history_below_regime_switcher_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _write_fixture(
        tmp_path / "sideways-dip.json", _fixture_payload(REGIME_SWITCHER_DIP_CLOSES)
    )

    exit_code, response = _run_json(
        capsys,
        _strategy_args(fixture, strategy="regime-switcher", extra=["--min-history", "30"]),
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "min_history"
    assert "--min-history must be at least 54 for regime-switcher" in (
        response["errors"][0]["message"]
    )


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
