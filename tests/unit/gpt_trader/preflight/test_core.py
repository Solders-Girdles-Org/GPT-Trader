"""Tests for PreflightCheck initialization, delegations, and helpers."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import gpt_trader.preflight.core as preflight_core_module
from gpt_trader.preflight.core import PreflightCheck
from gpt_trader.preflight.report import ReportTarget


class TestPreflightCheckInit:
    """Test PreflightCheck initialization."""

    def test_default_initialization(self) -> None:
        """PreflightCheck should initialize with defaults."""
        check = PreflightCheck()

        assert check.verbose is False
        assert check.profile == "canary"
        assert check.context is not None
        assert check.context.profile == "canary"

    def test_custom_initialization(self) -> None:
        """PreflightCheck should accept custom values."""
        check = PreflightCheck(verbose=True, profile="prod")

        assert check.verbose is True
        assert check.profile == "prod"
        assert check.context.verbose is True
        assert check.context.profile == "prod"


class TestPreflightCheckCompatibilityMirrors:
    """Test backwards-compatibility properties that mirror context."""

    def test_errors_mirrors_context(self) -> None:
        """errors property should mirror context.errors."""
        check = PreflightCheck()
        check.context.errors.append("test error")

        assert check.errors == ["test error"]
        assert check.errors is check.context.errors

    def test_warnings_mirrors_context(self) -> None:
        """warnings property should mirror context.warnings."""
        check = PreflightCheck()
        check.context.warnings.append("test warning")

        assert check.warnings == ["test warning"]

    def test_successes_mirrors_context(self) -> None:
        """successes property should mirror context.successes."""
        check = PreflightCheck()
        check.context.successes.append("test success")

        assert check.successes == ["test success"]

    def test_results_mirrors_context(self) -> None:
        """results property should mirror context.results."""
        check = PreflightCheck()
        check.context.results.append(
            {"status": "pass", "message": "test", "details": {"source": "unit"}}
        )

        assert check.results == [
            {"status": "pass", "message": "test", "details": {"source": "unit"}}
        ]

    def test_config_mirrors_context(self) -> None:
        """config property should mirror context.config."""
        check = PreflightCheck()
        check.context.config["key"] = "value"

        assert check.config == {"key": "value"}


class TestPreflightCheckDelegations:
    """Test that check methods delegate to module functions."""

    @pytest.mark.parametrize(
        ("method_name", "function_name", "return_value"),
        [
            ("check_python_version", "check_python_version", True),
            ("check_dependencies", "check_dependencies", True),
            ("check_environment_variables", "check_environment_variables", False),
            ("check_api_connectivity", "check_api_connectivity", True),
            ("check_key_permissions", "check_key_permissions", True),
            ("check_risk_configuration", "check_risk_configuration", True),
            ("check_pretrade_diagnostics", "check_pretrade_diagnostics", True),
            ("check_readiness_report", "check_readiness_report", True),
            ("check_trade_ideas_readiness", "check_trade_ideas_readiness", True),
            ("check_event_store_redaction", "check_event_store_redaction", True),
            ("check_test_suite", "check_test_suite", True),
            ("check_profile_configuration", "check_profile_configuration", True),
            ("check_system_time", "check_system_time", True),
            ("check_disk_space", "check_disk_space", True),
            ("simulate_dry_run", "simulate_dry_run", True),
            ("generate_report", "generate_report", (True, "READY")),
        ],
    )
    def test_delegations_call_module_function(
        self,
        method_name: str,
        function_name: str,
        return_value: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        check = PreflightCheck()

        mock = Mock(return_value=return_value)
        monkeypatch.setattr(preflight_core_module, function_name, mock)
        result = getattr(check, method_name)()

        if function_name == "generate_report":
            mock.assert_called_once_with(
                check,
                report_dir=None,
                report_path=None,
                report_target=ReportTarget.FILE,
                timestamp=None,
            )
        else:
            mock.assert_called_once_with(check)
        assert result == return_value


class TestPreflightCheckLoggingHelpers:
    """Test logging helper delegations."""

    def test_log_success_delegates_to_context(self, capsys: pytest.CaptureFixture) -> None:
        """log_success should delegate to context."""
        check = PreflightCheck()
        check.log_success("Success message")

        assert "Success message" in check.successes
        captured = capsys.readouterr()
        assert "Success message" in captured.out

    def test_log_warning_records_normalized_result(self) -> None:
        """log_warning should record a normalized payload."""
        check = PreflightCheck()
        check.log_warning("Warn message", details={"hint": "review"})

        assert check.results[-1] == {
            "status": "warn",
            "message": "Warn message",
            "details": {"hint": "review"},
        }

    def test_log_warning_delegates_to_context(self, capsys: pytest.CaptureFixture) -> None:
        """log_warning should delegate to context."""
        check = PreflightCheck()
        check.log_warning("Warning message")

        assert "Warning message" in check.warnings

    def test_log_error_delegates_to_context(self, capsys: pytest.CaptureFixture) -> None:
        """log_error should delegate to context."""
        check = PreflightCheck()
        check.log_error("Error message")

        assert "Error message" in check.errors

    def test_log_info_delegates_to_context(self, capsys: pytest.CaptureFixture) -> None:
        """log_info should delegate to context."""
        check = PreflightCheck(verbose=True)
        check.log_info("Info message")

        captured = capsys.readouterr()
        assert "Info message" in captured.out

    def test_section_header_delegates_to_context(self, capsys: pytest.CaptureFixture) -> None:
        """section_header should delegate to context."""
        check = PreflightCheck()
        check.section_header("Test Header")

        captured = capsys.readouterr()
        assert "Test Header" in captured.out


class TestPreflightCheckEnvironmentHelpers:
    """Test environment helper delegations.

    Note: PreflightContext uses slots=True, so we can't mock methods directly.
    Instead, we test that the facade returns the same results as the context.
    """

    def test_resolve_cdp_credentials_delegates(self) -> None:
        """_resolve_cdp_credentials should return same as context."""
        check = PreflightCheck()

        # Should return the same result as context method
        result = check._resolve_cdp_credentials()
        expected = check.context.resolve_cdp_credentials()

        assert result == expected

    def test_has_real_cdp_credentials_delegates(self) -> None:
        """_has_real_cdp_credentials should return same as context."""
        check = PreflightCheck()

        result = check._has_real_cdp_credentials()
        expected = check.context.has_real_cdp_credentials()

        assert result == expected

    def test_should_skip_remote_checks_delegates(self) -> None:
        """_should_skip_remote_checks should return same as context."""
        check = PreflightCheck()

        result = check._should_skip_remote_checks()
        expected = check.context.should_skip_remote_checks()

        assert result == expected

    def test_expected_env_defaults_delegates(self) -> None:
        """_expected_env_defaults should return same as context."""
        check = PreflightCheck()

        result = check._expected_env_defaults()
        expected = check.context.expected_env_defaults()

        assert result == expected
