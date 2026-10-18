"""The round-two protocol through the CLI: frozen manager inputs, the reading and its spend."""

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from gpt_trader.cli import main
from gpt_trader.core.fill_accounting import aware_time
from tests.unit.gpt_trader.features.experiment.test_manager_reading import DIGEST, runs

PROTOCOL_PATH = "config/experiments/round-two-2026-10-20/protocol.json"
PROTOCOL = json.loads(Path(PROTOCOL_PATH).read_text(encoding="utf-8"))


def _fake_capture(monkeypatch, *, hours_after_cutoff=3):
    from gpt_trader.cli.commands import experiment as command
    from gpt_trader.features.recorder import candle_capture

    now = aware_time(PROTOCOL["cutoff"]) + timedelta(hours=hours_after_cutoff)
    monkeypatch.setattr(command, "_utc_now", lambda: now)

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


def test_fetch_waits_until_the_last_bar_is_final(tmp_path, capsys, monkeypatch):
    _fake_capture(monkeypatch, hours_after_cutoff=1)
    args = ["experiment", "fetch-input", "--protocol", PROTOCOL_PATH, "--symbol", "BTC-USD"]
    assert main([*args, "--out", str(tmp_path / "early"), "--format", "json"]) == 1
    assert "may not be final" in json.loads(capsys.readouterr().out)["errors"][0]["message"]
    assert not (tmp_path / "early").exists()


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
            entries = []
            for row in rows:
                if "not_run" in row:
                    entries.append(row)
                    continue
                root = str(tmp_path / f"{rule}-{case}-{row['symbol']}")
                reports[root] = row
                entries.append(root)
            mapping[rule][case] = entries
    monkeypatch.setattr(command, "_inspect", lambda root: reports[str(root)])
    path = tmp_path / "runs.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return path


def _ledger(tmp_path, calls):
    from gpt_trader.features.experiment.model_transport import SpendLedger, account_label

    start = aware_time(PROTOCOL["scored_from"])
    ledger = SpendLedger(tmp_path / "spend.sqlite", Decimal("4720"))
    for symbol, fee, slippage, count in calls:
        for turn in range(count):
            call = ledger.reserve(
                "claude-fable-5-1",
                Decimal("0.01"),
                account=account_label(symbol, fee, slippage),
                decision_time=(start + timedelta(hours=1 + 4 * min(turn, 209))).isoformat(),
            )
            ledger.settle(call, Decimal("0.005"), "replied")
    return ledger


def _read(runs_file, *extra):
    command = ["experiment", "read", "--protocol", PROTOCOL_PATH, "--runs", str(runs_file)]
    return main([*command, "--source-sha256", DIGEST, *extra, "--format", "json"])


def test_read_applies_the_round_two_bar_and_reconciles_the_ledger(tmp_path, capsys, monkeypatch):
    every = [
        (symbol, costs["fee_bps"], costs["slippage_bps"], 210)
        for symbol in PROTOCOL["symbols"]
        for costs in PROTOCOL["cost_cases"].values()
    ]
    # One re-asked bar on the first account, after a crash before its commit.
    every[0] = (*every[0][:3], 211)
    ledger = _ledger(tmp_path, every)
    runs_file = _runs_file(tmp_path, monkeypatch, runs())
    assert _read(runs_file, "--spend-ledger", str(ledger.path)) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert (result["verdict"], result["action"]) == ("passes", "propose-round-three")
    assert result["source_sha256"] == DIGEST
    spend = result["model_spend"]
    assert spend["journal_turns"] == 16 * 210 and spend["re_asked_calls"] == 1
    assert spend["breaches"] == []
    assert Decimal(spend["ledger"]["charged_usd"]) == ledger.spent_usd()

    assert _read(runs_file) == 0
    assert json.loads(capsys.readouterr().out)["data"]["model_spend"]["ledger"] is None


def test_read_flags_ledger_calls_outside_the_runs_file(tmp_path, capsys, monkeypatch):
    def not_started(grouped):
        stress = grouped["ai-trade-manager"]["stress"]
        stress[-1] = {"symbol": stress[-1]["symbol"], "not_run": "spend stop: never started"}

    ledger = _ledger(tmp_path, [("DOT-USD", "60", "10", 5), ("BTC-USD", "10", "5", 3)])
    runs_file = _runs_file(tmp_path, monkeypatch, runs(edit=not_started))
    assert _read(runs_file, "--spend-ledger", str(ledger.path)) == 0
    breaches = json.loads(capsys.readouterr().out)["data"]["model_spend"]["breaches"]
    assert breaches[0].startswith("ledger calls for an account outside the runs file: DOT-USD")
    assert any("fewer ledger calls than journaled turns: BTC-USD" in item for item in breaches)


def test_read_refuses_misfit_runs_a_missing_digest_and_round_one_flags(
    tmp_path, capsys, monkeypatch
):
    def drifted(grouped):
        grouped["ai-trade-manager"]["base"][0]["manager"]["settings"]["max_tokens"] = 8000

    runs_file = _runs_file(tmp_path, monkeypatch, runs(edit=drifted))
    assert _read(runs_file) == 1
    assert (
        "frozen ai-trade-manager settings"
        in json.loads(capsys.readouterr().out)["errors"][0]["message"]
    )
    command = ["experiment", "read", "--protocol", PROTOCOL_PATH, "--runs", str(runs_file)]
    assert main([*command, "--format", "json"]) == 1
    assert "--source-sha256" in json.loads(capsys.readouterr().out)["errors"][0]["message"]

    def omitted(grouped):
        grouped["mean-reversion"]["base"].pop()

    assert _read(_runs_file(tmp_path, monkeypatch, runs(edit=omitted))) == 1
    assert "exactly once" in json.loads(capsys.readouterr().out)["errors"][0]["message"]
    held_out = "config/experiments/held-out-2026-09-06/protocol.json"
    command = ["experiment", "read", "--protocol", held_out, "--runs", str(runs_file)]
    assert main([*command, "--spend-ledger", str(tmp_path / "x"), "--format", "json"]) == 1
    assert "apply only" in json.loads(capsys.readouterr().out)["errors"][0]["message"]


def test_read_flags_a_second_run_of_an_account(tmp_path, capsys, monkeypatch):
    every = [
        (symbol, costs["fee_bps"], costs["slippage_bps"], 210)
        for symbol in PROTOCOL["symbols"]
        for costs in PROTOCOL["cost_cases"].values()
    ]
    ledger = _ledger(tmp_path, [*every, ("DOT-USD", "10", "5", 50)])
    runs_file = _runs_file(tmp_path, monkeypatch, runs())
    assert _read(runs_file, "--spend-ledger", str(ledger.path)) == 0
    spend = json.loads(capsys.readouterr().out)["data"]["model_spend"]
    assert spend["breaches"] == [
        "decision times go back to an earlier bar, a second run: DOT-USD fee_bps=10 slippage_bps=5"
    ]
