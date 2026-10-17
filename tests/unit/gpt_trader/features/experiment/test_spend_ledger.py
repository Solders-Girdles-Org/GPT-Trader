"""The spend ledger and its hard ceiling: reservations, uncommitted calls, file safety."""

from decimal import Decimal

import pytest
from tests.unit.gpt_trader.features.experiment.model_transport_test_helpers import (
    first_request,
    transport,
)

from gpt_trader.features.experiment.engine import run_experiment
from gpt_trader.features.experiment.ledger import inspect_run
from gpt_trader.features.experiment.model_transport import (
    SpendCeilingReached,
    SpendLedger,
    SpendLedgerError,
    TransportHalted,
    wire_params,
    worst_case_usd,
)


def test_spend_ceiling_stops_the_run_loudly_before_the_call(manager_input, tmp_path):
    params = wire_params(first_request(manager_input), manager_input.manager)
    # Room for one worst-case reservation, not two.
    ledger = SpendLedger(tmp_path / "spend.sqlite", worst_case_usd(params) + Decimal("0.05"))
    sender = transport(manager_input, ledger)
    root = tmp_path / "run"
    with pytest.raises(SpendCeilingReached, match="it was not sent"):
        run_experiment(root, manager_input, transport=sender)
    assert len(sender.client.messages.calls) == 1
    assert inspect_run(root)["processed_bars"] == 1
    assert ledger.spent_usd() == Decimal("0.09")


def test_calls_whose_bar_never_committed_still_count(manager_input, ledger, tmp_path):
    class CrashBeforeCommit:
        def __init__(self, inner):
            self.inner = inner
            self.model = inner.model

        def decide(self, request):
            self.inner.decide(request)
            raise RuntimeError("process died before the journal commit")

    root = tmp_path / "run"
    with pytest.raises(RuntimeError):
        run_experiment(
            root,
            manager_input,
            max_bars=1,
            transport=CrashBeforeCommit(transport(manager_input, ledger)),
        )
    assert inspect_run(root)["processed_bars"] == 0
    run_experiment(root, max_bars=1, transport=transport(manager_input, ledger))
    assert inspect_run(root)["processed_bars"] == 1
    assert [call["status"] for call in ledger.calls()] == ["replied", "replied"]
    assert ledger.spent_usd() == Decimal("0.18")


def test_a_call_that_never_settles_keeps_its_full_reservation(manager_input, ledger):
    sender = transport(manager_input, ledger, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        sender.decide(first_request(manager_input))
    (call,) = ledger.calls()
    assert call["status"] == "reserved" and call["cost_usd"] is None
    assert ledger.spent_usd() == Decimal(call["reserved_usd"])


def test_ledger_is_shared_by_path_and_bound_to_its_cap(manager_input, ledger, tmp_path):
    transport(manager_input, ledger).decide(first_request(manager_input))
    again = SpendLedger(tmp_path / "spend.sqlite", Decimal("100"))
    assert again.spent_usd() == Decimal("0.09")
    with pytest.raises(SpendLedgerError, match=r"bound to a \$100 cap"):
        SpendLedger(tmp_path / "spend.sqlite", Decimal("200"))
    for bad in (Decimal(0), Decimal(-1), Decimal("NaN")):
        with pytest.raises(SpendLedgerError):
            SpendLedger(tmp_path / "other.sqlite", bad)


def test_an_unusable_ledger_stops_the_run(manager_input, ledger):
    ledger.path.mkdir()
    with pytest.raises(TransportHalted, match="is unusable"):
        transport(manager_input, ledger).decide(first_request(manager_input))


def test_ledger_file_is_created_only_by_the_first_reservation(manager_input, ledger, tmp_path):
    assert not ledger.path.exists() and ledger.spent_usd() == 0
    tiny = SpendLedger(tmp_path / "tiny.sqlite", Decimal("0.01"))
    with pytest.raises(SpendCeilingReached, match="above the"):
        transport(manager_input, tiny).decide(first_request(manager_input))
    assert not tiny.path.exists()
    transport(manager_input, ledger).decide(first_request(manager_input))
    assert ledger.path.is_file() and len(ledger.calls()) == 1


def test_a_file_that_is_not_a_spend_ledger_is_refused_and_left_unchanged(
    experiment_input, tmp_path
):
    root = tmp_path / "baseline"
    run_experiment(root, experiment_input, max_bars=1)
    journal = next(root.iterdir())
    before = journal.read_bytes()
    with pytest.raises(SpendLedgerError, match="is not a spend ledger"):
        SpendLedger(journal, Decimal("5"))
    text = tmp_path / "notes.txt"
    text.write_text("not a database")
    with pytest.raises(SpendLedgerError, match="not a readable spend ledger.*restart the count"):
        SpendLedger(text, Decimal("5"))
    assert journal.read_bytes() == before and text.read_text() == "not a database"


def test_a_ledger_path_must_be_a_file_in_an_existing_directory(tmp_path):
    for path in (tmp_path, tmp_path / "missing" / "spend.sqlite"):
        with pytest.raises(SpendLedgerError, match="must be a file in an existing directory"):
            SpendLedger(path, Decimal("5"))


def test_a_ledger_interrupted_mid_write_is_recovered_not_refused(manager_input, ledger):
    import subprocess
    import sys

    transport(manager_input, ledger).decide(first_request(manager_input))
    killed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, signal, sqlite3, sys\n"
            "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
            "c.execute('PRAGMA cache_size = 1')\n"
            "c.execute('BEGIN IMMEDIATE')\n"
            "for _ in range(200):\n"
            '    c.execute("INSERT INTO calls (model, reserved_at, reserved_usd, status, detail) '
            "VALUES ('m', 't', '999', 'reserved', ?)\", ('x' * 4000,))\n"
            "c.execute(\"UPDATE calls SET cost_usd = '999'\")\n"
            'c.execute("INSERT INTO calls (model, reserved_at, reserved_usd, status) '
            'SELECT model, reserved_at, reserved_usd, status FROM calls")\n'
            "os.kill(os.getpid(), signal.SIGKILL)\n",
            str(ledger.path),
        ],
        check=False,
    )
    assert killed.returncode != 0
    assert ledger.path.with_name(ledger.path.name + "-journal").exists()  # a hot journal
    reopened = SpendLedger(ledger.path, Decimal("100"))
    assert reopened.spent_usd() == Decimal("0.09")
