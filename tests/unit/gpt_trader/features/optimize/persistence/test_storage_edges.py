from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gpt_trader.config import path_registry
from gpt_trader.features.optimize.persistence.storage import OptimizationRun, OptimizationStorage
from gpt_trader.features.optimize.runner.batch_runner import TrialResult
from gpt_trader.features.optimize.types import (
    OptimizationConfig,
    ParameterDefinition,
    ParameterSpace,
    ParameterType,
)


def _make_config() -> OptimizationConfig:
    param = ParameterDefinition(
        name="risk_limit",
        parameter_type=ParameterType.FLOAT,
        low=0.1,
        high=1.0,
    )
    space = ParameterSpace(strategy_parameters=[param])
    return OptimizationConfig(
        study_name="study-1",
        parameter_space=space,
        objective_name="sharpe_constrained",
        number_of_trials=3,
    )


def _make_run() -> OptimizationRun:
    started_at = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    completed_at = datetime(2024, 1, 1, 13, 0, tzinfo=UTC)
    return OptimizationRun(
        run_id="run-1",
        study_name="study-1",
        started_at=started_at,
        completed_at=completed_at,
        config=_make_config(),
        best_parameters={"risk_limit": 0.2},
        best_objective_value=1.23,
        total_trials=3,
        feasible_trials=2,
        trials=[],
    )


def test_run_to_dict_serializes_datetimes_and_config() -> None:
    run = _make_run()
    data = run.to_dict()

    assert data["started_at"] == run.started_at.isoformat()
    assert data["completed_at"] == run.completed_at.isoformat()
    assert data["config"]["parameter_count"] == 1
    assert data["config"]["study_name"] == run.config.study_name


def test_run_from_dict_returns_config_dict_and_no_trials() -> None:
    run = _make_run()
    run.trials = [
        TrialResult(
            trial_number=1,
            parameters={"risk_limit": 0.2},
            objective_value=1.0,
            is_feasible=True,
            duration_seconds=1.0,
        )
    ]
    data = run.to_dict()
    loaded = OptimizationRun.from_dict(data)

    assert isinstance(loaded.config, dict)
    assert loaded.trials == []


def test_serialize_config_accepts_dict_and_config() -> None:
    run = _make_run()
    config_dict = {"study_name": "dict-only"}
    assert run._serialize_config(config_dict) is config_dict

    config_payload = run._serialize_config(run.config)
    assert config_payload["parameter_count"] == 1
    assert config_payload["objective_name"] == run.config.objective_name


def test_serialize_trial_with_missing_metrics() -> None:
    run = _make_run()
    trial = TrialResult(
        trial_number=1,
        parameters={"risk_limit": 0.2},
        objective_value=1.0,
        is_feasible=True,
        duration_seconds=1.0,
        risk_metrics=None,
        trade_statistics=None,
    )

    payload = run._serialize_trial(trial)
    assert payload["metrics"] is None


def test_serialize_trial_with_partial_metrics() -> None:
    run = _make_run()
    risk_metrics = MagicMock()
    risk_metrics.total_return_pct = Decimal("1.23")
    risk_metrics.sharpe_ratio = Decimal("2.5")
    risk_metrics.max_drawdown_pct = Decimal("3.3")
    trial = TrialResult(
        trial_number=2,
        parameters={"risk_limit": 0.3},
        objective_value=2.0,
        is_feasible=True,
        duration_seconds=1.0,
        risk_metrics=risk_metrics,
        trade_statistics=None,
    )

    payload = run._serialize_trial(trial)
    assert payload["metrics"]["trades"] is None
    assert payload["metrics"]["total_return"] == "1.23"
    assert payload["metrics"]["sharpe"] == "2.5"
    assert payload["metrics"]["drawdown"] == "3.3"


def test_serialize_trial_with_full_metrics() -> None:
    run = _make_run()
    risk_metrics = MagicMock()
    risk_metrics.total_return_pct = Decimal("4.56")
    risk_metrics.sharpe_ratio = Decimal("1.1")
    risk_metrics.max_drawdown_pct = Decimal("7.8")
    trade_statistics = MagicMock()
    trade_statistics.total_trades = 9
    trial = TrialResult(
        trial_number=3,
        parameters={"risk_limit": 0.4},
        objective_value=3.0,
        is_feasible=True,
        duration_seconds=1.0,
        risk_metrics=risk_metrics,
        trade_statistics=trade_statistics,
    )

    payload = run._serialize_trial(trial)
    assert payload["metrics"]["trades"] == 9
    assert payload["metrics"]["total_return"] == "4.56"
    assert payload["metrics"]["sharpe"] == "1.1"
    assert payload["metrics"]["drawdown"] == "7.8"


def test_storage_save_run_writes_results_file(tmp_path: Path) -> None:
    storage = OptimizationStorage(base_dir=tmp_path)
    run = _make_run()
    run.run_id = "run-save"

    file_path = storage.save_run(run)

    assert file_path == tmp_path / "run-save" / "results.json"
    assert file_path.exists()


def test_storage_default_uses_registry_runtime_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_dir = tmp_path / "runtime_data" / "optimize"
    legacy_dir = tmp_path / "home" / ".gpt_trader" / "optimize"
    monkeypatch.setattr(path_registry, "OPTIMIZATION_RUNS_DIR", default_dir)
    monkeypatch.setattr(path_registry, "LEGACY_OPTIMIZATION_RUNS_DIR", legacy_dir)

    storage = OptimizationStorage()

    assert storage.base_dir == default_dir
    assert storage.legacy_base_dir == legacy_dir
    assert default_dir.exists()
    assert not legacy_dir.exists()


def test_storage_load_run_falls_back_to_legacy_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_dir = tmp_path / "runtime_data" / "optimize"
    legacy_dir = tmp_path / "home" / ".gpt_trader" / "optimize"
    monkeypatch.setattr(path_registry, "OPTIMIZATION_RUNS_DIR", default_dir)
    monkeypatch.setattr(path_registry, "LEGACY_OPTIMIZATION_RUNS_DIR", legacy_dir)

    run = _make_run()
    run.run_id = "legacy-run"
    run_dir = legacy_dir / run.run_id
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(json_payload(run))

    storage = OptimizationStorage()
    loaded = storage.load_run("legacy-run")

    assert loaded is not None
    assert loaded.run_id == "legacy-run"


def test_storage_explicit_base_dir_does_not_use_legacy_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit_dir = tmp_path / "custom"
    legacy_dir = tmp_path / "home" / ".gpt_trader" / "optimize"
    monkeypatch.setattr(path_registry, "LEGACY_OPTIMIZATION_RUNS_DIR", legacy_dir)

    run = _make_run()
    run.run_id = "legacy-run"
    run_dir = legacy_dir / run.run_id
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(json_payload(run))

    storage = OptimizationStorage(base_dir=explicit_dir)

    assert storage.legacy_base_dir is None
    assert storage.load_run("legacy-run") is None


def test_storage_load_run_missing_and_invalid_json(tmp_path: Path) -> None:
    storage = OptimizationStorage(base_dir=tmp_path)

    assert storage.load_run("missing") is None

    run_dir = tmp_path / "run-bad"
    run_dir.mkdir()
    (run_dir / "results.json").write_text("{bad json")
    assert storage.load_run("run-bad") is None


def test_storage_list_runs_sorted_by_started_at(tmp_path: Path) -> None:
    storage = OptimizationStorage(base_dir=tmp_path)
    older = {
        "run_id": "run-old",
        "study_name": "study-old",
        "started_at": "2024-01-01T00:00:00+00:00",
        "best_objective_value": 1.0,
    }
    newer = {
        "run_id": "run-new",
        "study_name": "study-new",
        "started_at": "2024-01-02T00:00:00+00:00",
        "best_objective_value": 2.0,
    }
    for payload in (older, newer):
        run_dir = tmp_path / payload["run_id"]
        run_dir.mkdir()
        (run_dir / "results.json").write_text(f"""{{
  "run_id": "{payload["run_id"]}",
  "study_name": "{payload["study_name"]}",
  "started_at": "{payload["started_at"]}",
  "best_objective_value": {payload["best_objective_value"]}
}}""")

    runs = storage.list_runs()

    assert runs[0]["run_id"] == "run-new"
    assert runs[1]["run_id"] == "run-old"


def test_storage_list_runs_includes_legacy_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_dir = tmp_path / "runtime_data" / "optimize"
    legacy_dir = tmp_path / "home" / ".gpt_trader" / "optimize"
    monkeypatch.setattr(path_registry, "OPTIMIZATION_RUNS_DIR", default_dir)
    monkeypatch.setattr(path_registry, "LEGACY_OPTIMIZATION_RUNS_DIR", legacy_dir)

    current = _make_run()
    current.run_id = "run-current"
    current.started_at = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)
    legacy = _make_run()
    legacy.run_id = "run-legacy"
    legacy.started_at = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

    for base_dir, run in ((default_dir, current), (legacy_dir, legacy)):
        run_dir = base_dir / run.run_id
        run_dir.mkdir(parents=True)
        (run_dir / "results.json").write_text(json_payload(run))

    runs = OptimizationStorage().list_runs()

    assert [run["run_id"] for run in runs] == ["run-current", "run-legacy"]


def json_payload(run: OptimizationRun) -> str:
    return json.dumps(run.to_dict())
