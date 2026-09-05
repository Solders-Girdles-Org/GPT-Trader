"""Actual SQLite/subprocess boundaries and adversarial continuation evidence."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest

from gpt_trader.core.fill_accounting import semantic_checksum
from gpt_trader.features.experiment import inputs
from gpt_trader.features.experiment.engine import run_experiment
from gpt_trader.features.experiment.inputs import parse_input
from gpt_trader.features.experiment.ledger import ExperimentIntegrityError, inspect_run


def stored_rows(root):
    with sqlite3.connect(root / "experiment.sqlite3") as connection:
        return connection.execute(
            "SELECT sequence,payload,checksum FROM events ORDER BY sequence"
        ).fetchall()


def test_resume_equals_uninterrupted_and_completion_is_idempotent(experiment_input, tmp_path):
    expected = run_experiment(tmp_path / "whole", experiment_input)
    partial = run_experiment(tmp_path / "resume", experiment_input, max_bars=55)
    assert partial["processed_bars"] == 55
    assert partial["account"]["fill_count"] == 0
    assert partial["pending_decision"] is not None
    actual = run_experiment(tmp_path / "resume")
    assert actual == expected
    assert stored_rows(tmp_path / "resume") == stored_rows(tmp_path / "whole")
    assert run_experiment(tmp_path / "resume") == actual
    assert len(stored_rows(tmp_path / "resume")) == 60


@pytest.mark.parametrize("changed", ["settings", "dataset"])
def test_changed_bound_input_cannot_resume(experiment_payload, tmp_path, changed):
    source = parse_input(experiment_payload)
    root = tmp_path / "bound"
    run_experiment(root, source, max_bars=1)
    original = stored_rows(root)
    replacement = deepcopy(experiment_payload)
    if changed == "settings":
        replacement["settings"] = {"fee_bps": "20"}
    else:
        replacement["candles"][0]["volume"] = "200"
    with pytest.raises(ExperimentIntegrityError, match="changed"):
        run_experiment(root, parse_input(replacement))
    assert stored_rows(root) == original


def test_foreign_database_is_not_adopted(experiment_input, tmp_path):
    root = tmp_path / "foreign"
    root.mkdir()
    database = root / "experiment.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE foreign_evidence (value TEXT)")
        connection.execute("INSERT INTO foreign_evidence VALUES ('preserve')")
    before = database.read_bytes()
    with pytest.raises((sqlite3.DatabaseError, ExperimentIntegrityError)):
        run_experiment(root, experiment_input)
    assert database.read_bytes() == before


def test_changed_implementation_refuses_historical_resume(experiment_input, tmp_path, monkeypatch):
    root = tmp_path / "versioned"
    run_experiment(root, experiment_input, max_bars=1)
    before = stored_rows(root)
    monkeypatch.setattr(inputs, "implementation_digest", lambda: "different-installed-source")
    with pytest.raises(ExperimentIntegrityError, match="binding failed"):
        run_experiment(root)
    assert stored_rows(root) == before


def test_drawdown_halt_survives_resume_but_existing_position_can_exit(experiment_payload, tmp_path):
    experiment_payload["settings"] = {"drawdown_fraction": "0.0001"}
    root = tmp_path / "halted"
    partial = run_experiment(root, parse_input(experiment_payload), max_bars=56)
    assert partial["halted"]
    assert partial["position"] is not None
    assert partial["account"]["fill_count"] == 1
    completed = run_experiment(root)
    assert completed["halted"]
    assert completed["position"] is None
    assert completed["account"]["fill_count"] == 2
    assert completed["pending_decision"] is None


def test_rehashed_control_state_corruption_is_refused(experiment_input, tmp_path):
    root = tmp_path / "corrupt"
    run_experiment(root, experiment_input, max_bars=1)
    with sqlite3.connect(root / "experiment.sqlite3") as connection:
        connection.execute("DROP TRIGGER events_update")
        event = json.loads(connection.execute("SELECT payload FROM events").fetchone()[0])
        event["state"]["halted"] = True
        connection.execute(
            "UPDATE events SET payload=?,checksum=?",
            (json.dumps(event), semantic_checksum(event)),
        )
    original = stored_rows(root)
    with pytest.raises(ExperimentIntegrityError, match="deterministic replay"):
        inspect_run(root)
    with pytest.raises(ExperimentIntegrityError, match="deterministic replay"):
        run_experiment(root)
    assert stored_rows(root) == original


def test_subprocess_exit_after_fill_calculation_commits_nothing(experiment_input, tmp_path):
    root = tmp_path / "crash"
    run_experiment(root, experiment_input, max_bars=55)
    before = stored_rows(root)
    script = """
import os, sys
from pathlib import Path
from gpt_trader.features.experiment import engine
original = engine._fill
def interrupted(*args, **kwargs):
    result = original(*args, **kwargs)
    if args[1] == 55:
        os._exit(87)
    return result
engine._fill = interrupted
engine.run_experiment(Path(sys.argv[1]), max_bars=1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 87, result.stderr
    assert stored_rows(root) == before
    resumed = run_experiment(root)
    uninterrupted = run_experiment(tmp_path / "reference", experiment_input)
    assert resumed == uninterrupted
    assert stored_rows(root) == stored_rows(tmp_path / "reference")


def test_concurrent_resumers_commit_each_observation_once(experiment_input, tmp_path):
    root = tmp_path / "concurrent"
    run_experiment(root, experiment_input, max_bars=55)
    barrier = Barrier(2)

    def resume():
        barrier.wait(timeout=10)
        return run_experiment(root)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(resume) for _ in range(2)]
        reports = [future.result(timeout=30) for future in futures]
    assert reports[0] == reports[1]
    assert reports[0]["account"]["fill_count"] == 2
    assert [row[0] for row in stored_rows(root)] == list(range(60))
