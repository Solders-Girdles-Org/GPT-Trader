"""Tests for preflight report generation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gpt_trader.preflight.core import PreflightCheck
from gpt_trader.preflight.hints import DEFAULT_REMEDIATION_HINT
from gpt_trader.preflight.report import (
    InMemoryReportArtifactSink,
    ReportSinkError,
    ReportSinkResult,
    ReportTarget,
    StdoutReportArtifactSink,
    evaluate_preflight_status,
    format_preflight_report,
    generate_report,
    report_path_for_timestamp,
)


@pytest.fixture
def report_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _read_report(tmp_path: Path) -> dict[str, Any]:
    paths = sorted(tmp_path.glob("preflight_report_*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


class TestGenerateReport:
    """Test report generation."""

    def test_returns_ready_status_with_no_errors_few_warnings(
        self, report_cwd: Path, capsys: pytest.CaptureFixture
    ) -> None:
        checker = PreflightCheck()
        checker.context.successes.extend(["Success 1", "Success 2", "Success 3"])
        checker.context.warnings.extend(["Warning 1", "Warning 2"])  # <= 3 warnings

        success, status = generate_report(checker)

        assert success is True
        assert status == "READY"

        captured = capsys.readouterr()
        assert "READY" in captured.out
        assert "Preflight evidence is READY for gate review" in captured.out
        assert "production trading still requires the documented live gates" in captured.out
        assert "System is READY for production trading" not in captured.out

    def test_returns_review_status_with_many_warnings(
        self, report_cwd: Path, capsys: pytest.CaptureFixture
    ) -> None:
        checker = PreflightCheck()
        checker.context.successes.append("Success 1")
        checker.context.warnings.extend(["Warning 1", "Warning 2", "Warning 3", "Warning 4"])

        success, status = generate_report(checker)

        assert success is True  # Still "success" (no errors)
        assert status == "REVIEW"

        captured = capsys.readouterr()
        assert "REVIEW" in captured.out
        assert "review before proceeding" in captured.out

    def test_returns_not_ready_status_with_errors(
        self, report_cwd: Path, capsys: pytest.CaptureFixture
    ) -> None:
        checker = PreflightCheck()
        checker.context.successes.append("Success 1")
        checker.context.errors.append("Critical error")

        success, status = generate_report(checker)

        assert success is False
        assert status == "NOT READY"

        captured = capsys.readouterr()
        assert "NOT READY" in captured.out
        assert "critical issues must be resolved" in captured.out

    def test_prints_summary_counts(self, report_cwd: Path, capsys: pytest.CaptureFixture) -> None:
        checker = PreflightCheck()
        checker.context.successes.extend(["S1", "S2", "S3"])
        checker.context.warnings.extend(["W1", "W2"])
        checker.context.errors.append("E1")

        generate_report(checker)

        captured = capsys.readouterr()
        assert "Passed: 3" in captured.out
        assert "Warnings: 2" in captured.out
        assert "Failed: 1" in captured.out

    def test_prints_recommendations_for_ready(
        self, report_cwd: Path, capsys: pytest.CaptureFixture
    ) -> None:
        checker = PreflightCheck(profile="canary")
        checker.context.successes.append("Success")

        generate_report(checker)

        captured = capsys.readouterr()
        assert "--dry-run" in captured.out
        assert "--reduce-only" in captured.out
        assert "explicit approval" in captured.out
        assert "active monitoring" in captured.out
        assert "Begin live with: uv run gpt-trader run --profile canary" not in captured.out

    def test_warn_only_output_is_marked_diagnostic_only(
        self,
        report_cwd: Path,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GPT_TRADER_PREFLIGHT_WARN_ONLY", "1")
        checker = PreflightCheck(profile="prod")
        checker.context.successes.append("Success")

        generate_report(checker)

        captured = capsys.readouterr()
        report_data = _read_report(report_cwd)
        assert "DIAGNOSTIC-ONLY MODE" in captured.out
        assert "does not satisfy the live readiness gate" in captured.out
        assert report_data["diagnostic_only"] is True

    def test_prints_recommendations_for_not_ready(
        self, report_cwd: Path, capsys: pytest.CaptureFixture
    ) -> None:
        checker = PreflightCheck()
        checker.context.errors.append("Error")

        generate_report(checker)

        captured = capsys.readouterr()
        assert "Fix all critical errors" in captured.out
        assert "Run tests:" in captured.out

    def test_prints_remediation_hints_for_failed_checks(
        self, report_cwd: Path, capsys: pytest.CaptureFixture
    ) -> None:
        checker = PreflightCheck()
        checker.context.set_current_check("check_python_version")
        checker.log_error("Python 3.11 is not supported")
        checker.context.set_current_check(None)

        generate_report(checker)

        captured = capsys.readouterr()
        assert "Remediation hints:" in captured.out
        assert "Install Python 3.12" in captured.out

    def test_does_not_print_remediation_hints_when_ready(
        self, report_cwd: Path, capsys: pytest.CaptureFixture
    ) -> None:
        checker = PreflightCheck()
        checker.context.successes.append("Success")

        generate_report(checker)

        captured = capsys.readouterr()
        assert "Remediation hints:" not in captured.out

    def test_saves_json_report(self, report_cwd: Path) -> None:
        checker = PreflightCheck(profile="prod")
        checker.context.successes.extend(["S1", "S2"])
        checker.context.warnings.append("W1")

        generate_report(checker)

        report_data = _read_report(report_cwd)
        assert report_data["profile"] == "prod"
        assert report_data["status"] == "READY"
        assert report_data["diagnostic_only"] is False
        assert report_data["successes"] == 2
        assert report_data["warnings"] == 1
        assert report_data["errors"] == 0
        assert report_data["details"]["successes"] == ["S1", "S2"]
        assert report_data["details"]["warnings"] == ["W1"]
        assert "timestamp" in report_data

    def test_handles_file_save_error(
        self, report_cwd: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        checker = PreflightCheck()
        checker.context.successes.append("Success")

        def deny_write(*_args: object, **_kwargs: object) -> Any:
            raise ReportSinkError("Cannot write")

        monkeypatch.setattr(
            "gpt_trader.preflight.report.FileReportArtifactSink.write",
            deny_write,
        )

        success, status = generate_report(checker)

        assert success is True
        assert status == "READY"

        captured = capsys.readouterr()
        assert "Could not save report" in captured.out
        assert not list(report_cwd.glob("preflight_report_*.json"))

    def test_stdout_target_emits_report(
        self, report_cwd: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checker = PreflightCheck()
        checker.context.successes.append("Success")

        recorded: list[str] = []

        def record_stdout_write(_self: StdoutReportArtifactSink, payload: str) -> ReportSinkResult:
            recorded.append(payload)
            return ReportSinkResult(description="stdout")

        monkeypatch.setattr(
            "gpt_trader.preflight.report.StdoutReportArtifactSink.write",
            record_stdout_write,
        )

        success, status = generate_report(checker, report_target=ReportTarget.STDOUT)

        assert success is True
        assert status == "READY"
        assert recorded
        assert json.loads(recorded[0])["profile"] == "canary"
        assert not list(report_cwd.glob("preflight_report_*.json"))

    def test_in_memory_sink_records_payload(self, report_cwd: Path) -> None:
        checker = PreflightCheck(profile="prod")
        checker.context.successes.append("Success")
        sink = InMemoryReportArtifactSink()

        success, status = generate_report(checker, sink=sink)

        assert success is True
        assert status == "READY"
        assert len(sink.payloads) == 1
        payload = json.loads(sink.payloads[0])
        assert payload["profile"] == "prod"
        assert payload["status"] == "READY"
        assert payload["diagnostic_only"] is False

    def test_prints_section_header(self, report_cwd: Path, capsys: pytest.CaptureFixture) -> None:
        checker = PreflightCheck()
        checker.context.successes.append("Success")

        generate_report(checker)

        captured = capsys.readouterr()
        assert "PREFLIGHT REPORT" in captured.out

    def test_respects_explicit_report_path(
        self,
        report_cwd: Path,
    ) -> None:
        checker = PreflightCheck(profile="prod")
        checker.context.successes.append("Success")
        explicit_path = report_cwd / "reports" / "custom_preflight_report.json"

        success, status = generate_report(checker, report_path=explicit_path)

        assert success is True
        assert status == "READY"
        assert explicit_path.exists()
        report_data = json.loads(explicit_path.read_text(encoding="utf-8"))
        assert report_data["profile"] == "prod"

    def test_report_dir_and_report_path_are_mutually_exclusive(
        self,
        report_cwd: Path,
    ) -> None:
        checker = PreflightCheck()
        checker.context.successes.append("Success")
        explicit_path = report_cwd / "explicit.json"

        with pytest.raises(ValueError, match="mutually exclusive"):
            generate_report(checker, report_dir=report_cwd / "reports", report_path=explicit_path)


class TestReportCalculations:
    """Test report calculations."""

    def test_total_checks_calculated_correctly(self, report_cwd: Path) -> None:
        checker = PreflightCheck()
        checker.context.successes.extend(["S1", "S2", "S3"])
        checker.context.warnings.extend(["W1", "W2"])
        checker.context.errors.append("E1")

        generate_report(checker)

        report_data = _read_report(report_cwd)
        assert report_data["total_checks"] == 6  # 3 + 2 + 1


class TestReportFormatting:
    """Test pure report formatting utilities."""

    def test_format_preflight_report_is_pure(self, report_cwd: Path) -> None:
        checker = PreflightCheck(profile="prod")
        checker.context.successes.extend(["S1", "S2"])
        checker.context.warnings.append("W1")
        timestamp = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        report_data = format_preflight_report(checker, timestamp=timestamp)

        assert report_data["timestamp"] == timestamp.isoformat()
        assert report_data["profile"] == "prod"
        assert report_data["status"] == "READY"
        assert report_data["diagnostic_only"] is False
        assert report_data["details"]["successes"] == ["S1", "S2"]
        assert report_data["details"]["warnings"] == ["W1"]
        assert not list(report_cwd.glob("preflight_report_*.json"))

    @pytest.mark.parametrize(
        ("warning_count", "error_count", "expected_status"),
        [
            (0, 0, "READY"),
            (3, 0, "READY"),
            (4, 0, "REVIEW"),
            (0, 1, "NOT READY"),
            (10, 2, "NOT READY"),
        ],
    )
    def test_evaluate_preflight_status_boundaries(
        self,
        warning_count: int,
        error_count: int,
        expected_status: str,
    ) -> None:
        status, _message = evaluate_preflight_status(
            success_count=5,
            warning_count=warning_count,
            error_count=error_count,
        )

        assert status == expected_status

    def test_report_path_for_timestamp_honors_output_dir(self) -> None:
        timestamp = datetime(2026, 2, 11, 16, 2, 58, tzinfo=UTC)
        report_path = report_path_for_timestamp(
            timestamp,
            output_dir=Path("var/reports/preflight"),
        )

        assert report_path == Path("var/reports/preflight/preflight_report_20260211_160258.json")


class TestReportHints:
    """Tests for structured remediation hints in preflight reports."""

    def test_error_hints_reflect_known_checks(self, report_cwd: Path) -> None:
        checker = PreflightCheck()
        checker.context.set_current_check("check_python_version")
        checker.log_error("Python 3.11 is not supported")
        checker.context.set_current_check(None)
        timestamp = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        report_data = format_preflight_report(checker, timestamp=timestamp)
        hints = report_data["details"]["error_hints"]

        assert len(hints) == 1
        assert hints[0]["message"] == "Python 3.11 is not supported"
        assert "Python 3.12" in hints[0]["hint"]
        assert hints[0]["check"] == "check_python_version"

    def test_error_hints_use_generic_fallback(self, report_cwd: Path) -> None:
        checker = PreflightCheck()
        checker.context.set_current_check("unknown_check")
        checker.log_error("Something unexpected occurred")
        checker.context.set_current_check(None)
        timestamp = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        report_data = format_preflight_report(checker, timestamp=timestamp)
        hints = report_data["details"]["error_hints"]

        assert hints
        assert hints[0]["hint"] == DEFAULT_REMEDIATION_HINT
