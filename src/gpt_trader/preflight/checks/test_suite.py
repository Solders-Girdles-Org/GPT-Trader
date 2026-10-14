from __future__ import annotations

import re
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gpt_trader.preflight.core import PreflightCheck


def check_test_suite(checker: PreflightCheck) -> bool:
    """Run a targeted subset of the test suite."""
    checker.section_header("7. TEST SUITE VALIDATION")

    print("Running core test suite...")

    command = None
    if shutil.which("uv"):
        command = [
            "uv",
            "run",
            "pytest",
        ]
    elif shutil.which("pytest"):
        command = ["pytest"]
    else:
        checker.log_warning("No test runner found (uv/pytest); skipping test suite")
        return True

    command.extend(
        [
            "tests/unit/gpt_trader/app",
            "tests/unit/gpt_trader/features/brokerages/coinbase/test_coinbase_auth.py",
            "tests/unit/gpt_trader/features/brokerages/coinbase/test_coinbase_models.py",
            "-q",
            "--tb=no",
        ]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )

        output = result.stdout + result.stderr
        if result.returncode == 0:
            passed = re.search(r"(\d+) passed", output)
            if passed:
                checker.log_success(f"{passed.group(1)} core tests passed")
            else:
                checker.log_success("Core test suite passed")
            return True

        checker.log_error(
            f"Test suite failed (exit code {result.returncode}): {_summarize_counts(output)}"
        )
        if checker.verbose:
            print(output)
        return False

    except subprocess.TimeoutExpired:
        checker.log_error("Test suite timed out")
        return False
    except Exception as exc:
        checker.log_error(f"Failed to run tests: {exc}")
        return False


def _summarize_counts(output: str) -> str:
    """Build a "N passed, N failed, N errors" summary from pytest output."""
    counts = [
        match.group(0)
        for pattern in (r"\d+ passed", r"\d+ failed", r"\d+ errors?\b")
        if (match := re.search(pattern, output))
    ]
    return ", ".join(counts) or "no test counts reported"
