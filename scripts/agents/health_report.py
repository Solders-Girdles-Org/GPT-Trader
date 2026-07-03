#!/usr/bin/env python3
"""Generate agent-health reports (lint/types/tests/preflight/config validation)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

load_dotenv: Callable[..., bool] | None
try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover - optional dependency for CLI convenience
    load_dotenv = None
else:
    load_dotenv = _load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_ROOT))

from gpt_trader import __version__ as APP_VERSION  # noqa: E402
from gpt_trader.app.config.bot_config import BotConfig  # noqa: E402
from gpt_trader.app.config.profile_loader import ProfileLoader, is_dev_profile  # noqa: E402
from gpt_trader.app.config.validation import validate_config  # noqa: E402
from gpt_trader.config.types import Profile  # noqa: E402
from gpt_trader.preflight.core import PreflightCheck  # noqa: E402
from scripts.agents import quality_gate  # noqa: E402

SCHEMA_VERSION = "1.1"
TOOL_NAME = "agent-health"
TOOL_SCRIPT = "scripts/agents/health_report.py"


@dataclass
class HealthCheckResult:
    name: str
    status: str
    summary: str
    duration_seconds: float
    details: dict[str, Any] = field(default_factory=dict)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_profile(value: str) -> Profile:
    try:
        return Profile(value.lower())
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unknown profile: {value}") from exc


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _run_quality_checks(
    *,
    checks: list[str],
    files: list[str] | None,
    full: bool,
) -> list[HealthCheckResult]:
    report = quality_gate.run_quality_gate(
        checks=checks,
        fast=not full,
        paths=files,
    )
    results: list[HealthCheckResult] = []
    for result in report["results"]:
        status = "passed" if result["passed"] else "failed"
        results.append(
            HealthCheckResult(
                name=result["name"],
                status=status,
                summary=result["summary"],
                duration_seconds=result["duration_seconds"],
                details={
                    "command": result.get("command", ""),
                    "findings": result.get("findings", []),
                },
            )
        )
    return results


SUMMARY_COUNT_PATTERN = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|failed|error|errors|skipped|xfailed|xpassed)"
)
SUMMARY_DURATION_PATTERN = re.compile(r"in\s+(?P<duration>[0-9.]+)s")


def _extract_pytest_summary(stdout: str, stderr: str) -> str:
    summary = "Tests completed"
    lines = [line.strip() for line in (stdout + stderr).split("\n") if line.strip()]
    for line in reversed(lines):
        if SUMMARY_COUNT_PATTERN.search(line):
            return line
    for line in lines:
        if ("passed" in line or "failed" in line or "error" in line) and "::" not in line:
            summary = line
            break
    return summary


def _parse_pytest_summary_counts(summary: str) -> dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for match in SUMMARY_COUNT_PATTERN.finditer(summary):
        label = match.group("label")
        value = _coerce_int(match.group("count"))
        if label in {"error", "errors"}:
            counts["errors"] += value
        elif label == "failed":
            counts["failed"] += value
        elif label == "passed":
            counts["passed"] += value
        elif label == "skipped":
            counts["skipped"] += value
    duration_match = SUMMARY_DURATION_PATTERN.search(summary)
    duration_seconds = _coerce_float(duration_match.group("duration")) if duration_match else 0.0
    total = sum(counts.values())
    return {**counts, "total": total, "duration_seconds": duration_seconds}


def _parse_pytest_json_report(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    counts = {
        "passed": _coerce_int(summary.get("passed")),
        "failed": _coerce_int(summary.get("failed")),
        "errors": _coerce_int(summary.get("errors") or summary.get("error")),
        "skipped": _coerce_int(summary.get("skipped")),
    }
    total = _coerce_int(summary.get("total") or summary.get("collected"))
    if total == 0:
        total = sum(counts.values())
    duration_seconds = _coerce_float(payload.get("duration") or summary.get("duration"))
    return {**counts, "total": total, "duration_seconds": duration_seconds}


def _parse_junit_xml(xml_text: str) -> dict[str, Any]:
    root = ElementTree.fromstring(xml_text)
    suites = []
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = list(root.findall(".//testsuite"))

    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    duration_seconds = 0.0
    total = 0
    for suite in suites:
        total += _coerce_int(suite.attrib.get("tests"))
        counts["failed"] += _coerce_int(suite.attrib.get("failures"))
        counts["errors"] += _coerce_int(suite.attrib.get("errors"))
        counts["skipped"] += _coerce_int(suite.attrib.get("skipped"))
        duration_seconds += _coerce_float(suite.attrib.get("time"))

    counts["passed"] = max(total - counts["failed"] - counts["errors"] - counts["skipped"], 0)
    return {**counts, "total": total, "duration_seconds": duration_seconds}


def _run_pytest(args: list[str]) -> HealthCheckResult:
    cmd = ["uv", "run", "pytest"] + args
    exit_code, stdout, stderr, duration = quality_gate.run_command(cmd, cwd=PROJECT_ROOT)
    findings = quality_gate.parse_pytest_output(stdout, stderr)
    summary = _extract_pytest_summary(stdout, stderr)
    summary_counts = _parse_pytest_summary_counts(summary)
    if summary_counts.get("duration_seconds") == 0.0:
        summary_counts["duration_seconds"] = round(duration, 2)
    status = "passed" if exit_code == 0 else "failed"
    return HealthCheckResult(
        name="tests",
        status=status,
        summary=summary,
        duration_seconds=duration,
        details={
            "command": " ".join(cmd),
            "findings": findings,
            "summary_counts": summary_counts,
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr[-2000:],
        },
    )


def _normalize_test_summary(raw: dict[str, Any]) -> dict[str, Any]:
    counts = {
        "passed": _coerce_int(raw.get("passed")),
        "failed": _coerce_int(raw.get("failed")),
        "errors": _coerce_int(raw.get("errors")),
        "skipped": _coerce_int(raw.get("skipped")),
    }
    total = _coerce_int(raw.get("total"))
    if total == 0:
        total = sum(counts.values())
    duration_seconds = _coerce_float(raw.get("duration_seconds"))
    return {**counts, "total": total, "duration_seconds": duration_seconds}


def _load_ci_input(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": str(path),
        "format": "unknown",
        "status": "ok",
        "summary": None,
    }

    if not path.exists():
        entry["status"] = "missing"
        entry["error"] = "File not found"
        return entry

    if path.suffix.lower() in {".xml", ".junit"}:
        try:
            summary = _parse_junit_xml(path.read_text(encoding="utf-8"))
        except Exception as exc:
            entry["status"] = "invalid"
            entry["format"] = "junit-xml"
            entry["error"] = str(exc)
            return entry
        entry["format"] = "junit-xml"
        entry["summary"] = _normalize_test_summary(summary)
        return entry

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        entry["status"] = "invalid"
        entry["error"] = str(exc)
        return entry

    if isinstance(payload, dict) and "summary" in payload:
        summary = _parse_pytest_json_report(payload)
        entry["format"] = "pytest-json"
        entry["summary"] = _normalize_test_summary(summary)
        return entry

    entry["status"] = "unsupported"
    entry["error"] = "Unsupported CI input format"
    return entry


def _build_test_summary(
    *,
    results: list[HealthCheckResult],
    ci_inputs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    sources: list[dict[str, Any]] = []

    for entry in ci_inputs:
        if entry.get("summary"):
            sources.append(
                {
                    "source": "ci-input",
                    "path": entry.get("path"),
                    "format": entry.get("format"),
                    "summary": entry["summary"],
                }
            )

    for result in results:
        if result.name != "tests":
            continue
        summary_counts = result.details.get("summary_counts")
        if summary_counts:
            sources.append(
                {
                    "source": "pytest",
                    "summary": _normalize_test_summary(summary_counts),
                }
            )

    if not sources:
        return None

    totals = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0}
    duration_seconds = 0.0
    for source in sources:
        summary = _normalize_test_summary(source["summary"])
        source["summary"] = summary
        totals["passed"] += summary["passed"]
        totals["failed"] += summary["failed"]
        totals["errors"] += summary["errors"]
        totals["skipped"] += summary["skipped"]
        totals["total"] += summary["total"]
        duration_seconds += summary["duration_seconds"]

    return {
        **totals,
        "duration_seconds": round(duration_seconds, 2),
        "sources": sources,
    }


def _run_preflight(profile: Profile, verbose: bool, warn_only: bool) -> HealthCheckResult:
    stdout = StringIO()
    stderr = StringIO()
    previous_warn_only = os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY")
    previous_skip_remote = os.environ.get("COINBASE_PREFLIGHT_SKIP_REMOTE")
    if warn_only:
        os.environ["GPT_TRADER_PREFLIGHT_WARN_ONLY"] = "1"
    if is_dev_profile(profile.value) and os.environ.get("COINBASE_PREFLIGHT_FORCE_REMOTE") != "1":
        os.environ["COINBASE_PREFLIGHT_SKIP_REMOTE"] = "1"

    checker = PreflightCheck(verbose=verbose, profile=profile.value)
    check_functions = [
        checker.check_python_version,
        checker.check_dependencies,
        checker.check_environment_variables,
        checker.check_api_connectivity,
        checker.check_key_permissions,
        checker.check_risk_configuration,
        checker.check_pretrade_diagnostics,
        checker.check_test_suite,
        checker.check_profile_configuration,
        checker.check_system_time,
        checker.check_disk_space,
        checker.simulate_dry_run,
    ]

    try:
        start = time.time()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            for check in check_functions:
                try:
                    check()
                except Exception as exc:  # pragma: no cover - defensive
                    checker.log_error(f"Preflight check failed: {exc}")
        duration = time.time() - start
    finally:
        if previous_warn_only is None:
            os.environ.pop("GPT_TRADER_PREFLIGHT_WARN_ONLY", None)
        else:
            os.environ["GPT_TRADER_PREFLIGHT_WARN_ONLY"] = previous_warn_only
        if previous_skip_remote is None:
            os.environ.pop("COINBASE_PREFLIGHT_SKIP_REMOTE", None)
        else:
            os.environ["COINBASE_PREFLIGHT_SKIP_REMOTE"] = previous_skip_remote

    errors = checker.errors
    warnings = checker.warnings
    successes = checker.successes

    if errors:
        status = "failed"
    elif warnings:
        status = "warning"
    else:
        status = "passed"

    summary = f"{len(successes)} passed, {len(warnings)} warnings, {len(errors)} errors"
    return HealthCheckResult(
        name="preflight",
        status=status,
        summary=summary,
        duration_seconds=duration,
        details={
            "successes": successes,
            "warnings": warnings,
            "errors": errors,
            "stdout": stdout.getvalue()[-4000:],
            "stderr": stderr.getvalue()[-2000:],
            "profile": profile.value,
        },
    )


def _run_config_validation(profile: Profile) -> HealthCheckResult:
    start = time.time()
    loader = ProfileLoader()
    schema = loader.load(profile)
    config = loader.build_bot_config(schema, profile)
    errors = validate_config(config)
    duration = time.time() - start

    status = "passed" if not errors else "failed"
    summary = "No config validation errors" if not errors else f"{len(errors)} errors"
    return HealthCheckResult(
        name="config_validation",
        status=status,
        summary=summary,
        duration_seconds=duration,
        details={
            "errors": errors,
            "profile": profile.value,
        },
    )


def _overall_status(results: list[HealthCheckResult]) -> str:
    statuses = {result.status for result in results}
    if "failed" in statuses:
        return "failed"
    if "warning" in statuses:
        return "warning"
    if statuses == {"skipped"}:
        return "skipped"
    return "passed"


def _build_hints(results: list[HealthCheckResult]) -> list[str]:
    hints: list[str] = []
    for result in results:
        if result.status != "failed":
            continue
        if result.name == "lint":
            hints.append("Run `uv run ruff check .` to inspect lint issues.")
        elif result.name == "format":
            hints.append("Run `uv run ruff format .` to fix formatting.")
        elif result.name == "types":
            hints.append("Run `uv run mypy src/gpt_trader` for type details.")
        elif result.name == "tests":
            command = result.details.get("command", "pytest")
            hints.append(f"Re-run failing tests with `{command}`.")
        elif result.name == "preflight":
            profile = result.details.get("profile", "canary")
            hints.append(
                "Run `uv run python scripts/production_preflight.py --profile "
                f"{profile} --verbose` for full preflight output."
            )
        elif result.name == "config_validation":
            hints.append("Fix config validation errors or update the profile defaults.")
    return hints


def build_schema() -> dict[str, Any]:
    summary_schema = {
        "type": "object",
        "properties": {
            "passed": {"type": "integer"},
            "failed": {"type": "integer"},
            "errors": {"type": "integer"},
            "skipped": {"type": "integer"},
            "total": {"type": "integer"},
            "duration_seconds": {"type": "number"},
        },
    }

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Agent Health Report",
        "type": "object",
        "required": ["schema_version", "tool", "generated_at", "status", "checks", "summary"],
        "properties": {
            "schema_version": {"type": "string"},
            "tool": {
                "type": "object",
                "required": ["name", "version", "script"],
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "script": {"type": "string"},
                },
            },
            "generated_at": {"type": "string"},
            "profile": {"type": "string"},
            "status": {"type": "string", "enum": ["passed", "failed", "warning", "skipped"]},
            "summary": {
                "type": "object",
                "properties": {
                    "passed": {"type": "integer"},
                    "failed": {"type": "integer"},
                    "warning": {"type": "integer"},
                    "skipped": {"type": "integer"},
                },
            },
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "status", "summary", "duration_seconds"],
                    "properties": {
                        "name": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["passed", "failed", "warning", "skipped"],
                        },
                        "summary": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                        "details": {"type": "object"},
                    },
                },
            },
            "ci_inputs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "format": {"type": "string"},
                        "status": {"type": "string"},
                        "summary": summary_schema,
                        "error": {"type": "string"},
                    },
                },
            },
            "test_summary": {
                "type": "object",
                "properties": {
                    **summary_schema["properties"],
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "path": {"type": "string"},
                                "format": {"type": "string"},
                                "summary": summary_schema,
                            },
                        },
                    },
                },
            },
            "hints": {"type": "array", "items": {"type": "string"}},
        },
    }


def build_example() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "name": TOOL_NAME,
            "version": APP_VERSION,
            "script": TOOL_SCRIPT,
        },
        "generated_at": "1970-01-01T00:00:00+00:00",
        "profile": "dev",
        "status": "warning",
        "summary": {"passed": 3, "failed": 0, "warning": 1, "skipped": 1},
        "checks": [
            {
                "name": "lint",
                "status": "passed",
                "summary": "No linting issues",
                "duration_seconds": 0.2,
                "details": {"command": "uv run ruff check ."},
            },
            {
                "name": "types",
                "status": "passed",
                "summary": "No type errors",
                "duration_seconds": 20.1,
                "details": {"command": "uv run mypy src/gpt_trader"},
            },
            {
                "name": "tests",
                "status": "skipped",
                "summary": "Skipped (no --pytest-args)",
                "duration_seconds": 0.0,
                "details": {},
            },
            {
                "name": "preflight",
                "status": "warning",
                "summary": "9 passed, 2 warnings, 0 errors",
                "duration_seconds": 4.2,
                "details": {"profile": "dev"},
            },
            {
                "name": "config_validation",
                "status": "passed",
                "summary": "No config validation errors",
                "duration_seconds": 0.1,
                "details": {"profile": "dev"},
            },
        ],
        "ci_inputs": [
            {
                "path": "var/results/pytest_report.json",
                "format": "pytest-json",
                "status": "ok",
                "summary": {
                    "passed": 120,
                    "failed": 2,
                    "errors": 0,
                    "skipped": 4,
                    "total": 126,
                    "duration_seconds": 12.4,
                },
            }
        ],
        "test_summary": {
            "passed": 120,
            "failed": 2,
            "errors": 0,
            "skipped": 4,
            "total": 126,
            "duration_seconds": 12.4,
            "sources": [
                {
                    "source": "ci-input",
                    "path": "var/results/pytest_report.json",
                    "format": "pytest-json",
                    "summary": {
                        "passed": 120,
                        "failed": 2,
                        "errors": 0,
                        "skipped": 4,
                        "total": 126,
                        "duration_seconds": 12.4,
                    },
                }
            ],
        },
        "hints": ["Run `uv run python scripts/production_preflight.py --profile dev --verbose`."],
    }


def build_report(
    results: list[HealthCheckResult],
    profile: Profile,
    ci_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = {
        "passed": sum(1 for result in results if result.status == "passed"),
        "failed": sum(1 for result in results if result.status == "failed"),
        "warning": sum(1 for result in results if result.status == "warning"),
        "skipped": sum(1 for result in results if result.status == "skipped"),
    }
    test_summary = _build_test_summary(results=results, ci_inputs=ci_inputs)
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "name": TOOL_NAME,
            "version": APP_VERSION,
            "script": TOOL_SCRIPT,
        },
        "generated_at": _timestamp(),
        "profile": profile.value,
        "status": _overall_status(results),
        "summary": summary,
        "checks": [
            {
                "name": result.name,
                "status": result.status,
                "summary": result.summary,
                "duration_seconds": round(result.duration_seconds, 2),
                "details": result.details,
            }
            for result in results
        ],
        "ci_inputs": ci_inputs,
        "test_summary": test_summary,
        "hints": _build_hints(results),
    }
    return report


def format_text_report(report: dict[str, Any]) -> str:
    lines = [
        "Agent Health Report",
        f"Status: {report['status'].upper()} (profile: {report.get('profile', 'unknown')})",
        f"Generated: {report['generated_at']}",
        "",
        "Checks:",
    ]
    for check in report["checks"]:
        lines.append(
            f"- {check['name']}: {check['status']} ({check['summary']}, {check['duration_seconds']}s)"
        )

    test_summary = report.get("test_summary")
    if test_summary:
        lines.append("")
        lines.append(
            "Test Summary: "
            f"total={test_summary['total']} "
            f"passed={test_summary['passed']} "
            f"failed={test_summary['failed']} "
            f"errors={test_summary['errors']} "
            f"skipped={test_summary['skipped']} "
            f"duration={test_summary['duration_seconds']}s"
        )
        sources = test_summary.get("sources", [])
        if sources:
            lines.append("Test Sources:")
            for source in sources:
                summary = source.get("summary", {})
                label = source.get("path") or source.get("format") or source.get("source")
                lines.append(
                    "- {label}: total={total} passed={passed} failed={failed} "
                    "errors={errors} skipped={skipped} duration={duration}s".format(
                        label=label,
                        total=summary.get("total", 0),
                        passed=summary.get("passed", 0),
                        failed=summary.get("failed", 0),
                        errors=summary.get("errors", 0),
                        skipped=summary.get("skipped", 0),
                        duration=summary.get("duration_seconds", 0.0),
                    )
                )

    hints = report.get("hints", [])
    if hints:
        lines.append("")
        lines.append("Hints:")
        for hint in hints:
            lines.append(f"- {hint}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run agent-health report")
    parser.add_argument(
        "--profile",
        default="dev",
        help="Profile to validate (default: dev)",
        choices=[profile.value for profile in Profile],
    )
    parser.add_argument(
        "--quality-checks",
        default="lint,format,types",
        help="Comma-separated list of quality checks (lint,format,types)",
    )
    parser.add_argument(
        "--quality-files",
        nargs="+",
        help="Limit quality checks to specific files or directories",
    )
    parser.add_argument(
        "--quality-full",
        action="store_true",
        help="Use full quality checks (disables fast mode)",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight checks",
    )
    parser.add_argument(
        "--skip-config",
        action="store_true",
        help="Skip config validation",
    )
    parser.add_argument(
        "--preflight-warn-only",
        action="store_true",
        help="Treat preflight errors as warnings",
    )
    parser.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to pytest (if omitted, tests are skipped)",
    )
    parser.add_argument(
        "--ci-input",
        action="append",
        type=Path,
        help="Path to pytest JSON or JUnit XML to merge into test summary",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output file path (defaults to stdout)",
    )
    parser.add_argument(
        "--text-output",
        type=Path,
        help="Optional text output path (defaults to health_report.txt next to JSON output)",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Output JSON schema and exit",
    )
    parser.add_argument(
        "--example",
        action="store_true",
        help="Output example JSON and exit",
    )

    args = parser.parse_args()

    if load_dotenv is not None:
        load_dotenv()

    if args.schema:
        output = json.dumps(build_schema(), indent=2)
        if args.output:
            _ensure_parent_dir(args.output)
            args.output.write_text(output)
        else:
            print(output)
        return 0

    if args.example:
        output = json.dumps(build_example(), indent=2)
        if args.output:
            _ensure_parent_dir(args.output)
            args.output.write_text(output)
        else:
            print(output)
        return 0

    profile = _parse_profile(args.profile)
    quality_checks = [c.strip() for c in args.quality_checks.split(",") if c.strip()]
    ci_inputs = [_load_ci_input(path) for path in (args.ci_input or [])]

    results: list[HealthCheckResult] = []
    results.extend(
        _run_quality_checks(
            checks=quality_checks,
            files=args.quality_files,
            full=args.quality_full,
        )
    )

    pytest_args = list(args.pytest_args) if args.pytest_args else []
    if not pytest_args:
        results.append(
            HealthCheckResult(
                name="tests",
                status="skipped",
                summary="Skipped (no --pytest-args)",
                duration_seconds=0.0,
                details={},
            )
        )
    else:
        results.append(_run_pytest(pytest_args))

    if args.skip_preflight:
        results.append(
            HealthCheckResult(
                name="preflight",
                status="skipped",
                summary="Skipped by flag",
                duration_seconds=0.0,
                details={},
            )
        )
    else:
        results.append(_run_preflight(profile, verbose=False, warn_only=args.preflight_warn_only))

    if args.skip_config:
        results.append(
            HealthCheckResult(
                name="config_validation",
                status="skipped",
                summary="Skipped by flag",
                duration_seconds=0.0,
                details={},
            )
        )
    else:
        results.append(_run_config_validation(profile))

    report = build_report(results, profile, ci_inputs)
    output = json.dumps(report, indent=2) if args.format == "json" else format_text_report(report)

    if args.output:
        _ensure_parent_dir(args.output)
        args.output.write_text(output)
    else:
        print(output)

    text_output: Path | None = None
    if args.text_output:
        text_output = args.text_output
    elif args.format == "json" and args.output:
        text_output = args.output.with_name("health_report.txt")

    if text_output:
        _ensure_parent_dir(text_output)
        text_output.write_text(format_text_report(report))

    exit_code = 1 if report["status"] == "failed" else 0
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
