"""Author plumbing tests: synthetic bars, injected replies, never model calls."""

import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from gpt_trader.features.experiment.author_runner import run_author_smoke
from gpt_trader.features.experiment.inputs import ManagerSettings, parse_input
from gpt_trader.features.experiment.ledger import ExperimentIntegrityError
from gpt_trader.features.experiment.model_transport import (
    AnthropicTransport,
    SpendCeilingReached,
    SpendLedger,
)
from gpt_trader.features.experiment.rulebook_author import (
    AuthorSettings,
    build_author_request,
    daily_bars,
    estimate_author_spend,
    reply_schema,
    validate_portfolio,
)
from gpt_trader.features.experiment.trade_manager import TransportError, TransportReply


class AuthorStub:
    model = "claude-fable-5-1"

    def __init__(self, mode="valid"):
        self.requests = []
        self.mode = mode

    def decide(self, request):
        self.requests.append(request)
        if self.mode == "failure":
            raise TransportError("stub timeout")
        rules = {
            symbol: {**raw, "version": raw["version"] + 1, "entry": "cash"}
            for symbol, raw in request["context"]["rulebooks"].items()
        }
        if self.mode == "invalid":
            rules[next(iter(rules))]["exposure_fraction"] = "1"
        text = json.dumps({"rulebooks": rules})
        if self.mode == "malformed":
            text = "not json"
        return TransportReply(
            self.model,
            text,
            "refusal" if self.mode == "refusal" else "end_turn",
            1200,
            500,
            25,
            Decimal(10),
            Decimal(50),
        )


@pytest.fixture
def portfolio(experiment_payload):
    return [
        parse_input({**experiment_payload, "symbol": symbol, "benchmark": "ai-rulebook"})
        for symbol in ("BTC-USD", "ETH-USD")
    ]


def test_valid_cadence_resume_and_rules_reach_engine(portfolio, tmp_path):
    transport = AuthorStub()
    settings = AuthorSettings(cadence_bars=24)
    result = run_author_smoke(
        portfolio, tmp_path / "smoke", ManagerSettings(), transport, settings=settings
    )
    assert result["not_evidence"] and result["accepted"] == 3
    assert len(transport.requests) == 3
    assert all(
        rule["entry"] == "cash" and rule["version"] == 4 for rule in result["rulebooks"].values()
    )
    # First turn has no complete UTC day; second has one. Future bars never leak.
    assert transport.requests[0]["context"]["daily_bars"]["BTC-USD"] == []
    assert len(transport.requests[1]["context"]["daily_bars"]["BTC-USD"]) == 1
    assert set(transport.requests[1]["context"]["accounts"]) == {"BTC-USD", "ETH-USD"}
    assert (tmp_path / "smoke" / "NOT-EVIDENCE.txt").is_file()
    again = AuthorStub("failure")
    assert (
        run_author_smoke(portfolio, tmp_path / "smoke", ManagerSettings(), again, settings=settings)
        == result
    )
    assert not again.requests
    import sqlite3

    with sqlite3.connect(tmp_path / "smoke" / "author.sqlite3") as connection:
        event = json.loads(
            connection.execute("SELECT payload FROM bars WHERE sequence=24").fetchone()[0]
        )
    assert all(
        e["state"]["quantity"] == "0" and e["state"]["rulebook"]["version"] == 3
        for e in event["events"].values()
    )


@pytest.mark.parametrize("mode", ["invalid", "refusal", "failure", "malformed"])
def test_bad_turn_preserves_all_rules_and_replays(portfolio, tmp_path, mode):
    stub = AuthorStub(mode)
    result = run_author_smoke(portfolio, tmp_path / "smoke", ManagerSettings(), stub, max_turns=1)
    assert result["accepted"] == 0
    assert result["rulebooks"] == {s.symbol: s.payload["rulebooks"][0]["rules"] for s in portfolio}
    replay = AuthorStub()
    assert (
        run_author_smoke(portfolio, tmp_path / "smoke", ManagerSettings(), replay, max_turns=1)
        == result
    )
    assert not replay.requests


def test_interrupted_run_replays_committed_turns(portfolio, tmp_path):
    class CrashAfterFirst(AuthorStub):
        def decide(self, request):
            if self.requests:
                raise RuntimeError("crash before commit")
            return super().decide(request)

    settings = AuthorSettings(cadence_bars=24)
    with pytest.raises(RuntimeError, match="crash"):
        run_author_smoke(
            portfolio, tmp_path / "smoke", ManagerSettings(), CrashAfterFirst(), settings=settings
        )
    resumed = AuthorStub()
    result = run_author_smoke(
        portfolio, tmp_path / "smoke", ManagerSettings(), resumed, settings=settings
    )
    assert len(resumed.requests) == 2 and result["accepted"] == 3
    assert resumed.requests[0]["context"]["rulebooks"]["BTC-USD"]["version"] == 2


def test_changed_input_and_tamper_rejected(portfolio, tmp_path):
    import sqlite3

    root = tmp_path / "smoke"
    run_author_smoke(portfolio, root, ManagerSettings(), AuthorStub(), max_turns=1)
    with pytest.raises(ExperimentIntegrityError, match="binding"):
        run_author_smoke(
            portfolio, root, ManagerSettings(max_tokens=100), AuthorStub(), max_turns=1
        )
    with sqlite3.connect(root / "author.sqlite3") as connection:
        connection.execute("UPDATE bars SET checksum='bad'")
    with pytest.raises(ExperimentIntegrityError, match="replay"):
        run_author_smoke(portfolio, root, ManagerSettings(), AuthorStub(), max_turns=1)


def test_offline_estimate_bounds_actual_request(portfolio):
    manager = ManagerSettings(max_tokens=16000)
    settings = AuthorSettings(cadence_bars=24)
    estimate = estimate_author_spend(portfolio, manager, settings)
    assert estimate["turns"] == 3 and estimate["cost_cases"] == 1
    assert Decimal(estimate["worst_case_usd"]) < 50
    assert not estimate["real_tokens_measured"]
    request = build_author_request(
        portfolio,
        48,
        {"BTC-USD": {"cash": "1000"}},
        {s.symbol: s.payload["rulebooks"][0]["rules"] for s in portfolio},
        settings,
    )
    from gpt_trader.features.experiment.model_transport import input_token_bound, wire_params

    assert (
        input_token_bound(
            wire_params(request, manager, reply_schema([s.symbol for s in portfolio]))
        )
        <= estimate["input_token_bound_max"]
    )


def test_portfolio_rejects_mixed_costs_and_duplicate_symbols(portfolio):
    with pytest.raises(ValueError, match="distinct"):
        validate_portfolio([portfolio[0], portfolio[0]])
    mixed = replace(portfolio[1], settings=replace(portfolio[1].settings, fee_bps=Decimal(60)))
    with pytest.raises(ValueError, match="share"):
        validate_portfolio([portfolio[0], mixed])
    assert len(daily_bars(portfolio[0], 59, 30)) == 2


def test_spend_ceiling_and_uncommitted_call_stay_counted(portfolio, tmp_path):
    class SDK:
        def __init__(self):
            self.messages = self
            self.calls = []

        def create(self, **params):
            self.calls.append(params)
            raise RuntimeError("crash after send")

    manager = ManagerSettings()
    ledger = SpendLedger(tmp_path / "ledger.sqlite", Decimal("0.8"))
    client = SDK()
    transport = AnthropicTransport(
        manager, ledger, client, reply_schema=reply_schema([s.symbol for s in portfolio])
    )
    with pytest.raises(RuntimeError, match="crash"):
        run_author_smoke(portfolio, tmp_path / "smoke", manager, transport, max_turns=1)
    assert len(client.calls) == 1
    assert "rulebooks" in client.calls[0]["output_config"]["format"]["schema"]["properties"]
    assert ledger.spent_usd() > 0
    with pytest.raises(SpendCeilingReached):
        run_author_smoke(portfolio, tmp_path / "smoke", manager, transport, max_turns=1)
    assert len(client.calls) == 1


def test_default_off_and_scratch_only(portfolio, tmp_path):
    with pytest.raises(ValueError, match="scratch"):
        run_author_smoke(
            portfolio, tmp_path / "runtime_data" / "smoke", ManagerSettings(), AuthorStub()
        )
    with pytest.raises(ValueError, match="three"):
        run_author_smoke(
            portfolio, tmp_path / "smoke", ManagerSettings(), AuthorStub(), max_turns=4
        )
    with pytest.raises(ValueError, match="injected"):
        run_author_smoke(portfolio, tmp_path / "smoke", ManagerSettings(), max_turns=1)


def test_real_transport_stub_journals_usage_and_charges(portfolio, tmp_path):
    class Client:
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
                    input_tokens=1200,
                    output_tokens=500,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                    cache_creation=None,
                ),
            )

    manager = ManagerSettings()
    ledger = SpendLedger(tmp_path / "ledger.sqlite", Decimal("10"))
    client = Client()
    transport = AnthropicTransport(
        manager, ledger, client, reply_schema=reply_schema([s.symbol for s in portfolio])
    )
    root = tmp_path / "smoke"
    result = run_author_smoke(portfolio, root, manager, transport, max_turns=1)
    assert result["accepted"] == 1
    assert ledger.spent_usd() == Decimal("0.037")
    assert client.calls == 1
    assert run_author_smoke(portfolio, root, manager, transport, max_turns=1) == result
    assert client.calls == 1
    assert ledger.calls()[0]["decision_time"] == "2026-01-01T01:00:00+00:00"
    import sqlite3

    with sqlite3.connect(root / "author.sqlite3") as connection:
        record = json.loads(connection.execute("SELECT payload FROM bars").fetchone()[0])["turn"]
    assert record["reply"]["input_tokens"] == 1200
    assert record["reply"]["cost_usd"] == "0.037"
