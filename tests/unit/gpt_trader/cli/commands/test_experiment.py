"""The owner entrypoint stays local, resumable and inspectable through CLI envelopes."""

import json
from datetime import UTC, datetime, timedelta

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


def _reverting_input(tmp_path, **extra):
    """Synthetic oscillation, a sharp dip and a recovery; mechanics only, no evidence."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [(100 + index % 2, 100 + index % 2) for index in range(30)]
    rows += [(101, 95), (95, 96), (96, 98.5), (98.5, 98.5), (98.5, 98.5), (98.5, 98.5)]
    candles = []
    for index, (opening, close) in enumerate(rows):
        high = 99 if index == 32 else max(opening, close) + 0.5
        candles.append(
            {
                "ts": (start + timedelta(hours=index)).isoformat(),
                "open": str(opening),
                "high": str(high),
                "low": str(min(opening, close) - 0.5),
                "close": str(close),
                "volume": "1",
            }
        )
    path = tmp_path / "reverting.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "synthetic:mean-reversion mechanics",
                "symbol": "BTC-USD",
                "recorded_at": (start + timedelta(hours=len(rows))).isoformat(),
                "candles": candles,
                **extra,
            }
        )
    )
    return path


def _run(*arguments):
    return main(["experiment", *arguments, "--format", "json"])


def test_cli_runs_mean_reversion_through_the_snapshot_strategy_wrapper(tmp_path, capsys):
    root = tmp_path / "reverting"
    source = _reverting_input(tmp_path)
    assert (
        _run("run", "--input", str(source), "--root", str(root), "--benchmark", "mean-reversion")
        == 0
    )
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["benchmark"] == "mean-reversion"
    entry = data["activity"][0]
    assert entry["decision"] == "mean reversion: consider entry at next open"
    assert entry["thesis"].startswith("mean-reversion emitted a buy signal for BTC-USD")
    assert data["account"]["fill_count"] == 2
    assert data["round_trips"] == 1
    assert _run("inspect", "--root", str(root)) == 0
    assert json.loads(capsys.readouterr().out)["data"] == data
    # The same data under the default rule is a different, separately bound run.
    assert _run("run", "--input", str(source), "--root", str(tmp_path / "default")) == 0
    default = json.loads(capsys.readouterr().out)["data"]
    assert default["benchmark"] == "baseline"
    assert default["identity"] != data["identity"]


def test_cli_text_report_shows_rule_end_valuation_and_passive_rows(tmp_path, capsys):
    source = _reverting_input(tmp_path, scored_from="2026-01-01T19:00:00+00:00")
    root = tmp_path / "text"
    assert (
        main(
            [
                "experiment",
                "run",
                "--input",
                str(source),
                "--root",
                str(root),
                "--benchmark",
                "mean-reversion",
            ]
        )
        == 0
    )
    text = capsys.readouterr().out
    assert "Benchmark: mean-reversion" in text
    assert "Window: 19 warm-up bars (history only), 17 scored bars" in text
    assert "End valuation (after exit costs): net $" in text
    assert "Hold the coin: net $" in text and "Cash: net $0" in text


def test_cli_benchmark_conflicting_with_input_creates_no_run(tmp_path, capsys):
    source = _reverting_input(tmp_path, benchmark="baseline")
    root = tmp_path / "conflict"
    assert (
        _run("run", "--input", str(source), "--root", str(root), "--benchmark", "mean-reversion")
        == 1
    )
    assert "conflicts" in json.dumps(json.loads(capsys.readouterr().out))
    assert not root.exists()


def test_cli_resume_cannot_switch_the_bound_benchmark(tmp_path, capsys):
    root = tmp_path / "bound"
    source = _reverting_input(tmp_path)
    assert _run("run", "--input", str(source), "--root", str(root), "--max-bars", "3") == 0
    before = (root / "experiment.sqlite3").read_bytes()
    capsys.readouterr()
    assert _run("run", "--root", str(root), "--benchmark", "mean-reversion") == 1
    assert "baseline" in json.dumps(json.loads(capsys.readouterr().out))
    assert (
        _run("run", "--input", str(source), "--root", str(root), "--benchmark", "mean-reversion")
        == 1
    )
    assert (root / "experiment.sqlite3").read_bytes() == before
    assert _run("run", "--root", str(root), "--benchmark", "baseline", "--max-bars", "1") == 0
