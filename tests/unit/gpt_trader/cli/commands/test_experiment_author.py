"""CLI author mode uses stub SDK transport only; estimates never create clients."""

import json

from gpt_trader.cli import main


def test_author_estimate_portfolio_offline(tmp_path, capsys, monkeypatch):
    experiment_payload = {
        "schema_version": 1,
        "source": "synthetic CLI author fixture",
        "symbol": "BTC-USD",
        "recorded_at": "2026-01-01T01:00:00Z",
        "candles": [
            {
                "ts": "2026-01-01T00:00:00Z",
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "1",
            }
        ],
    }
    from gpt_trader.features.experiment import model_transport

    def forbidden(*args, **kwargs):
        raise AssertionError("offline estimate created client")

    monkeypatch.setattr(model_transport, "from_environment", forbidden)
    paths = []
    for symbol in ("BTC-USD", "ETH-USD"):
        path = tmp_path / f"{symbol}.json"
        path.write_text(
            json.dumps({**experiment_payload, "symbol": symbol, "benchmark": "ai-rulebook"})
        )
        paths.append(str(path))
    result = main(
        [
            "experiment",
            "estimate-spend",
            "--author",
            "--input",
            paths[0],
            "--portfolio-input",
            paths[1],
            "--format",
            "json",
        ]
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["turns"] == 1
    assert payload["data"]["symbols"] == ["BTC-USD", "ETH-USD"]
    assert not list(tmp_path.glob("*.sqlite*"))


def test_author_smoke_cli_uses_author_schema(tmp_path, capsys, monkeypatch):
    from decimal import Decimal

    from gpt_trader.cli.commands import experiment
    from gpt_trader.features.experiment.trade_manager import TransportReply

    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "synthetic smoke fixture",
                "symbol": "BTC-USD",
                "benchmark": "ai-rulebook",
                "recorded_at": "2026-01-01T01:00:00Z",
                "candles": [
                    {
                        "ts": "2026-01-01T00:00:00Z",
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
    called = []

    class Stub:
        model = "claude-fable-5-1"

        def decide(self, request):
            called.append(request)
            current = request["context"]["rulebooks"]["BTC-USD"]
            text = json.dumps({"rulebooks": {"BTC-USD": {**current, "version": 2}}})
            return TransportReply(self.model, text, "end_turn", 0, 0, 0, Decimal(0), Decimal(0))

    def factory(manager, ledger, *, reply_schema, run):
        assert "rulebooks" in reply_schema["properties"]
        assert ledger.path == tmp_path / "spend.sqlite"
        assert run == str(tmp_path / "scratch")
        return Stub()

    monkeypatch.setattr(experiment, "from_environment", factory)
    assert (
        main(
            [
                "experiment",
                "author-smoke",
                "--input",
                str(path),
                "--root",
                str(tmp_path / "scratch"),
                "--model-transport",
                "anthropic",
                "--spend-ledger",
                str(tmp_path / "spend.sqlite"),
                "--spend-cap-usd",
                "10",
                "--max-turns",
                "1",
                "--format",
                "json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["not_evidence"] and result["accepted"] == 1 and len(called) == 1
