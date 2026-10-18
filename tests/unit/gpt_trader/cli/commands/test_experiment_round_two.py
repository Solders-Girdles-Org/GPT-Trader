"""The round-two protocol through the CLI: frozen manager inputs, the reading and its spend."""

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from gpt_trader.cli import main
from tests.unit.gpt_trader.features.experiment.test_manager_reading import runs

PROTOCOL_PATH = "config/experiments/round-two-2026-10-20/protocol.json"
PROTOCOL = json.loads(Path(PROTOCOL_PATH).read_text(encoding="utf-8"))


def _fake_capture(monkeypatch):
    from gpt_trader.features.recorder import candle_capture

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
            "fetched_at": "2026-11-24T03:00:00+00:00",
            "requests": [{"response": {"candles": candles}}],
        }
        return json.dumps(capture, sort_keys=True).encode()

    monkeypatch.setattr(candle_capture, "capture_public_hourly_candles", fake_capture)


def test_fetch_freezes_the_manager_settings_into_every_manager_input(tmp_path, capsys, monkeypatch):
    _fake_capture(monkeypatch)
    out = tmp_path / "btc"
    args = ["experiment", "fetch-input", "--protocol", PROTOCOL_PATH, "--symbol", "BTC-USD"]
    assert main([*args, "--out", str(out), "--format", "json"]) == 0
    capsys.readouterr()
    names = sorted(path.name for path in out.iterdir() if path.name != "capture.json")
    assert names == sorted(
        f"{rule}-{case}.json" for rule in PROTOCOL["benchmarks"] for case in PROTOCOL["cost_cases"]
    )
    manager = json.loads((out / "ai-trade-manager-stress.json").read_text())
    assert manager["manager"] == PROTOCOL["manager"]
    assert manager["scored_from"] == PROTOCOL["scored_from"]
    assert "manager" not in json.loads((out / "baseline-base.json").read_text())
    estimate = ["experiment", "estimate-spend", "--input", str(out / "ai-trade-manager-base.json")]
    assert main([*estimate, "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["turns_without_fills"] == 210 and result["max_tokens"] == 16000


def test_fetch_refuses_a_manager_protocol_without_manager_settings(tmp_path, capsys, monkeypatch):
    _fake_capture(monkeypatch)
    protocol = {key: value for key, value in PROTOCOL.items() if key != "manager"}
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")
    command = ["experiment", "fetch-input", "--protocol", str(path), "--symbol", "BTC-USD"]
    assert main([*command, "--out", str(tmp_path / "x"), "--format", "json"]) == 1
    assert "no 'manager' settings" in json.loads(capsys.readouterr().out)["errors"][0]["message"]
    assert not (tmp_path / "x").exists()


def _runs_file(tmp_path, monkeypatch, grouped):
    from gpt_trader.cli.commands import experiment as command

    reports, mapping = {}, {}
    for rule, cases in grouped.items():
        mapping[rule] = {}
        for case, rows in cases.items():
            roots = [str(tmp_path / f"{rule}-{case}-{row['symbol']}") for row in rows]
            reports.update(zip(roots, rows, strict=True))
            mapping[rule][case] = roots
    monkeypatch.setattr(command, "_inspect", lambda root: reports[str(root)])
    path = tmp_path / "runs.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return path


def test_read_applies_the_round_two_bar_and_reports_spend_from_the_ledger(
    tmp_path, capsys, monkeypatch
):
    from gpt_trader.features.experiment.model_transport import SpendLedger

    manager_ledger = SpendLedger(tmp_path / "spend.sqlite", Decimal("4720"))
    call = manager_ledger.reserve("claude-fable-5-1", Decimal("0.98"))
    manager_ledger.settle(call, None, "timeout")
    runs_file = _runs_file(tmp_path, monkeypatch, runs())
    read = ["experiment", "read", "--protocol", PROTOCOL_PATH, "--runs", str(runs_file)]
    assert main([*read, "--spend-ledger", str(manager_ledger.path), "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert (result["verdict"], result["action"]) == ("passes", "propose-round-three")
    spend = result["model_spend"]
    assert spend["journal_turns"] == 16 * 210
    assert Decimal(spend["ledger"]["charged_usd"]) == manager_ledger.spent_usd()
    assert spend["ledger_calls_minus_journal_turns"] == 1 - 16 * 210

    assert main([*read, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["model_spend"]["ledger"] is None


def test_read_refuses_misfit_runs_and_a_ledger_for_round_one(tmp_path, capsys, monkeypatch):
    def drifted(grouped):
        grouped["ai-trade-manager"]["base"][0]["manager"]["settings"]["max_tokens"] = 8000

    runs_file = _runs_file(tmp_path, monkeypatch, runs(edit=drifted))
    read = ["experiment", "read", "--protocol", PROTOCOL_PATH, "--runs", str(runs_file)]
    assert main([*read, "--format", "json"]) == 1
    error = json.loads(capsys.readouterr().out)["errors"][0]
    assert "frozen ai-trade-manager settings" in error["message"]
    held_out = "config/experiments/held-out-2026-09-06/protocol.json"
    command = ["experiment", "read", "--protocol", held_out, "--runs", str(runs_file)]
    assert main([*command, "--spend-ledger", str(tmp_path / "x"), "--format", "json"]) == 1
    assert (
        "--spend-ledger applies only" in json.loads(capsys.readouterr().out)["errors"][0]["message"]
    )
