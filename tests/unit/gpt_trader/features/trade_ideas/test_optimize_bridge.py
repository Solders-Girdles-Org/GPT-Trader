from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gpt_trader.core import Candle
from gpt_trader.features.trade_ideas import (
    BaselineProposerConfig,
    ReplayRunnerConfig,
    load_optimize_baseline_candidates,
    optimize_replay_min_history,
    replay_optimize_baseline_candidates,
)

AS_OF = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _candle(
    offset_hours: int,
    *,
    open_: str = "101",
    high: str = "102",
    low: str = "100",
    close: str = "101",
) -> Candle:
    return Candle(
        ts=AS_OF + timedelta(hours=offset_hours),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
    )


def _optimize_replay_fixture() -> list[Candle]:
    return [
        _candle(-6, open_="100", high="100", low="100", close="100"),
        _candle(-5, open_="100", high="100", low="100", close="100"),
        _candle(-4, open_="90", high="90", low="90", close="90"),
        _candle(-3, open_="100", high="100", low="100", close="100"),
        _candle(-2, open_="105", high="105", low="105", close="105"),
        _candle(-1, open_="110", high="110", low="110", close="110"),
        _candle(0, open_="110", high="112", low="109", close="111"),
        _candle(1, open_="111", high="130", low="111", close="126"),
    ]


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("objective", ["target-hit-rate", "average-return-r"])
def test_optimize_candidates_replay_as_ranked_baseline_configs(
    tmp_path: Path,
    objective: str,
) -> None:
    study_path = _write_json(
        tmp_path / "optimize-study.json",
        {
            "window_results": [
                {
                    "window_id": "fast",
                    "best_parameters": {
                        "short_ma_period": 2,
                        "long_ma_period": 4,
                        "crossover_lookback": 1,
                        "expiry_hours": 3,
                    },
                    "best_objective_value": 0.10,
                },
                {
                    "window_id": "slow",
                    "best_parameters": {
                        "short_ma_period": 3,
                        "long_ma_period": 5,
                        "crossover_lookback": 1,
                        "expiry_hours": 3,
                    },
                    "best_objective_value": 0.20,
                },
            ]
        },
    )
    candidates = load_optimize_baseline_candidates(
        study_path,
        base_config=BaselineProposerConfig(),
    )

    assert [candidate.candidate_id for candidate in candidates] == ["fast", "slow"]
    assert optimize_replay_min_history(candidates) == 6

    report = replay_optimize_baseline_candidates(
        candidates,
        study_path=study_path,
        objective=objective,
        symbol="BTC-USD",
        granularity="ONE_HOUR",
        candles=_optimize_replay_fixture(),
        replay_config=ReplayRunnerConfig(source="fixture:candles", min_history=6),
    )

    assert report.rows[0].rank == 1
    assert report.rows[0].candidate_id == "slow"
    assert report.rows[0].proposer_id == "baseline-ma-3-5"
    assert report.rows[0].report.target_hit_rate == Decimal("1")
    assert report.rows[0].report.average_return_r == Decimal("2")

    payload = report.to_dict()
    assert payload["candidate_count"] == 2
    assert payload["rankings"][0]["candidate_id"] == "slow"
    assert payload["rankings"][0]["report"]["ideas"][0]["outcome"] == "target_hit"


def test_load_optimize_candidates_accepts_best_only_parameters_export(
    tmp_path: Path,
) -> None:
    study_path = _write_json(
        tmp_path / "best-only.json",
        {
            "parameters": {
                "short_ma_period": 2,
                "long_ma_period": 4,
                "crossover_lookback": 1,
            },
            "objective_value": 0.42,
        },
    )

    candidates = load_optimize_baseline_candidates(
        study_path,
        base_config=BaselineProposerConfig(),
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_id == "candidate-1"
    assert candidates[0].config.short_window == 2
    assert candidates[0].config.long_window == 4
    assert candidates[0].optimize_objective_value == Decimal("0.42")


def test_load_optimize_candidates_falls_back_to_best_parameters_when_trials_empty(
    tmp_path: Path,
) -> None:
    study_path = _write_json(
        tmp_path / "empty-trials.json",
        {
            "trials": [],
            "best_parameters": {
                "short_ma_period": 2,
                "long_ma_period": 4,
                "crossover_lookback": 1,
            },
            "best_objective_value": 0.42,
        },
    )

    candidates = load_optimize_baseline_candidates(
        study_path,
        base_config=BaselineProposerConfig(),
    )

    assert len(candidates) == 1
    assert candidates[0].config.short_window == 2
    assert candidates[0].config.long_window == 4
    assert candidates[0].optimize_objective_value == Decimal("0.42")


def test_load_optimize_candidates_skips_invalid_walk_forward_windows(
    tmp_path: Path,
) -> None:
    study_path = _write_json(
        tmp_path / "walk-forward.json",
        {
            "window_results": [
                {
                    "window_id": "failed",
                    "is_valid": False,
                    "best_parameters": {
                        "short_ma_period": 2,
                        "long_ma_period": 4,
                    },
                },
                {
                    "window_id": "empty",
                    "is_valid": True,
                    "best_parameters": {},
                },
                {
                    "window_id": "valid",
                    "is_valid": True,
                    "best_parameters": {
                        "short_ma_period": 3,
                        "long_ma_period": 5,
                    },
                },
            ]
        },
    )

    candidates = load_optimize_baseline_candidates(
        study_path,
        base_config=BaselineProposerConfig(),
    )

    assert [candidate.candidate_id for candidate in candidates] == ["valid"]
    assert candidates[0].config.short_window == 3
    assert candidates[0].config.long_window == 5


def test_load_optimize_candidates_preserves_zero_trial_number(
    tmp_path: Path,
) -> None:
    study_path = _write_json(
        tmp_path / "trials.json",
        {
            "trials": [
                {
                    "trial_number": 0,
                    "parameters": {
                        "short_ma_period": 2,
                        "long_ma_period": 4,
                    },
                }
            ]
        },
    )

    candidates = load_optimize_baseline_candidates(
        study_path,
        base_config=BaselineProposerConfig(),
    )

    assert [candidate.candidate_id for candidate in candidates] == ["0"]
