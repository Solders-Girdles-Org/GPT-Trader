"""The AI trade manager channel runs only behind the explicit paid-transport opt-in."""

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


def test_cli_run_help_names_the_transport_opt_in(capsys):
    with pytest.raises(SystemExit):
        main(["experiment", "run", "--help"])
    run_help = "".join(capsys.readouterr().out.split())
    assert "ai-trade-managerrunsonlywith--model-transport" in run_help
    assert "acallthatcouldpassitisneversent" in run_help


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


def _manager_input(tmp_path, bars=6, benchmark="ai-trade-manager"):
    candles = [
        {
            "ts": f"2025-01-01T{hour:02d}:00:00+00:00",
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100",
            "volume": "1",
        }
        for hour in range(bars)
    ]
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "synthetic:CLI contract",
                "symbol": "BTC-USD",
                "recorded_at": f"2025-01-01T{bars:02d}:00:00+00:00",
                "candles": candles,
                "benchmark": benchmark,
                **({"manager": {"cadence_bars": 1}} if benchmark == "ai-trade-manager" else {}),
            }
        )
    )
    return path


def _run(tmp_path, *extra, benchmark="ai-trade-manager"):
    command = ["experiment", "run", "--input", str(_manager_input(tmp_path, benchmark=benchmark))]
    return main([*command, "--root", str(tmp_path / "run"), *extra, "--format", "json"])


def _error(capsys):
    return json.loads(capsys.readouterr().out)["errors"][0]


@pytest.fixture
def stub_transport(monkeypatch):
    """Replace only the SDK client; the real transport and spend ledger run."""
    from gpt_trader.cli.commands import experiment as command
    from gpt_trader.features.experiment.model_transport import AnthropicTransport
    from tests.unit.gpt_trader.features.experiment.test_model_transport import StubClient

    built = []

    def stubbed(manager, ledger):
        built.append(AnthropicTransport(manager, ledger, StubClient()))
        return built[-1]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    monkeypatch.setattr(command, "from_environment", stubbed)
    return built


def test_cli_transport_requires_a_ledger_and_cap(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    assert _run(tmp_path, "--model-transport", "anthropic", "--spend-cap-usd", "5") == 1
    error = _error(capsys)
    assert error["code"] == "MISSING_ARGUMENT" and "--spend-ledger" in error["message"]
    assert not (tmp_path / "run").exists()


def test_cli_transport_requires_the_environment_credential(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ledger = tmp_path / "spend.sqlite"
    flags = ["--model-transport", "anthropic", "--spend-ledger", str(ledger)]
    assert _run(tmp_path, *flags, "--spend-cap-usd", "5") == 1
    assert "ANTHROPIC_API_KEY" in _error(capsys)["message"]
    assert not ledger.exists() and not (tmp_path / "run").exists()


def test_cli_transport_refuses_other_channels_and_a_ledger_inside_the_run(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    flags = ["--model-transport", "anthropic", "--spend-cap-usd", "5"]
    inside = ["--spend-ledger", str(tmp_path / "run" / "spend.sqlite")]
    assert _run(tmp_path, *flags, *inside) == 1
    assert "outside the experiment root" in _error(capsys)["message"]
    outside = ["--spend-ledger", str(tmp_path / "spend.sqlite")]
    assert _run(tmp_path, *flags, *outside, benchmark="baseline") == 1
    assert "applies only to the ai-trade-manager channel" in _error(capsys)["message"]
    assert _run(tmp_path, *flags[:2], *outside, "--spend-cap-usd", "lots") == 1
    assert _error(capsys)["code"] == "CONFIG_INVALID"


def test_cli_runs_through_the_transport_and_stops_loudly_at_the_cap(
    tmp_path, capsys, stub_transport
):
    from decimal import Decimal

    from gpt_trader.features.experiment.ledger import inspect_run

    ledger = tmp_path / "spend.sqlite"
    flags = ["--model-transport", "anthropic", "--spend-ledger", str(ledger)]
    # Each call reserves its worst case (about $0.45 here) and settles at $0.09,
    # so a $0.60 cap allows a third call only while at most one has been made.
    assert _run(tmp_path, *flags, "--spend-cap-usd", "0.60", "--max-bars", "2") == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["processed_bars"] == 2 and result["manager"]["turns"] == 2
    (transport,) = stub_transport
    assert len(transport.client.messages.calls) == 2
    assert Decimal("0.42") < Decimal(transport.ledger.calls()[0]["reserved_usd"]) < Decimal("0.51")
    assert transport.ledger.spent_usd() == Decimal("0.18")
    resume = ["experiment", "run", "--root", str(tmp_path / "run"), *flags]
    assert main([*resume, "--spend-cap-usd", "0.60", "--format", "json"]) == 1
    error = _error(capsys)
    assert error["code"] == "OPERATION_FAILED"
    assert "run stopped: Spend ceiling reached" in error["message"]
    assert inspect_run(tmp_path / "run")["processed_bars"] == 2
    assert len(stub_transport[-1].client.messages.calls) == 0
    assert main([*resume, "--spend-cap-usd", "3", "--format", "json"]) == 1
    assert "bound to a $0.60 cap" in _error(capsys)["message"]


def test_cli_estimate_spend_makes_no_call(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    command = ["experiment", "estimate-spend", "--input", str(_manager_input(tmp_path))]
    assert main([*command, "--format", "json"]) == 0
    estimate = json.loads(capsys.readouterr().out)["data"]
    assert estimate["turns_without_fills"] == 6 and estimate["model"] == "claude-fable-5-1"


def test_cli_creates_nothing_when_the_sdk_is_missing_or_the_cap_is_too_small(
    tmp_path, capsys, monkeypatch
):
    import builtins

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    ledger = tmp_path / "spend.sqlite"
    flags = ["--model-transport", "anthropic", "--spend-ledger", str(ledger)]
    assert _run(tmp_path, *flags, "--spend-cap-usd", "0.10") == 1
    assert "too little room for one call" in _error(capsys)["message"]
    assert not ledger.exists() and not (tmp_path / "run").exists()
    real_import = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    assert _run(tmp_path, *flags, "--spend-cap-usd", "5") == 1
    assert "uv sync --extra ai-trade-manager" in _error(capsys)["message"]
    assert not ledger.exists() and not (tmp_path / "run").exists()


def test_cli_refuses_another_runs_journal_as_a_ledger(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    baseline = tmp_path / "baseline"
    source = ["experiment", "run", "--input", str(_manager_input(tmp_path, benchmark="baseline"))]
    assert main([*source, "--root", str(baseline), "--max-bars", "1", "--format", "json"]) == 0
    capsys.readouterr()
    journal = next(baseline.iterdir())
    before = journal.read_bytes()
    flags = ["--model-transport", "anthropic", "--spend-ledger", str(journal)]
    assert _run(tmp_path, *flags, "--spend-cap-usd", "5") == 1
    error = _error(capsys)
    assert error["code"] == "CONFIG_INVALID" and "is not a spend ledger" in error["message"]
    assert journal.read_bytes() == before and not (tmp_path / "run").exists()
