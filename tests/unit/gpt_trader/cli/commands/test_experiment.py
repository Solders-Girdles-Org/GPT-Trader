"""The owner entrypoint stays local, resumable and inspectable through CLI envelopes."""

import json

from gpt_trader.cli import main


def test_cli_run_and_read_only_inspection(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "synthetic:CLI contract",
                "symbol": "BTC-USD",
                "recorded_at": "2025-01-01T01:00:00Z",
                "candles": [
                    {
                        "ts": "2025-01-01T00:00:00Z",
                        "open": "100",
                        "high": "101",
                        "low": "99",
                        "close": "100",
                        "volume": "1",
                    }
                ],
            }
        )
    )
    root = tmp_path / "run"
    assert (
        main(["experiment", "run", "--input", str(source), "--root", str(root), "--format", "json"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["success"]
    assert result["data"]["account"]["cash"] == "1000"
    before = (root / "experiment.sqlite3").read_bytes()
    assert main(["experiment", "inspect", "--root", str(root), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == result["data"]
    assert (root / "experiment.sqlite3").read_bytes() == before
    assert main(["experiment", "run", "--root", str(root)]) == 0
    assert "Independent reconciliation passed; 0 simulated fills" in capsys.readouterr().out
    assert (root / "experiment.sqlite3").read_bytes() == before


def test_cli_invalid_input_creates_no_run(tmp_path, capsys):
    source = tmp_path / "invalid.json"
    source.write_text("{}")
    root = tmp_path / "run"
    assert (
        main(["experiment", "run", "--input", str(source), "--root", str(root), "--format", "json"])
        == 1
    )
    assert not json.loads(capsys.readouterr().out)["success"]
    assert not root.exists()


def test_cli_inspect_missing_run_creates_nothing(tmp_path, capsys):
    root = tmp_path / "missing"
    assert main(["experiment", "inspect", "--root", str(root), "--format", "json"]) == 1
    assert not json.loads(capsys.readouterr().out)["success"]
    assert not root.exists()


def test_cli_fetch_input_writes_capture_and_one_input_per_case(tmp_path, capsys, monkeypatch):
    from datetime import timedelta
    from pathlib import Path

    from gpt_trader.features.recorder import candle_capture

    protocol_path = "config/experiments/held-out-2026-09-06/protocol.json"
    protocol = json.loads(Path(protocol_path).read_text(encoding="utf-8"))

    async def fake_capture(symbol, start, end, **_):
        candles = [
            {
                "start": str(int((start + timedelta(hours=hour)).timestamp())),
                "low": "1",
                "high": "3",
                "open": "2",
                "close": "2",
                "volume": "1",
            }
            for hour in range(912)
        ]
        capture = {
            "schema": candle_capture.CAPTURE_SCHEMA,
            "symbol": symbol,
            "granularity": "ONE_HOUR",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "fetched_at": "2026-10-12T00:00:00+00:00",
            "requests": [{"response": {"candles": candles}}],
        }
        return json.dumps(capture, sort_keys=True).encode()

    monkeypatch.setattr(candle_capture, "capture_public_hourly_candles", fake_capture)
    out = tmp_path / "eth"
    args = ["experiment", "fetch-input", "--protocol", protocol_path]
    assert main([*args, "--symbol", "ETH-USD", "--out", str(out), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    names = sorted(path.name for path in out.iterdir())
    assert names == [
        "baseline-base.json",
        "baseline-stress.json",
        "capture.json",
        "mean-reversion-base.json",
        "mean-reversion-stress.json",
    ]
    stress = json.loads((out / "mean-reversion-stress.json").read_text())
    assert stress["benchmark"] == "mean-reversion"
    assert stress["settings"]["fee_bps"] == "60"
    assert stress["scored_from"] == protocol["scored_from"]
    verify = ["experiment", "verify-input", "--capture", str(out / "capture.json")]
    inputs = [
        arg for name in names if name != "capture.json" for arg in ("--input", str(out / name))
    ]
    assert main([*verify, *inputs, "--format", "json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["data"]["verified"]) == 4
    run = tmp_path / "run"
    run_args = ["experiment", "run", "--input", str(out / "baseline-base.json"), "--root", str(run)]
    assert main([*run_args, "--max-bars", "1", "--format", "json"]) == 0
    capsys.readouterr()
    assert main([*verify, "--root", str(run), "--format", "json"]) == 0
    capsys.readouterr()
    stress["candles"][100]["close"] = "2.5"
    (out / "mean-reversion-stress.json").write_text(json.dumps(stress), encoding="utf-8")
    assert main([*verify, *inputs, "--format", "json"]) == 1
    assert "differs from its capture" in capsys.readouterr().out
    assert main([*verify, "--format", "json"]) == 1
    capsys.readouterr()
    assert main([*args, "--symbol", "ETH-USD", "--out", str(out), "--format", "json"]) == 1
    capsys.readouterr()
    assert (
        main([*args, "--symbol", "DOGE-USD", "--out", str(tmp_path / "x"), "--format", "json"]) == 1
    )
