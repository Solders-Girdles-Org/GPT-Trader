"""Tests for preflight CLI."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gpt_trader.preflight.check_graph import (
    CORE_PREFLIGHT_CHECKS,
    assemble_preflight_check_graph,
)
from gpt_trader.preflight.cli import _header, main
from gpt_trader.preflight.cli_args import (
    PreflightCliArgs,
    _normalize_preflight_args,
    parse_preflight_args,
)
from gpt_trader.preflight.report import ReportTarget


@dataclass(frozen=True)
class CLIMocks:
    checker: MagicMock
    preflight_class: MagicMock
    header: MagicMock


@pytest.fixture
def cli_mocks(monkeypatch: pytest.MonkeyPatch) -> CLIMocks:
    checker = MagicMock()
    checker.generate_report.return_value = (True, "READY")
    preflight_class = MagicMock(return_value=checker)
    header = MagicMock()

    monkeypatch.setattr("gpt_trader.preflight.cli.PreflightCheck", preflight_class)
    monkeypatch.setattr("gpt_trader.preflight.cli._header", header)

    return CLIMocks(checker=checker, preflight_class=preflight_class, header=header)


class TestHeader:
    """Test CLI header function."""

    def test_prints_header_with_profile(self, capsys: pytest.CaptureFixture) -> None:
        """Should print header with profile name."""
        _header("canary")

        captured = capsys.readouterr()
        assert "GPT-TRADER PRODUCTION PREFLIGHT CHECK" in captured.out
        assert "canary" in captured.out

    def test_prints_timestamp(self, capsys: pytest.CaptureFixture) -> None:
        """Should print timestamp in header."""
        _header("prod")

        captured = capsys.readouterr()
        assert "UTC" in captured.out

    def test_prints_separators(self, capsys: pytest.CaptureFixture) -> None:
        """Should print separator lines."""
        _header("dev")

        captured = capsys.readouterr()
        assert "=" * 70 in captured.out


class TestParsePreflightArgs:
    """Test CLI argument parsing."""

    def test_defaults(self) -> None:
        parsed = parse_preflight_args([])

        assert parsed == PreflightCliArgs(
            verbose=False,
            profile=None,
            warn_only=False,
            diagnostics_bundle=False,
            report_dir=None,
            report_path=None,
            report_target=ReportTarget.FILE,
        )

    def test_missing_profile_uses_canary_fallback(self) -> None:
        parser = argparse.ArgumentParser()
        args = argparse.Namespace(
            verbose=False,
            profile=None,
            warn_only=False,
            diagnostics_bundle=False,
            report_dir=None,
            report_path=None,
            report_target=ReportTarget.FILE,
        )

        normalized_args = _normalize_preflight_args(parser, args)

        assert normalized_args.profile is None

    def test_warn_only_flag(self) -> None:
        parsed = parse_preflight_args(["--warn-only"])

        assert parsed.warn_only is True

    def test_report_target_flag(self) -> None:
        parsed = parse_preflight_args(["--report-target", "stdout"])

        assert parsed.report_target == ReportTarget.STDOUT

    def test_report_target_stdout_rejects_report_dir(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            parse_preflight_args(
                ["--report-target", "stdout", "--report-dir", str(tmp_path / "reports")]
            )

    def test_report_target_stdout_rejects_report_path(self, tmp_path: Path) -> None:
        target_file = tmp_path / "explicit.json"
        with pytest.raises(SystemExit):
            parse_preflight_args(["--report-target", "stdout", "--report-path", str(target_file)])

    def test_diagnostics_bundle_flag(self) -> None:
        parsed = parse_preflight_args(["--diagnostics-bundle"])

        assert parsed.diagnostics_bundle is True

    def test_report_dir_resolves_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        parsed = parse_preflight_args(["--report-dir", "reports"])

        assert parsed.report_dir == (tmp_path / "reports").resolve()

    def test_invalid_profile_exits_non_zero(self) -> None:
        with pytest.raises(SystemExit) as exc:
            parse_preflight_args(["--profile", "invalid"])

        assert exc.value.code != 0

    def test_report_path_rejects_directory(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            parse_preflight_args(["--report-path", str(tmp_path)])

        assert exc.value.code != 0

    def test_warn_only_does_not_suppress_parse_errors(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            parse_preflight_args(["--warn-only", "--report-path", str(tmp_path)])

        assert exc.value.code != 0


class TestMain:
    """Test main CLI entry point."""

    @pytest.fixture(autouse=True)
    def _clear_warn_only_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GPT_TRADER_PREFLIGHT_WARN_ONLY", raising=False)

    def test_returns_zero_on_success(self, cli_mocks: CLIMocks) -> None:
        """Should return 0 when all checks pass."""
        cli_mocks.checker.generate_report.return_value = (True, "READY")

        assert main(["--profile", "dev"]) == 0

    def test_returns_one_on_failure(self, cli_mocks: CLIMocks) -> None:
        """Should return 1 when checks fail."""
        cli_mocks.checker.generate_report.return_value = (False, "NOT_READY")

        assert main(["--profile", "prod"]) == 1

    def test_passes_verbose_flag(self, cli_mocks: CLIMocks) -> None:
        """Should pass verbose flag to PreflightCheck."""
        main(["--verbose"])

        cli_mocks.preflight_class.assert_called_once_with(verbose=True, profile="canary")

    def test_passes_profile_flag(self, cli_mocks: CLIMocks) -> None:
        """Should pass profile flag to PreflightCheck."""
        main(["--profile", "prod"])

        cli_mocks.preflight_class.assert_called_once_with(verbose=False, profile="prod")

    def test_uses_canary_profile_by_default(self, cli_mocks: CLIMocks) -> None:
        """Should use canary profile when not specified."""
        main([])

        cli_mocks.preflight_class.assert_called_once_with(verbose=False, profile="canary")

    def test_env_profile_override(
        self, cli_mocks: CLIMocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GPT_TRADER_PROFILE", "spot")

        main([])

        cli_mocks.preflight_class.assert_called_once_with(verbose=False, profile="spot")

    def test_runs_all_check_functions(self, cli_mocks: CLIMocks) -> None:
        """Should call all check functions."""
        main([])

        # Verify all checks were called
        cli_mocks.checker.check_python_version.assert_called_once()
        cli_mocks.checker.check_dependencies.assert_called_once()
        cli_mocks.checker.check_environment_variables.assert_called_once()
        cli_mocks.checker.check_api_connectivity.assert_called_once()
        cli_mocks.checker.check_key_permissions.assert_called_once()
        cli_mocks.checker.check_risk_configuration.assert_called_once()
        cli_mocks.checker.check_pretrade_diagnostics.assert_called_once()
        cli_mocks.checker.check_test_suite.assert_called_once()
        cli_mocks.checker.check_profile_configuration.assert_called_once()
        cli_mocks.checker.check_system_time.assert_called_once()
        cli_mocks.checker.check_disk_space.assert_called_once()
        cli_mocks.checker.simulate_dry_run.assert_called_once()
        cli_mocks.checker.check_event_store_redaction.assert_called_once()
        cli_mocks.checker.check_trade_ideas_readiness.assert_called_once()
        cli_mocks.checker.check_readiness_report.assert_called_once()

    def test_runs_checks_in_graph_order(self, cli_mocks: CLIMocks) -> None:
        main([])

        expected_order = [
            node.name for node in assemble_preflight_check_graph(CORE_PREFLIGHT_CHECKS)
        ]
        expected_set = set(expected_order)
        actual_order = [
            call_name
            for call_name, *_rest in cli_mocks.checker.method_calls
            if call_name in expected_set
        ]

        assert actual_order == expected_order

    def test_handles_check_exception_gracefully(self, cli_mocks: CLIMocks) -> None:
        """Should handle exceptions from checks gracefully."""
        cli_mocks.checker.check_python_version.side_effect = Exception("Test exception")
        cli_mocks.checker.generate_report.return_value = (True, "READY")

        # Should not raise, should handle gracefully
        assert main([]) == 0
        cli_mocks.checker.log_error.assert_called_once()

    def test_calls_header_with_profile(self, cli_mocks: CLIMocks) -> None:
        """Should call header with correct profile."""
        cli_mocks.checker.generate_report.return_value = (True, "READY")

        main(["--profile", "prod"])

        cli_mocks.header.assert_called_once_with("prod")

    def test_short_flags_work(self, cli_mocks: CLIMocks) -> None:
        """Should accept short flag versions."""
        main(["-v", "-p", "prod"])

        cli_mocks.preflight_class.assert_called_once_with(verbose=True, profile="prod")

    def test_warn_only_sets_env_var(self, cli_mocks: CLIMocks) -> None:
        observed_warn_only_values: list[str | None] = []
        cli_mocks.checker.check_python_version.side_effect = (
            lambda: observed_warn_only_values.append(
                os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY")
            )
        )

        main(["--warn-only"])

        assert observed_warn_only_values == ["1"]
        assert os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY") is None

    def test_generate_report_called_with_default_target(self, cli_mocks: CLIMocks) -> None:
        main([])

        cli_mocks.checker.generate_report.assert_called_once_with(
            report_dir=None,
            report_path=None,
            report_target=ReportTarget.FILE,
        )

    def test_report_target_flag_passes_to_generate_report(self, cli_mocks: CLIMocks) -> None:
        main(["--report-target", "stdout"])

        cli_mocks.checker.generate_report.assert_called_once_with(
            report_dir=None,
            report_path=None,
            report_target=ReportTarget.STDOUT,
        )

    def test_diagnostics_bundle_mode_outputs_bundle(self, cli_mocks: CLIMocks, monkeypatch, capsys):
        bundle = {
            "schema_version": "test:v1",
            "bundle": {"readiness": {"status": "READY", "message": ""}},
        }
        monkeypatch.setattr(
            "gpt_trader.preflight.cli.build_diagnostics_bundle",
            lambda profile, **kwargs: bundle,
        )

        assert main(["--diagnostics-bundle"]) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == bundle
        cli_mocks.preflight_class.assert_not_called()
        cli_mocks.header.assert_not_called()

    def test_diagnostics_bundle_mode_handles_exceptions(self, monkeypatch, capsys):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("gpt_trader.preflight.cli.build_diagnostics_bundle", _boom)

        assert main(["--diagnostics-bundle"]) == 1
        captured = capsys.readouterr()
        assert "Error generating diagnostics bundle" in captured.err
