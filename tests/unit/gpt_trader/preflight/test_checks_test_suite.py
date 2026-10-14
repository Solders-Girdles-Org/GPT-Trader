"""Tests for the test-suite preflight check, which decides on the pytest exit code."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

import gpt_trader.preflight.checks.test_suite as test_suite_checks
from gpt_trader.preflight.checks.test_suite import check_test_suite
from gpt_trader.preflight.core import PreflightCheck


@pytest.fixture
def run_stub(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.setattr(test_suite_checks.shutil, "which", lambda name: f"/usr/bin/{name}")
    stub = MagicMock()
    monkeypatch.setattr(test_suite_checks.subprocess, "run", stub)
    return stub


def _completed(returncode: int, stdout: str, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["pytest"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestCheckTestSuitePasses:
    def test_exit_zero_passes(self, run_stub: MagicMock) -> None:
        run_stub.return_value = _completed(0, "12 passed in 1.2s")
        checker = PreflightCheck()

        assert check_test_suite(checker) is True
        assert checker.successes == ["12 core tests passed"]
        assert checker.errors == []

    def test_exit_zero_with_skips_reports_passed_count(self, run_stub: MagicMock) -> None:
        run_stub.return_value = _completed(0, "===== 157 passed, 2 skipped in 5.23s =====")
        checker = PreflightCheck()

        assert check_test_suite(checker) is True
        assert checker.successes == ["157 core tests passed"]

    def test_exit_zero_without_count_still_passes(self, run_stub: MagicMock) -> None:
        run_stub.return_value = _completed(0, "")
        checker = PreflightCheck()

        assert check_test_suite(checker) is True
        assert checker.successes == ["Core test suite passed"]

    def test_prints_section_header(
        self, run_stub: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_stub.return_value = _completed(0, "10 passed")

        check_test_suite(PreflightCheck())

        assert "TEST SUITE" in capsys.readouterr().out


class TestCheckTestSuiteFails:
    def test_failed_tests_fail(self, run_stub: MagicMock) -> None:
        run_stub.return_value = _completed(1, "10 passed, 2 failed in 1.2s")
        checker = PreflightCheck()

        assert check_test_suite(checker) is False
        assert checker.successes == []
        assert checker.errors == ["Test suite failed (exit code 1): 10 passed, 2 failed"]

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("12 passed, 2 errors in 1.2s", "12 passed, 2 errors"),
            ("ERROR tests/x.py - ImportError\n3 passed, 1 error", "3 passed, 1 error"),
        ],
    )
    def test_errors_without_failures_fail(
        self, run_stub: MagicMock, output: str, expected: str
    ) -> None:
        run_stub.return_value = _completed(1, output)
        checker = PreflightCheck()

        assert check_test_suite(checker) is False
        assert checker.successes == []
        assert checker.errors == [f"Test suite failed (exit code 1): {expected}"]

    def test_collection_interrupt_fails(self, run_stub: MagicMock) -> None:
        run_stub.return_value = _completed(2, "Interrupted: 1 error during collection\n7 passed")
        checker = PreflightCheck()

        assert check_test_suite(checker) is False
        assert checker.successes == []
        assert checker.errors == ["Test suite failed (exit code 2): 7 passed, 1 error"]

    def test_no_tests_collected_fails(self, run_stub: MagicMock) -> None:
        run_stub.return_value = _completed(5, "no tests ran in 0.01s")
        checker = PreflightCheck()

        assert check_test_suite(checker) is False
        assert checker.errors == ["Test suite failed (exit code 5): no test counts reported"]

    def test_timeout_fails(self, run_stub: MagicMock) -> None:
        run_stub.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=30)
        checker = PreflightCheck()

        assert check_test_suite(checker) is False
        assert checker.errors == ["Test suite timed out"]

    def test_runner_exception_fails(self, run_stub: MagicMock) -> None:
        run_stub.side_effect = FileNotFoundError("pytest not found")
        checker = PreflightCheck()

        assert check_test_suite(checker) is False
        assert checker.errors == ["Failed to run tests: pytest not found"]

    def test_verbose_failure_prints_output(
        self, run_stub: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_stub.return_value = _completed(1, "ERROR tests/x.py - ImportError\n3 passed, 1 error")

        check_test_suite(PreflightCheck(verbose=True))

        assert "ERROR tests/x.py - ImportError" in capsys.readouterr().out
