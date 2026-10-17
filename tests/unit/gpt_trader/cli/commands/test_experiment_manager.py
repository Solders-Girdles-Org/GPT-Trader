"""The AI trade manager channel is selectable but refused while no transport ships."""

import json

import pytest

from gpt_trader.cli import main


def test_cli_manager_channel_without_transport_creates_no_run(tmp_path, capsys):
    source = tmp_path / "input.json"
    bar = {"ts": "2025-01-01T00:00:00Z", "open": "100", "high": "101", "low": "99"}
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "synthetic:CLI contract",
                "symbol": "BTC-USD",
                "recorded_at": "2025-01-01T01:00:00Z",
                "candles": [{**bar, "close": "100", "volume": "1"}],
            }
        )
    )
    root = tmp_path / "run"
    command = ["experiment", "run", "--input", str(source), "--root", str(root)]
    assert main([*command, "--benchmark", "ai-trade-manager", "--format", "json"]) == 1
    error = json.loads(capsys.readouterr().out)["errors"][0]
    assert error["code"] == "OPERATION_FAILED"
    assert "needs a model transport" in error["message"]
    assert not root.exists()


def test_cli_run_help_names_the_missing_transport(capsys):
    with pytest.raises(SystemExit):
        main(["experiment", "run", "--help"])
    run_help = " ".join(capsys.readouterr().out.split())
    assert "ai-trade-manager needs a model transport" in run_help


def test_cli_resume_of_a_manager_run_advances_nothing(tmp_path, capsys):
    from decimal import Decimal

    from gpt_trader.features.experiment.engine import run_experiment
    from gpt_trader.features.experiment.inputs import parse_input
    from gpt_trader.features.experiment.ledger import inspect_run
    from gpt_trader.features.experiment.trade_manager import TransportReply

    class Hold:
        model = "fake-model-1"

        def decide(self, request):
            text = json.dumps({"action": "hold", "reason": "wait"})
            return TransportReply(self.model, text, "end_turn", 1, 1, 1, Decimal(0), Decimal(0))

    bars = [
        {
            "ts": f"2025-01-01T{hour:02d}:00:00+00:00",
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100",
            "volume": "1",
        }
        for hour in range(6)
    ]
    payload = {
        "schema_version": 1,
        "source": "synthetic:CLI contract",
        "symbol": "BTC-USD",
        "recorded_at": "2025-01-01T06:00:00+00:00",
        "candles": bars,
        "benchmark": "ai-trade-manager",
    }
    root = tmp_path / "run"
    run_experiment(root, parse_input(payload), max_bars=1, transport=Hold())
    assert main(["experiment", "run", "--root", str(root), "--format", "json"]) == 1
    assert "needs a model transport" in json.loads(capsys.readouterr().out)["errors"][0]["message"]
    assert inspect_run(root)["processed_bars"] == 1
