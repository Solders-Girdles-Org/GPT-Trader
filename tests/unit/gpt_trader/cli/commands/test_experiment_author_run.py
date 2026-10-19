"""Author forward-run path through the CLI: frozen inputs, stub transport, read-only report."""

import json
from decimal import Decimal

from gpt_trader.cli import main
from gpt_trader.features.experiment.rulebook import STARTING_RULEBOOK
from gpt_trader.features.experiment.trade_manager import TransportReply
from tests.unit.gpt_trader.cli.commands.test_experiment_round_two import PROTOCOL, _fake_capture

STARTING = {**STARTING_RULEBOOK.to_dict(), "entry": "cash"}
AUTHOR = {
    "starting_rulebook": STARTING,
    "cadence_bars": 168,
    "history_days": 30,
    "model": "claude-fable-5-1",
    "effort": "high",
    "max_tokens": 16000,
}


def _protocol(tmp_path, name="protocol.json", **changes):
    protocol = {
        key: value for key, value in PROTOCOL.items() if key not in ("manager", "reading")
    } | {"benchmarks": ["ai-rulebook", "mean-reversion", "baseline"], "author": AUTHOR}
    path = tmp_path / name
    path.write_text(json.dumps(protocol | changes), encoding="utf-8")
    return path


def _fetch(tmp_path, protocol, symbol):
    out = tmp_path / symbol
    command = ["experiment", "fetch-input", "--protocol", str(protocol), "--symbol", symbol]
    return main([*command, "--out", str(out), "--format", "json"]), out


class Stub:
    model = "claude-fable-5-1"

    def __init__(self):
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        rules = {
            symbol: {**raw, "version": raw["version"] + 1}
            for symbol, raw in request["context"]["rulebooks"].items()
        }
        text = json.dumps({"rulebooks": rules})
        return TransportReply(self.model, text, "end_turn", 0, 0, 0, Decimal(0), Decimal(0))


def test_fetch_freezes_starting_rulebook_and_run_reports(tmp_path, capsys, monkeypatch):
    from gpt_trader.cli.commands import experiment

    _fake_capture(monkeypatch)
    protocol = _protocol(tmp_path)
    inputs = []
    for symbol in ("BTC-USD", "ETH-USD"):
        code, out = _fetch(tmp_path, protocol, symbol)
        assert code == 0
        payload = json.loads((out / "ai-rulebook-base.json").read_text())
        assert payload["rulebooks"] == [
            {"effective_from": PROTOCOL["scored_from"], "rules": STARTING}
        ]
        assert "rulebooks" not in json.loads((out / "baseline-base.json").read_text())
        inputs.append(out / "ai-rulebook-base.json")
    verify = ["experiment", "verify-input", "--capture", str(tmp_path / "BTC-USD" / "capture.json")]
    assert main([*verify, "--input", str(inputs[0]), "--format", "json"]) == 0
    capsys.readouterr()
    stub = Stub()

    def factory(manager, ledger, *, reply_schema, run):
        assert manager.max_tokens == 16000 and run == str((tmp_path / "run").resolve())
        assert ledger.path == tmp_path / "spend.sqlite"
        stub.ledger = ledger
        return stub

    monkeypatch.setattr(experiment, "from_environment", factory)
    command = ["experiment", "author-run", "--protocol", str(protocol)]
    for path in inputs:
        command += ["--input", str(path)]
    command += ["--root", str(tmp_path / "run"), "--model-transport", "anthropic"]
    command += ["--spend-ledger", str(tmp_path / "spend.sqlite"), "--spend-cap-usd", "20"]
    assert main([*command, "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["complete"] and result["total_bars"] == 912
    assert result["author"]["turns"] == 5 == len(stub.requests)
    assert result["ledger"] == {"path": str((tmp_path / "spend.sqlite").resolve()), "cap": "20"}
    assert [row["symbol"] for row in result["symbols"]] == ["BTC-USD", "ETH-USD"]
    inspect = ["experiment", "author-inspect", "--root", str(tmp_path / "run")]
    assert main([*inspect, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == result


def test_run_refuses_inputs_not_frozen_by_protocol(tmp_path, capsys, monkeypatch):
    from gpt_trader.cli.commands import experiment

    _fake_capture(monkeypatch)
    original = _protocol(tmp_path)
    code, out = _fetch(tmp_path, original, "BTC-USD")
    assert code == 0
    changed = _protocol(
        tmp_path,
        "changed.json",
        author={**AUTHOR, "starting_rulebook": STARTING_RULEBOOK.to_dict()},
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("client must not be created")

    monkeypatch.setattr(experiment, "from_environment", forbidden)
    capsys.readouterr()
    command = ["experiment", "author-run", "--protocol", str(changed)]
    command += ["--input", str(out / "ai-rulebook-base.json"), "--root", str(tmp_path / "run")]
    command += ["--model-transport", "anthropic", "--spend-ledger", str(tmp_path / "s.sqlite")]
    assert main([*command, "--spend-cap-usd", "20", "--format", "json"]) == 1
    error = json.loads(capsys.readouterr().out)["errors"][0]
    assert error["code"] == "CONFIG_INVALID" and "starting rulebook" in error["message"]
    assert not (tmp_path / "run").exists()
    inside = [*command[:3], str(original), *command[4:-1], str(tmp_path / "run" / "s.sqlite")]
    assert main([*inside, "--spend-cap-usd", "20", "--format", "json"]) == 1
    assert "outside" in json.loads(capsys.readouterr().out)["errors"][0]["message"]


def test_fetch_refuses_rulebook_protocol_without_author(tmp_path, capsys, monkeypatch):
    _fake_capture(monkeypatch)
    protocol = _protocol(tmp_path, author={**AUTHOR, "cadence_bars": 0})
    code, out = _fetch(tmp_path, protocol, "BTC-USD")
    assert code == 1
    assert "'author' settings" in json.loads(capsys.readouterr().out)["errors"][0]["message"]
    assert not out.exists()


def test_inspect_missing_run_is_typed(tmp_path, capsys):
    assert main(["experiment", "author-inspect", "--root", str(tmp_path), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "RUN_NOT_FOUND"
