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
