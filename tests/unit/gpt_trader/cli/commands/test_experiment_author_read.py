"""The frozen rulebook-author protocol end to end: fetch, rules, author runs, read.

Stub SDK client and a real spend ledger only; never a model call or credential.
"""

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from gpt_trader.cli import main
from gpt_trader.core.fill_accounting import aware_time
from gpt_trader.features.experiment.inputs import implementation_digest

PROTOCOL_PATH = "config/experiments/rulebook-author-2026-10-22/protocol.json"
PROTOCOL = json.loads(Path(PROTOCOL_PATH).read_text(encoding="utf-8"))
SYMBOLS = ["BTC-USD", "ETH-USD"]


class Client:
    """Answers each author request with valid next rulebooks, like the SDK."""

    def __init__(self):
        self.messages = self
        self.calls = 0

    def create(self, **params):
        self.calls += 1
        context = json.loads(params["messages"][0]["content"])
        rules = {
            symbol: {**raw, "version": raw["version"] + 1}
            for symbol, raw in context["rulebooks"].items()
        }
        return SimpleNamespace(
            model="claude-fable-5-1",
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps({"rulebooks": rules}))],
            usage=SimpleNamespace(
                input_tokens=20000,
                output_tokens=3000,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                cache_creation=None,
            ),
        )


# The frozen settings and reading on a shorter window, so 1,560-bar runs stay out
# of unit tests: 3 warm-up days and 8 scored days (two author turns per case).
START = aware_time(PROTOCOL["scored_from"])
WINDOW = {
    "warmup_start": (START - timedelta(days=3)).isoformat(),
    "cutoff": (START + timedelta(days=8)).isoformat(),
    "bars_per_symbol": 264,
}


def _protocol(tmp_path):
    reduced = PROTOCOL | WINDOW | {"symbols": SYMBOLS, "min_scored_symbols": 2}
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(reduced), encoding="utf-8")
    return path


def _fake_capture(monkeypatch):
    from gpt_trader.cli.commands import experiment as command
    from gpt_trader.features.recorder import candle_capture

    now = aware_time(WINDOW["cutoff"]) + timedelta(hours=3)
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
            for hour in range(WINDOW["bars_per_symbol"])
        ]
        capture = {
            "schema": candle_capture.CAPTURE_SCHEMA,
            "symbol": symbol,
            "granularity": "ONE_HOUR",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "fetched_at": "2026-11-26T03:00:00+00:00",
            "requests": [{"response": {"candles": candles}}],
        }
        return json.dumps(capture, sort_keys=True).encode()

    monkeypatch.setattr(candle_capture, "capture_public_hourly_candles", fake_capture)


def _ok(command, capsys):
    assert main([*command, "--format", "json"]) == 0, capsys.readouterr().out
    return json.loads(capsys.readouterr().out)["data"]


def test_fetch_run_and_read_the_frozen_protocol(tmp_path, capsys, monkeypatch):
    from gpt_trader.cli.commands import experiment
    from gpt_trader.features.experiment.model_transport import AnthropicTransport

    _fake_capture(monkeypatch)
    protocol = _protocol(tmp_path)
    for symbol in SYMBOLS:
        fetch = ["experiment", "fetch-input", "--protocol", str(protocol), "--symbol", symbol]
        _ok([*fetch, "--out", str(tmp_path / "inputs" / symbol)], capsys)
    runs = {}
    for rule in ("mean-reversion", "baseline"):
        runs[rule] = {}
        for case in PROTOCOL["cost_cases"]:
            roots = []
            for symbol in SYMBOLS:
                root = tmp_path / "runs" / f"{rule}-{case}-{symbol}"
                source = tmp_path / "inputs" / symbol / f"{rule}-{case}.json"
                _ok(["experiment", "run", "--input", str(source), "--root", str(root)], capsys)
                roots.append(str(root))
            runs[rule][case] = roots
    client = Client()

    def factory(manager, ledger, *, reply_schema, run):
        return AnthropicTransport(manager, ledger, client, run=run, reply_schema=reply_schema)

    monkeypatch.setattr(experiment, "from_environment", factory)
    ledger = tmp_path / "spend-ledger.sqlite"
    runs["ai-rulebook"] = {}
    for case in PROTOCOL["cost_cases"]:
        root = tmp_path / "runs" / f"ai-rulebook-{case}"
        command = ["experiment", "author-run", "--protocol", str(protocol)]
        for symbol in SYMBOLS:
            command += ["--input", str(tmp_path / "inputs" / symbol / f"ai-rulebook-{case}.json")]
        command += ["--root", str(root), "--model-transport", "anthropic"]
        command += ["--spend-ledger", str(ledger), "--spend-cap-usd", "25"]
        report = _ok(command, capsys)
        assert report["complete"] and report["author"]["outcomes"]["accepted"] == 2
        runs["ai-rulebook"][case] = str(root)
    assert client.calls == 4
    runs_file = tmp_path / "runs.json"
    runs_file.write_text(json.dumps(runs))
    read = ["experiment", "read", "--protocol", str(protocol), "--runs", str(runs_file)]
    read += ["--source-sha256", implementation_digest(), "--spend-ledger", str(ledger)]
    result = _ok(read, capsys)
    # Flat prices: the author stays in cash, which does not beat cash by the margin.
    assert result["verdict"] == "fails"
    assert result["action"] == "one-fresh-window-then-pause-if-not-passed"
    assert result["author"]["base"]["compared_symbols"] == SYMBOLS
    assert all(result["author"][case]["author"]["checks"].values() for case in ("base", "stress"))
    spend = result["model_spend"]
    assert spend["breaches"] == [] and spend["journal_turns"] == 4
    assert spend["re_asked_calls"] == 0
    # A digest other than the frozen one is refused, never read.
    wrong = [*read[:-4], "--source-sha256", "0" * 64, "--spend-ledger", str(ledger)]
    assert main([*wrong, "--format", "json"]) == 1
    assert "not the frozen" in json.loads(capsys.readouterr().out)["errors"][0]["message"]


def test_author_reading_requires_digest_and_ledger(tmp_path, capsys):
    runs_file = tmp_path / "runs.json"
    runs_file.write_text("{}")
    command = ["experiment", "read", "--protocol", PROTOCOL_PATH, "--runs", str(runs_file)]
    assert main([*command, "--format", "json"]) == 1
    error = json.loads(capsys.readouterr().out)["errors"][0]
    assert error["code"] == "MISSING_ARGUMENT" and "--source-sha256" in error["message"]
