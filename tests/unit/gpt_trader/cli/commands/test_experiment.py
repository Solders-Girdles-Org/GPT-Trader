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


def _failure(capsys, argv):
    """Run an expected operator mistake; return its error, asserting no traceback."""
    assert main([*argv, "--format", "json"]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    envelope = json.loads(captured.out)
    assert not envelope["success"]
    assert envelope["command"] == f"experiment {argv[1]}"
    return envelope["errors"][0]


def test_cli_missing_input_file_is_file_not_found(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    root = tmp_path / "run"
    error = _failure(capsys, ["experiment", "run", "--root", str(root), "--input", str(missing)])
    assert error["code"] == "FILE_NOT_FOUND"
    assert str(missing) in error["message"]
    assert not root.exists()


def test_cli_malformed_input_names_the_file(tmp_path, capsys):
    source = tmp_path / "bad.json"
    source.write_text("{bad")
    root = tmp_path / "run"
    error = _failure(capsys, ["experiment", "run", "--root", str(root), "--input", str(source)])
    assert error["code"] == "CONFIG_INVALID"
    assert str(source) in error["message"] and "line 1, column 2" in error["message"]
    assert not root.exists()


def test_cli_invalid_input_is_a_validation_error(tmp_path, capsys):
    source = tmp_path / "empty.json"
    source.write_text("{}")
    error = _failure(
        capsys, ["experiment", "run", "--root", str(tmp_path / "run"), "--input", str(source)]
    )
    assert error["code"] == "VALIDATION_ERROR"
    assert str(source) in error["message"] and "schema_version" in error["message"]


def test_cli_run_without_input_on_new_root_is_run_not_found(tmp_path, capsys):
    root = tmp_path / "new"
    error = _failure(capsys, ["experiment", "run", "--root", str(root)])
    assert error["code"] == "RUN_NOT_FOUND"
    assert str(root) in error["message"] and "--input" in error["message"]
    assert not root.exists()


def test_cli_run_root_that_is_a_file_is_refused(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(_one_bar_input()))
    root = tmp_path / "afile"
    root.write_text("")
    error = _failure(capsys, ["experiment", "run", "--root", str(root), "--input", str(source)])
    assert error["code"] == "INVALID_ARGUMENT"
    assert f"{root} is not a directory" in error["message"]


def test_cli_inspect_missing_root_names_the_root(tmp_path, capsys):
    root = tmp_path / "missing"
    error = _failure(capsys, ["experiment", "inspect", "--root", str(root)])
    assert error["code"] == "RUN_NOT_FOUND"
    assert error["message"] == f"Experiment root {root} has no experiment.sqlite3"
    assert not root.exists()


def test_cli_inspect_unreadable_ledger_is_typed(tmp_path, capsys):
    root = tmp_path / "run"
    root.mkdir()
    (root / "experiment.sqlite3").write_text("not a database")
    error = _failure(capsys, ["experiment", "inspect", "--root", str(root)])
    assert error["code"] == "OPERATION_FAILED"
    assert str(root) in error["message"]


def test_cli_fetch_protocol_missing_key_names_it(tmp_path, capsys):
    protocol = tmp_path / "p.json"
    protocol.write_text(json.dumps({"warmup_start": "x"}))
    argv = ["experiment", "fetch-input", "--protocol", str(protocol), "--symbol", "BTC-USD"]
    error = _failure(capsys, [*argv, "--out", str(tmp_path / "out")])
    assert error["code"] == "CONFIG_INVALID"
    assert f"Protocol {protocol} is missing required keys 'symbols'" in error["message"]
    assert error["details"]["fields"][0] == "symbols"
    assert not (tmp_path / "out").exists()


def test_cli_read_protocol_missing_key_names_it(tmp_path, capsys):
    from pathlib import Path

    committed = Path("config/experiments/held-out-2026-09-06/protocol.json")
    payload = json.loads(committed.read_text(encoding="utf-8"))
    del payload["protocol"]
    protocol = tmp_path / "p.json"
    protocol.write_text(json.dumps(payload))
    runs = tmp_path / "runs.json"
    runs.write_text("{}")
    argv = ["experiment", "read", "--protocol", str(protocol), "--runs", str(runs)]
    error = _failure(capsys, argv)
    assert error["code"] == "CONFIG_INVALID"
    assert error["message"] == f"Protocol {protocol} is missing required key 'protocol'"


def test_cli_read_runs_naming_a_missing_root_is_run_not_found(tmp_path, capsys):
    protocol = "config/experiments/held-out-2026-09-06/protocol.json"
    payload = json.loads(open(protocol, encoding="utf-8").read())
    missing = str(tmp_path / "missing")
    runs = tmp_path / "runs.json"
    runs.write_text(
        json.dumps(
            {
                rule: {case: [missing] for case in payload["cost_cases"]}
                for rule in payload["benchmarks"]
            }
        )
    )
    error = _failure(capsys, ["experiment", "read", "--protocol", protocol, "--runs", str(runs)])
    assert error["code"] == "RUN_NOT_FOUND"
    assert missing in error["message"]


def test_cli_verify_missing_capture_is_file_not_found(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_text("{}")
    capture = tmp_path / "capture.json"
    argv = ["experiment", "verify-input", "--capture", str(capture), "--input", str(source)]
    error = _failure(capsys, argv)
    assert error["code"] == "FILE_NOT_FOUND"
    assert str(capture) in error["message"]


def test_cli_max_bars_must_be_positive_at_parse_time(tmp_path, capsys):
    import pytest

    for value in ("0", "-5"):
        with pytest.raises(SystemExit) as exit_info:
            main(["experiment", "run", "--root", str(tmp_path / "run"), "--max-bars", value])
        assert exit_info.value.code == 2
        assert "must be a positive integer" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


def test_cli_unexpected_failure_still_reports_an_internal_error(
    tmp_path, capsys, caplog, monkeypatch
):
    from gpt_trader.cli.commands import experiment

    def broken(root):
        raise RuntimeError("engine bug")

    monkeypatch.setattr(experiment, "inspect_run", broken)
    root = tmp_path / "run"
    root.mkdir()
    (root / "experiment.sqlite3").write_text("")
    assert main(["experiment", "inspect", "--root", str(root), "--format", "json"]) == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "experiment inspect"
    assert envelope["errors"][0]["code"] == "INTERNAL_ERROR"
    assert any(record.exc_info for record in caplog.records)


def _one_bar_input():
    return {
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


def test_cli_new_run_in_non_empty_root_is_refused(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(_one_bar_input()))
    root = tmp_path / "run"
    root.mkdir()
    (root / "notes.txt").write_text("keep")
    error = _failure(capsys, ["experiment", "run", "--root", str(root), "--input", str(source)])
    assert error["code"] == "INVALID_ARGUMENT"
    assert f"{root} is not empty" in error["message"]
    assert sorted(path.name for path in root.iterdir()) == ["notes.txt"]


def test_cli_engine_faults_mid_run_stay_internal_errors(tmp_path, capsys, caplog, monkeypatch):
    import sqlite3

    from gpt_trader.cli.commands import experiment

    source = tmp_path / "input.json"
    source.write_text(json.dumps(_one_bar_input()))
    for fault in (ValueError("engine bug"), sqlite3.IntegrityError("constraint bug")):

        def broken(*_args, fault=fault, **_kwargs):
            raise fault

        monkeypatch.setattr(experiment, "run_experiment", broken)
        caplog.clear()
        root = tmp_path / type(fault).__name__
        argv = ["experiment", "run", "--root", str(root), "--input", str(source)]
        assert main([*argv, "--format", "json"]) == 1
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["errors"][0]["code"] == "INTERNAL_ERROR"
        assert any(record.exc_info for record in caplog.records)


def test_cli_protocol_with_malformed_values_is_config_invalid(tmp_path, capsys):
    committed = "config/experiments/held-out-2026-09-06/protocol.json"
    payload = json.loads(open(committed, encoding="utf-8").read())
    payload["cost_cases"] = ["base", "stress"]
    protocol = tmp_path / "p.json"
    protocol.write_text(json.dumps(payload))
    argv = ["experiment", "fetch-input", "--protocol", str(protocol), "--symbol", "ETH-USD"]
    error = _failure(capsys, [*argv, "--out", str(tmp_path / "out")])
    assert error["code"] == "CONFIG_INVALID"
    assert error["details"]["fields"] == ["cost_cases"]


def test_cli_verify_capture_of_wrong_shape_is_config_invalid(tmp_path, capsys):
    capture = tmp_path / "capture.json"
    capture.write_text("[]")
    source = tmp_path / "input.json"
    source.write_text("{}")
    argv = ["experiment", "verify-input", "--capture", str(capture), "--input", str(source)]
    error = _failure(capsys, argv)
    assert error["code"] == "CONFIG_INVALID"
    assert str(capture) in error["message"]
