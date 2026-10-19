"""Full-window author run: synthetic bars, injected replies, never model calls."""

import json
import sqlite3
from decimal import Decimal

import pytest
from tests.unit.gpt_trader.features.experiment.test_rulebook_author import AuthorStub

from gpt_trader.core.fill_accounting import semantic_checksum
from gpt_trader.features.experiment import author_runner
from gpt_trader.features.experiment.author_runner import (
    inspect_author,
    run_author,
    run_author_smoke,
)
from gpt_trader.features.experiment.inputs import ManagerSettings, parse_input
from gpt_trader.features.experiment.ledger import ExperimentIntegrityError, encode
from gpt_trader.features.experiment.rulebook import STARTING_RULEBOOK
from gpt_trader.features.experiment.rulebook_author import AuthorSettings, protocol_author

SETTINGS = AuthorSettings(cadence_bars=24)


class HoldStub(AuthorStub):
    """Valid replies that hold, so the accounts trade and must reconcile."""

    def decide(self, request):
        reply = super().decide(request)
        rules = {
            symbol: {
                **raw,
                "version": raw["version"] + 1,
                "entry": "hold",
                "stop_fraction": "0.04",
                "target_fraction": "100",
            }
            for symbol, raw in request["context"]["rulebooks"].items()
        }
        return reply.__class__(**{**vars(reply), "text": json.dumps({"rulebooks": rules})})


@pytest.fixture
def portfolio(experiment_payload):
    return [
        parse_input({**experiment_payload, "symbol": symbol, "benchmark": "ai-rulebook"})
        for symbol in ("BTC-USD", "ETH-USD")
    ]


def test_full_window_runs_every_bar_and_reports_each_coin(portfolio, tmp_path):
    stub = HoldStub()
    report = run_author(portfolio, tmp_path / "run", ManagerSettings(), stub, settings=SETTINGS)
    assert report["complete"] and report["processed_bars"] == 60
    assert len(stub.requests) == 3
    assert report["author"]["turns"] == 3
    assert report["author"]["outcomes"] == {"accepted": 3, "rejected": 0, "failed": 0}
    assert report["author"]["models"] == ["claude-fable-5-1"]
    assert report["author"]["cost_usd"] == "0.111"  # 3 stub turns at $0.037
    assert not (tmp_path / "run" / "NOT-EVIDENCE.txt").exists()
    symbols = {row["symbol"]: row for row in report["symbols"]}
    assert set(symbols) == {"BTC-USD", "ETH-USD"}
    for row in symbols.values():
        assert row["complete"] and row["benchmark"] == "ai-rulebook"
        assert row["account"]["reconciled"] and row["account"]["fill_count"] >= 1
        assert row["end_valuation"]["comparators"]["hold_the_coin"] is not None
        assert row["rulebook"]["version"] == 4
        assert [item["rules"]["version"] for item in row["rulebook"]["activations"]] == [2, 3, 4]
    assert inspect_author(tmp_path / "run") == report


def test_resume_equals_uninterrupted_and_never_reasks(portfolio, tmp_path):
    whole = run_author(
        portfolio, tmp_path / "whole", ManagerSettings(), HoldStub(), settings=SETTINGS
    )
    split = tmp_path / "split"
    partial = run_author(
        portfolio, split, ManagerSettings(), HoldStub(), settings=SETTINGS, max_bars=30
    )
    assert not partial["complete"] and partial["processed_bars"] == 30
    assert partial["author"]["turns"] == 2
    resumed_stub = HoldStub()
    resumed = run_author(portfolio, split, ManagerSettings(), resumed_stub, settings=SETTINGS)
    assert len(resumed_stub.requests) == 1
    assert [row["account"] for row in resumed["symbols"]] == [
        row["account"] for row in whole["symbols"]
    ]
    finished = HoldStub("failure")
    run_author(portfolio, split, ManagerSettings(), finished, settings=SETTINGS)
    assert not finished.requests


@pytest.mark.parametrize(("mode", "outcome"), [("failure", "failed"), ("invalid", "rejected")])
def test_bad_turns_are_classified_and_keep_rules(portfolio, tmp_path, mode, outcome):
    report = run_author(
        portfolio, tmp_path / "run", ManagerSettings(), AuthorStub(mode), settings=SETTINGS
    )
    assert report["author"]["outcomes"][outcome] == 3
    assert len(report["author"]["errors"]) == 3
    assert all(row["rulebook"]["version"] == 1 for row in report["symbols"])


def test_changed_input_settings_or_source_refused(portfolio, tmp_path, monkeypatch):
    root = tmp_path / "run"
    run_author(portfolio, root, ManagerSettings(), HoldStub(), settings=SETTINGS, max_bars=2)
    with pytest.raises(ExperimentIntegrityError, match="binding"):
        run_author(portfolio, root, ManagerSettings(max_tokens=100), HoldStub(), settings=SETTINGS)
    with pytest.raises(ExperimentIntegrityError, match="binding"):
        run_author(portfolio, root, ManagerSettings(), HoldStub(), settings=AuthorSettings(48))
    monkeypatch.setattr(author_runner, "implementation_digest", lambda: "other")
    with pytest.raises(ExperimentIntegrityError, match="binding failed"):
        inspect_author(root)


def test_journal_is_immutable_and_tamper_rejected(portfolio, tmp_path):
    root = tmp_path / "run"
    run_author(portfolio, root, ManagerSettings(), HoldStub(), settings=SETTINGS)
    with (
        sqlite3.connect(root / "author.sqlite3") as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute("UPDATE bars SET checksum='bad'")
    # Even a rewrite that recomputes the whole chain must replay identically.
    with sqlite3.connect(root / "author.sqlite3") as connection:
        connection.execute("DROP TRIGGER bars_update")
        rows = connection.execute("SELECT sequence, payload FROM bars ORDER BY sequence").fetchall()
        binding = json.loads(connection.execute("SELECT payload FROM metadata").fetchone()[0])
        previous = semantic_checksum(binding)
        for sequence, payload in rows:
            event = json.loads(payload)
            event["previous"] = previous
            if sequence == 24:
                event["turn"]["rulebooks"]["BTC-USD"]["entry"] = "cash"
            previous = semantic_checksum(event)
            connection.execute(
                "UPDATE bars SET payload=?, checksum=? WHERE sequence=?",
                (encode(event), previous, sequence),
            )
    with pytest.raises(ExperimentIntegrityError, match="differs"):
        inspect_author(root)


def test_missing_due_turn_record_never_calls(portfolio, tmp_path):
    root = tmp_path / "run"
    run_author(portfolio, root, ManagerSettings(), HoldStub(), settings=SETTINGS, max_bars=1)
    with sqlite3.connect(root / "author.sqlite3") as connection:
        connection.execute("DROP TRIGGER bars_update")
        binding = json.loads(connection.execute("SELECT payload FROM metadata").fetchone()[0])
        event = json.loads(connection.execute("SELECT payload FROM bars").fetchone()[0])
        event["turn"] = None
        connection.execute(
            "UPDATE bars SET payload=?, checksum=?",
            (encode(event), semantic_checksum(event)),
        )
        assert event["previous"] == semantic_checksum(binding)
    stub = HoldStub()
    with pytest.raises(ExperimentIntegrityError, match="due turn"):
        run_author(portfolio, root, ManagerSettings(), stub, settings=SETTINGS)
    assert not stub.requests


def test_smoke_is_not_inspectable_and_run_requires_transport(portfolio, tmp_path):
    run_author_smoke(portfolio, tmp_path / "smoke", ManagerSettings(), AuthorStub(), max_turns=1)
    with pytest.raises(ValueError, match="smoke is not evidence"):
        inspect_author(tmp_path / "smoke")
    with pytest.raises(ValueError, match="No author journal"):
        inspect_author(tmp_path / "missing")
    from gpt_trader.features.experiment.trade_manager import TransportUnavailableError

    with pytest.raises(TransportUnavailableError):
        run_author(portfolio, tmp_path / "run", ManagerSettings(), settings=SETTINGS)
    with pytest.raises(ValueError, match="positive"):
        run_author(portfolio, tmp_path / "x", ManagerSettings(), HoldStub(), max_bars=0)


def test_protocol_author_block_is_exact():
    raw = {
        "starting_rulebook": STARTING_RULEBOOK.to_dict(),
        "cadence_bars": 168,
        "history_days": 30,
        "model": "claude-fable-5-1",
        "effort": "high",
        "max_tokens": 16000,
    }
    settings, manager, rules = protocol_author(raw, Decimal("0.20"))
    assert settings == AuthorSettings(168, 30) and manager.max_tokens == 16000
    assert rules == STARTING_RULEBOOK.to_dict()
    with pytest.raises(ValueError, match="exactly"):
        protocol_author({**raw, "extra": 1}, Decimal("0.20"))
    with pytest.raises(ValueError, match="version 1"):
        protocol_author(
            {**raw, "starting_rulebook": {**STARTING_RULEBOOK.to_dict(), "version": 2}},
            Decimal("0.20"),
        )
    with pytest.raises(ValueError, match="exposure_fraction"):
        protocol_author(raw, Decimal("0.10"))
