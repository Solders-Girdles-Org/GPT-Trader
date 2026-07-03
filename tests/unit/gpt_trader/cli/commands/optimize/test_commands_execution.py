"""Unit tests for optimize CLI command execution paths."""

from __future__ import annotations

from argparse import Namespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from gpt_trader.cli.commands.optimize import apply, compare, export, run, view
from gpt_trader.cli.commands.optimize import list as list_cmd
from gpt_trader.cli.response import CliErrorCode, CliResponse


class TestRunCommand:
    """Test run command functionality."""

    def test_dry_run_returns_success(self):
        """Test dry run mode returns success."""
        args = Namespace(
            config=None,
            objective="sharpe",
            strategy="perps_baseline",
            symbols=["BTC-USD"],
            name=None,
            trials=10,
            sampler="tpe",
            seed=None,
            timeout=None,
            start_date=None,
            end_date=None,
            granularity="FIVE_MINUTE",
            format="text",
            quiet=False,
            dry_run=True,
        )

        result = run.execute(args)
        assert result == 0


class TestListCommand:
    """Test list command functionality."""

    def test_execute_with_no_runs(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        """Test list command with no runs."""
        mock_storage = MagicMock()
        mock_storage.list_runs.return_value = []

        monkeypatch.setattr(list_cmd, "OptimizationStorage", MagicMock(return_value=mock_storage))
        args = Namespace(format="text", limit=20, study=None)
        result = list_cmd.execute(args)

        assert result == 0

    def test_execute_with_runs(self, monkeypatch: pytest.MonkeyPatch):
        """Test list command with runs."""
        mock_storage = MagicMock()
        mock_storage.list_runs.return_value = [
            {
                "run_id": "opt_123",
                "study_name": "test",
                "started_at": "2024-01-01T00:00:00",
                "best_value": 1.5,
            }
        ]

        monkeypatch.setattr(list_cmd, "OptimizationStorage", MagicMock(return_value=mock_storage))
        args = Namespace(format="text", limit=20, study=None)
        result = list_cmd.execute(args)

        assert result == 0


class TestViewCommand:
    """Test view command functionality."""

    def test_execute_with_missing_run(self, monkeypatch: pytest.MonkeyPatch):
        """Test view command with missing run."""
        mock_storage = MagicMock()
        mock_storage.list_runs.return_value = []

        monkeypatch.setattr(view, "OptimizationStorage", MagicMock(return_value=mock_storage))
        args = Namespace(
            run_id="latest", format="text", trials=10, show_params=False, summary_only=False
        )
        result = view.execute(args)

        assert result == 1  # Error: no runs found


class TestCompareCommand:
    """Test compare command functionality."""

    def test_execute_requires_two_runs(self, capsys):
        """Test compare command requires at least 2 runs."""
        args = Namespace(run_ids=["opt_123"], format="text")
        result = compare.execute(args)
        assert result == 1

    def _mock_run(self, data: dict[str, Any]) -> MagicMock:
        run = MagicMock()
        run.to_dict.return_value = data
        return run

    def test_execute_json_returns_baseline_payload(self, monkeypatch: pytest.MonkeyPatch):
        """Test JSON output includes baseline metadata and matrix deltas."""
        run_a = {
            "run_id": "opt_a",
            "study_name": "alpha",
            "started_at": "2024-01-01T00:00:00",
            "completed_at": "2024-01-01T01:00:00",
            "total_trials": 100,
            "feasible_trials": 90,
            "best_objective_value": 1.8,
            "best_parameters": {"p": 1},
        }
        run_b = {
            "run_id": "opt_b",
            "study_name": "beta",
            "started_at": "2024-01-02T00:00:00",
            "completed_at": "2024-01-02T01:00:00",
            "total_trials": 95,
            "feasible_trials": 88,
            "best_objective_value": 2.0,
            "best_parameters": {"p": 2},
        }

        mock_storage = MagicMock()
        mock_storage.load_run.side_effect = [
            self._mock_run(run_a),
            self._mock_run(run_b),
        ]

        monkeypatch.setattr(compare, "OptimizationStorage", MagicMock(return_value=mock_storage))

        args = Namespace(run_ids=["opt_a", "opt_b"], output_format="json", baseline="opt_b")
        response = compare.execute(args)

        assert isinstance(response, CliResponse)
        assert response.success
        assert response.data["baseline_run"]["run_id"] == "opt_b"
        matrix = response.data["matrix"]
        assert matrix[0]["values"][0]["delta"] == pytest.approx(1.8 - 2.0)
        assert matrix[0]["values"][1]["delta"] == pytest.approx(0.0)

    def test_execute_json_invalid_baseline_returns_error(self, monkeypatch: pytest.MonkeyPatch):
        """Test baseline validation rejects run IDs outside the comparison set."""
        run_a = {
            "run_id": "opt_a",
            "study_name": "alpha",
            "started_at": "2024-01-01T00:00:00",
            "completed_at": "2024-01-01T01:00:00",
            "total_trials": 100,
            "feasible_trials": 90,
            "best_objective_value": 1.8,
        }
        run_b = {
            "run_id": "opt_b",
            "study_name": "beta",
            "started_at": "2024-01-02T00:00:00",
            "completed_at": "2024-01-02T01:00:00",
            "total_trials": 95,
            "feasible_trials": 88,
            "best_objective_value": 2.0,
        }

        mock_storage = MagicMock()
        mock_storage.load_run.side_effect = [
            self._mock_run(run_a),
            self._mock_run(run_b),
        ]

        monkeypatch.setattr(compare, "OptimizationStorage", MagicMock(return_value=mock_storage))

        args = Namespace(run_ids=["opt_a", "opt_b"], output_format="json", baseline="missing")
        response = compare.execute(args)

        assert isinstance(response, CliResponse)
        assert not response.success
        assert response.errors
        assert response.errors[0].code == CliErrorCode.INVALID_ARGUMENT.value


class TestExportCommand:
    """Test export command functionality."""

    def test_execute_with_missing_run(self, monkeypatch: pytest.MonkeyPatch):
        """Test export command with missing run."""
        mock_storage = MagicMock()
        mock_storage.list_runs.return_value = []

        monkeypatch.setattr(export, "OptimizationStorage", MagicMock(return_value=mock_storage))
        args = Namespace(
            run_id="latest",
            format="json",
            output=None,
            best_only=False,
            include_trials=False,
        )
        result = export.execute(args)

        assert result == 1  # Error: no runs found


class TestApplyCommand:
    """Test apply command functionality."""

    def test_execute_with_missing_run(self, monkeypatch: pytest.MonkeyPatch):
        """Test apply command with missing run."""
        mock_storage = MagicMock()
        mock_storage.list_runs.return_value = []

        monkeypatch.setattr(apply, "OptimizationStorage", MagicMock(return_value=mock_storage))
        args = Namespace(
            run_id="latest",
            output=None,
            profile="optimized",
            base_config=None,
            strategy_only=False,
            dry_run=False,
        )
        result = apply.execute(args)

        assert result == 1  # Error: no runs found

    def test_build_output_config_strategy_only_filters_groups(self):
        run_data = {
            "run_id": "opt_123",
            "study_name": "study",
            "best_objective_value": 1.23,
            "best_parameters": {
                "short_ma_period": 10,
                "target_leverage": 4,
                "slippage_bps": 5,
            },
        }

        output = apply._build_output_config(
            run_data,
            base_config={},
            profile_name="test",
            strategy_only=True,
        )

        assert output["strategy"]["short_ma_period"] == 10
        assert "risk" not in output
        assert "simulation" not in output

    def test_build_output_config_includes_all_groups(self):
        run_data = {
            "run_id": "opt_321",
            "study_name": "study",
            "best_objective_value": 2.46,
            "best_parameters": {
                "short_ma_period": 8,
                "target_leverage": 5,
                "slippage_bps": 10,
            },
        }

        output = apply._build_output_config(
            run_data,
            base_config={},
            profile_name="test",
            strategy_only=False,
        )

        assert output["strategy"]["short_ma_period"] == 8
        assert output["risk"]["target_leverage"] == 5
        assert output["simulation"]["slippage_bps"] == 10

    def test_build_output_config_routes_legacy_risk_keys_to_risk_group(self):
        run_data = {
            "run_id": "opt_legacy_risk",
            "study_name": "study",
            "best_objective_value": 3.14,
            "best_parameters": {
                "short_ma_period": 7,
                "max_position_size": 10000,
                "max_drawdown_pct": 8.5,
                "reduce_only_threshold": 0.15,
                "daily_loss_limit_pct": 0.03,
                "daily_loss_limit": 200,
                "dry_run_equity_usd": 2500,
                "trailing_stop_pct": 0.02,
            },
        }

        output = apply._build_output_config(
            run_data,
            base_config={},
            profile_name="test",
            strategy_only=False,
        )

        assert output["strategy"]["short_ma_period"] == 7
        assert "max_position_size" not in output["strategy"]
        assert "max_drawdown_pct" not in output["strategy"]
        assert "reduce_only_threshold" not in output["strategy"]
        assert "daily_loss_limit_pct" not in output["strategy"]
        assert "daily_loss_limit" not in output["strategy"]
        assert "dry_run_equity_usd" not in output["strategy"]
        assert "trailing_stop_pct" not in output["strategy"]
        assert output["risk"]["max_position_size"] == 10000
        assert output["risk"]["max_drawdown_pct"] == 8.5
        assert output["risk"]["reduce_only_threshold"] == 0.15
        # daily_loss_limit_pct joined daily_loss_limit as a removed parameter
        # (#1120 stage 3): the runtime breaker derives from the RiskBudget.
        assert "daily_loss_limit_pct" not in output["risk"]
        assert "daily_loss_limit" not in output["risk"]
        assert output["risk"]["dry_run_equity_usd"] == 2500
        assert output["risk"]["trailing_stop_pct"] == 0.02
