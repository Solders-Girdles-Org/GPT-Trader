from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gpt_trader import cli
from gpt_trader.cli.response import CliErrorCode

AS_OF = datetime(2035, 6, 12, 0, 0, tzinfo=UTC)
REPLAY_AS_OF = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
# 57 flat candles: the regime detector warms at long_ema_period (50) and then
# needs min_regime_ticks (5) to leave UNKNOWN before the 3-candle crossover.
GOLDEN_CROSS = ["100"] * 57 + ["102", "104", "106"]


def _run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main(argv)
    output = capsys.readouterr().out
    assert output
    return exit_code, json.loads(output)


def _root_args(root: Path) -> list[str]:
    return ["--ideas-root", str(root), "--format", "json"]


def _series_payload(
    closes: list[str],
    *,
    symbol: str = "BTC-USD",
    as_of: datetime = AS_OF,
    last_volume: str = "5000",
) -> dict[str, Any]:
    candles: list[dict[str, str]] = []
    for index, close in enumerate(closes):
        timestamp = as_of - timedelta(days=len(closes) - index)
        candles.append(
            {
                "ts": timestamp.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": last_volume if index == len(closes) - 1 else "1000",
            }
        )
    return {"symbol": symbol, "granularity": "1d", "candles": candles}


def _snapshot_payload() -> dict[str, Any]:
    return {
        "as_of": AS_OF.isoformat(),
        "source": "local-fixture:coinbase-candles",
        "series": [_series_payload(GOLDEN_CROSS)],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _replay_candle(
    offset_hours: int,
    *,
    open_: str = "101",
    high: str = "102",
    low: str = "100",
    close: str = "101",
) -> dict[str, str]:
    return {
        "ts": (REPLAY_AS_OF + timedelta(hours=offset_hours)).isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": "1000",
    }


def _replay_fixture() -> dict[str, list[dict[str, str]]]:
    warmup = [
        _replay_candle(index - 56, open_="100", high="100", low="100", close="100")
        for index in range(55)
    ]
    return {
        "candles": [
            *warmup,
            _replay_candle(-1, open_="110", high="110", low="110", close="110"),
            _replay_candle(0, open_="110", high="112", low="109", close="111"),
            _replay_candle(1, open_="111", high="126", low="111", close="126"),
        ]
    }


def test_propose_regime_aware_persists_generated_proposal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "ideas"
    snapshot_path = _write_json(tmp_path / "snapshot.json", _snapshot_payload())

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "propose-regime-aware",
            *_root_args(root),
            "--snapshot",
            str(snapshot_path),
        ],
    )

    assert exit_code == 0
    assert response["success"] is True
    assert response["command"] == "ideas propose-regime-aware"
    assert response["data"]["proposer_id"] == "regime-aware-ma-10-50"
    assert response["data"]["proposal_count"] == 1
    proposal = response["data"]["proposed"][0]
    assert proposal["decision_id"].startswith("trade-20350612-btcusd-")
    assert proposal["state"] == "proposed"
    event = json.loads((root / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert event["actor_type"] == "ai"
    assert event["actor_id"] == "regime-aware-ma-10-50"
    assert "proposer_id=regime-aware-ma-10-50" in event["evidence"]
    latest = json.loads(
        (root / "records" / proposal["decision_id"] / "latest.json").read_text(encoding="utf-8")
    )
    assert any("detector=market-regime-detector-v1" in item for item in latest["data_used"])
    assert "Regime overlay" in latest["thesis"]


def test_replay_regime_aware_json_output_returns_replay_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_json(tmp_path / "candles.json", _replay_fixture())

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "replay",
            "regime-aware",
            "--file",
            str(fixture),
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
            "json",
        ],
    )

    assert exit_code == 0
    assert response["command"] == "ideas replay regime-aware"
    data = response["data"]
    assert data["proposer_id"] == "regime-aware-ma-2-4"
    assert data["snapshots_evaluated"] == 3
    assert data["ideas_proposed"] == 1
    assert data["target_hits"] == 1
    assert data["ideas"][0]["outcome"] == "target_hit"


def test_replay_regime_aware_rejects_min_history_below_detector_warmup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_json(tmp_path / "candles.json", _replay_fixture())

    exit_code, response = _run_json(
        capsys,
        [
            "ideas",
            "replay",
            "regime-aware",
            "--file",
            str(fixture),
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
            "--min-history",
            "5",
            "--format",
            "json",
        ],
    )

    assert exit_code == 1
    assert response["errors"][0]["code"] == CliErrorCode.INVALID_ARGUMENT.value
    assert response["errors"][0]["details"]["field"] == "min_history"
    assert "--min-history must be at least 55" in response["errors"][0]["message"]
    assert "regime-long-ema=50" in response["errors"][0]["message"]
