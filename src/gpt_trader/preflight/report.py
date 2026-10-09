from __future__ import annotations

import json
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from gpt_trader.preflight.hints import DEFAULT_REMEDIATION_HINT

from .context import Colors

if TYPE_CHECKING:
    from gpt_trader.preflight.context import PreflightContext
    from gpt_trader.preflight.core import PreflightCheck


class FailureHint(TypedDict):
    message: str
    hint: str
    check: str | None


def _collect_failure_hints(context: PreflightContext) -> list[FailureHint]:
    hints: list[FailureHint] = []
    for result in context.results:
        if result["status"] != "fail":
            continue
        details = result["details"]
        hints.append(
            FailureHint(
                message=result["message"],
                hint=details.get("hint") or DEFAULT_REMEDIATION_HINT,
                check=details.get("check"),
            )
        )
    return hints


def evaluate_preflight_status(
    *,
    success_count: int,
    warning_count: int,
    error_count: int,
) -> tuple[str, str]:
    """Return status label and message for the given totals."""
    if error_count == 0:
        if warning_count <= 3:
            return (
                "READY",
                "Preflight evidence is READY for gate review; production trading still requires the documented live gates",
            )
        return "REVIEW", "System has warnings - review before proceeding"
    return "NOT READY", "System is NOT READY - critical issues must be resolved"


def _warn_only_mode_enabled() -> bool:
    return os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY") == "1"


def format_preflight_report(
    checker: PreflightCheck,
    *,
    timestamp: datetime | None = None,
    diagnostic_only: bool | None = None,
) -> dict[str, Any]:
    """Return report payload without IO side effects."""
    if timestamp is None:
        timestamp = datetime.now(UTC)
    if diagnostic_only is None:
        diagnostic_only = _warn_only_mode_enabled()

    ctx = checker.context
    status, _message = evaluate_preflight_status(
        success_count=len(ctx.successes),
        warning_count=len(ctx.warnings),
        error_count=len(ctx.errors),
    )
    total_checks = len(ctx.successes) + len(ctx.warnings) + len(ctx.errors)
    failure_hints = _collect_failure_hints(ctx)

    return {
        "timestamp": timestamp.isoformat(),
        "profile": checker.profile,
        "status": status,
        "successes": len(ctx.successes),
        "warnings": len(ctx.warnings),
        "errors": len(ctx.errors),
        "diagnostic_only": diagnostic_only,
        "details": {
            "successes": list(ctx.successes),
            "warnings": list(ctx.warnings),
            "errors": list(ctx.errors),
            "error_hints": failure_hints,
        },
        "total_checks": total_checks,
    }


def serialize_preflight_report(report_payload: Mapping[str, Any]) -> str:
    """Serialize report payload to JSON text."""
    return json.dumps(report_payload, indent=2)


def report_path_for_timestamp(timestamp: datetime, *, output_dir: Path | None = None) -> Path:
    """Resolve report path for the provided timestamp."""
    filename = f"preflight_report_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
    if output_dir is None:
        return Path(filename)
    return output_dir / filename


def _build_report_sink(
    target: ReportTarget,
    *,
    timestamp: datetime,
    report_dir: Path | None,
    report_path: Path | None,
) -> tuple[ReportArtifactSink, Path | None]:
    if target == ReportTarget.FILE:
        if report_dir is not None and report_path is not None:
            raise ValueError("report_dir and report_path are mutually exclusive")
        final_path = report_path or report_path_for_timestamp(timestamp, output_dir=report_dir)
        return FileReportArtifactSink(final_path), final_path
    if target == ReportTarget.STDOUT:
        if report_dir is not None or report_path is not None:
            raise ValueError("stdout target ignores report_dir/report_path")
        return StdoutReportArtifactSink(), None
    raise ValueError(f"Unsupported report target: {target}")


class ReportTarget(str, Enum):
    FILE = "file"
    STDOUT = "stdout"


class ReportSinkError(Exception):
    """Indicates a failure writing a preflight report artifact."""


@dataclass(frozen=True)
class ReportSinkResult:
    description: str
    path: Path | None = None


class ReportArtifactSink(ABC):
    """Write-only abstraction for report artifacts (file, stdout, in-memory)."""

    @abstractmethod
    def write(self, payload: str) -> ReportSinkResult:
        """Write the serialized report payload and return metadata."""


class FileReportArtifactSink(ReportArtifactSink):
    """Sink that writes JSON payloads to disk using atomic replaces."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, payload: str) -> ReportSinkResult:
        directory = self._path.parent
        tmp_path: Path | None = None

        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                dir=directory,
                delete=False,
                encoding="utf-8",
            ) as handle:
                tmp_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path)
            return ReportSinkResult(description=str(self._path), path=self._path)
        except OSError as exc:  # pragma: no cover - defensive guard
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise ReportSinkError("Failed to write report") from exc


class StdoutReportArtifactSink(ReportArtifactSink):
    """Sink that emits JSON payloads to stdout."""

    def write(self, payload: str) -> ReportSinkResult:
        try:
            sys.stdout.write("\n")
            sys.stdout.write(payload)
            sys.stdout.write("\n")
            sys.stdout.flush()
        except OSError as exc:
            raise ReportSinkError("Failed to emit report to stdout") from exc
        return ReportSinkResult(description="stdout")


class InMemoryReportArtifactSink(ReportArtifactSink):
    """Sink that records emitted payloads for testing."""

    def __init__(self) -> None:
        self.payloads: list[str] = []

    def write(self, payload: str) -> ReportSinkResult:
        self.payloads.append(payload)
        return ReportSinkResult(description="memory")


def generate_report(
    checker: PreflightCheck,
    *,
    report_dir: Path | None = None,
    report_path: Path | None = None,
    report_target: ReportTarget = ReportTarget.FILE,
    sink: ReportArtifactSink | None = None,
    timestamp: datetime | None = None,
) -> tuple[bool, str]:
    """Render terminal summary and persist JSON report."""
    ctx = checker.context
    failure_hints = _collect_failure_hints(ctx)
    diagnostic_only = _warn_only_mode_enabled()
    checker.section_header("PREFLIGHT REPORT")

    print(f"\n{Colors.BOLD}Summary:{Colors.RESET}")
    print(f"  {Colors.GREEN}✅ Passed: {len(ctx.successes)}{Colors.RESET}")
    print(f"  {Colors.YELLOW}⚠️  Warnings: {len(ctx.warnings)}{Colors.RESET}")
    print(f"  {Colors.RED}❌ Failed: {len(ctx.errors)}{Colors.RESET}")

    status, message = evaluate_preflight_status(
        success_count=len(ctx.successes),
        warning_count=len(ctx.warnings),
        error_count=len(ctx.errors),
    )
    color = {
        "READY": Colors.GREEN,
        "REVIEW": Colors.YELLOW,
        "NOT READY": Colors.RED,
    }[status]

    print(f"\n{Colors.BOLD}{color}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{color}STATUS: {status}{Colors.RESET}")
    print(f"{color}{message}{Colors.RESET}")
    print(f"{Colors.BOLD}{color}{'=' * 70}{Colors.RESET}")

    if diagnostic_only:
        print(f"\n{Colors.BOLD}{Colors.YELLOW}DIAGNOSTIC-ONLY MODE{Colors.RESET}")
        print(
            "Warn-only preflight output is for investigation only and does not "
            "satisfy the live readiness gate."
        )

    if failure_hints:
        print(f"\n{Colors.BOLD}Remediation hints:{Colors.RESET}")
        for idx, hint in enumerate(failure_hints, start=1):
            print(f"{idx}. {hint['hint']} — {hint['message']}")

    print(f"\n{Colors.BOLD}Recommendations:{Colors.RESET}")
    if status == "READY":
        print(f"1. Start with: uv run gpt-trader run --profile {checker.profile} --dry-run")
        print("2. Review dry-run evidence against the approved runbook and decision record")
        print(
            "3. Progress canary/prod live profiles with: uv run gpt-trader run --profile "
            f"{checker.profile} --reduce-only"
        )
        print("4. Keep --dry-run or --reduce-only until explicit approval is recorded")
        print("5. Remove safety flags only with active monitoring and rollback coverage")
    elif status == "REVIEW":
        print("1. Review all warnings above")
        print("2. Consider starting with paper trading: PERPS_PAPER=1")
        print("3. Ensure emergency procedures are documented")
        print("4. Test kill switch: RISK_KILL_SWITCH_ENABLED=1")
    else:
        print("1. Fix all critical errors listed above")
        print("2. Review config/environments/.env.production for configuration guidance")
        print("3. Run tests: uv run pytest tests/unit/gpt_trader")
        print("4. Verify credentials and API connectivity")

    report_timestamp = timestamp or datetime.now(UTC)
    report_payload = format_preflight_report(
        checker,
        timestamp=report_timestamp,
        diagnostic_only=diagnostic_only,
    )
    report_content = serialize_preflight_report(report_payload)

    sink_to_use = sink
    if sink_to_use is None:
        sink_to_use, _ = _build_report_sink(
            report_target,
            timestamp=report_timestamp,
            report_dir=report_dir,
            report_path=report_path,
        )

    try:
        sink_result = sink_to_use.write(report_content)
    except ReportSinkError as exc:
        print(f"\n{Colors.YELLOW}Could not save report: {exc}{Colors.RESET}")
    else:
        if sink_result.path is not None:
            print(f"\n{Colors.CYAN}Report saved to: {sink_result.path}{Colors.RESET}")
        else:
            print(f"\n{Colors.CYAN}Report emitted via {sink_result.description}{Colors.RESET}")

    return len(ctx.errors) == 0, status
