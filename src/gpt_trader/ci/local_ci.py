from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

AGENT_HEALTH_SCRIPT = "\n".join(
    [
        "set -u",
        "set -o pipefail",
        "mkdir -p var/agents/health",
        "set +e",
        "make agent-health-fast AGENT_HEALTH_FAST_QUALITY_CHECKS=none",
        "status=$?",
        "set -e",
        "if [ -f var/agents/health/health_report.json ]; then",
        "  uv run python - <<'PY' | tee var/agents/health/health_report.txt",
        "from scripts.agents.health_report import format_text_report",
        "import json",
        'with open("var/agents/health/health_report.json") as handle:',
        "    report = json.load(handle)",
        "print(format_text_report(report))",
        "PY",
        "fi",
        "exit $status",
    ]
)


@dataclass(frozen=True)
class PlannedStep:
    label: str
    command: Sequence[str]
    env: dict[str, str] | None = None
    enabled: bool = True
    skip_reason: str | None = None
    # Advisory steps run but never fail the overall suite: a non-zero exit is
    # surfaced as a non-blocking warning. Used to align local checks with CI
    # lanes that are advisory on pull requests (e.g. agent artifacts freshness).
    advisory: bool = False


@dataclass(frozen=True)
class LocalCIProfile:
    canonical_name: str
    description: str
    readiness_enabled: bool
    readiness_skip_reason: str | None
    agent_artifacts_enabled: bool
    agent_artifacts_skip_reason: str | None
    # Property/contract/integration lanes are required GitHub pull_request
    # checks, so the default pr profile (and strict) runs them; quick skips
    # them for a fast development loop.
    required_suites_enabled: bool
    required_suites_skip_reason: str | None


LOCAL_CI_PROFILES: dict[str, LocalCIProfile] = {
    "pr": LocalCIProfile(
        canonical_name="pr",
        description=(
            "PR profile (default) matches the GitHub pull_request required-check "
            "surface: lint/format, docs audits, type check, test guardrails, unit, "
            "property, contract, and integration tests, with agent artifacts "
            "freshness as an advisory warning."
        ),
        readiness_enabled=False,
        readiness_skip_reason="The canary readiness gate is local/live evidence beyond GitHub pull_request CI; use the strict profile when you need it.",
        agent_artifacts_enabled=True,
        agent_artifacts_skip_reason=None,
        required_suites_enabled=True,
        required_suites_skip_reason=None,
    ),
    "strict": LocalCIProfile(
        canonical_name="strict",
        description=(
            "Strict profile runs the local PR-readiness validation set plus local/live "
            "readiness checks beyond GitHub pull_request CI."
        ),
        readiness_enabled=True,
        readiness_skip_reason=None,
        agent_artifacts_enabled=True,
        agent_artifacts_skip_reason=None,
        required_suites_enabled=True,
        required_suites_skip_reason=None,
    ),
    "quick": LocalCIProfile(
        canonical_name="quick",
        description="Quick/dev profile skips the readiness gate, agent artifacts freshness, and the property/contract/integration suites for a fast development loop.",
        readiness_enabled=False,
        readiness_skip_reason="Use the strict profile when you need the readiness gate; quick/dev skips it because readiness data may be missing in short-lived loops.",
        agent_artifacts_enabled=False,
        agent_artifacts_skip_reason="Agent artifacts freshness is disabled in quick/dev to avoid regenerating var/agents; run strict before merging if you need that check.",
        required_suites_enabled=False,
        required_suites_skip_reason="Property/contract/integration suites are skipped in quick/dev; the default pr profile runs the full GitHub pull_request surface.",
    ),
}

PROFILE_ALIASES: dict[str, str] = {
    "pr": "pr",
    "strict": "strict",
    "full": "strict",
    "quick": "quick",
    "dev": "quick",
}


@dataclass(frozen=True)
class StepResult:
    label: str
    status: str
    return_code: int | None
    note: str | None = None


def find_repo_root(start_path: Path) -> Path:
    for candidate in [start_path, *start_path.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    message = f"Could not find repo root from {start_path}"
    raise RuntimeError(message)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run GPT-Trader local validation. The default pr profile matches the "
            "GitHub pull_request required-check surface; strict adds local/live "
            "readiness evidence beyond it."
        )
    )
    parser.add_argument(
        "--include-property-tests",
        action="store_true",
        help="Include property tests (CI: property-tests).",
    )
    parser.add_argument(
        "--include-contract-tests",
        action="store_true",
        help="Include contract tests (CI: contract-tests).",
    )
    parser.add_argument(
        "--include-integration-tests",
        action="store_true",
        help="Include integration tests (CI: integration-tests).",
    )
    parser.add_argument(
        "--include-agent-health",
        action="store_true",
        help="Include agent-health fast checks (CI: agent-health).",
    )
    parser.add_argument(
        "--profile",
        "-p",
        type=str.lower,
        choices=sorted(PROFILE_ALIASES.keys()),
        default="pr",
        help=(
            "Select a local CI profile: pr (default, matches the GitHub "
            "pull_request required-check surface), strict/full (pr plus the "
            "readiness gate), or quick/dev (fast loop)."
        ),
    )
    return parser.parse_args(argv)


def resolve_profile(selection: str) -> LocalCIProfile:
    canonical = PROFILE_ALIASES.get(selection)
    if canonical is None:
        raise ValueError(f"Unknown local CI profile '{selection}'")
    return LOCAL_CI_PROFILES[canonical]


def _format_check_status(name: str, enabled: bool, reason: str | None) -> str:
    if enabled:
        return f"{name}: enabled"
    if reason:
        return f"{name}: disabled — {reason}"
    return f"{name}: disabled"


def print_profile_banner(selection: str, profile: LocalCIProfile) -> None:
    alias_note = "" if selection == profile.canonical_name else f" (alias '{selection}')"
    print(f"\nLocal CI profile: {profile.canonical_name}{alias_note}", flush=True)
    print(profile.description, flush=True)
    print("Profile check status:", flush=True)
    print(
        f"  - {_format_check_status('Readiness gate', profile.readiness_enabled, profile.readiness_skip_reason)}",
        flush=True,
    )
    print(
        f"  - {_format_check_status('Agent artifacts freshness', profile.agent_artifacts_enabled, profile.agent_artifacts_skip_reason)}",
        flush=True,
    )
    print(
        f"  - {_format_check_status('Property/contract/integration suites', profile.required_suites_enabled, profile.required_suites_skip_reason)}",
        flush=True,
    )


def build_steps(profile: LocalCIProfile, args: argparse.Namespace) -> list[PlannedStep]:
    test_env = {
        "GPT_TRADER_STRICT_CONTAINER": "1",
        "PYTHONWARNINGS": "default",
    }

    steps = [
        PlannedStep(
            label="Lint (Ruff)",
            command=["uv", "run", "ruff", "check", "."],
        ),
        PlannedStep(
            label="Lint (Ruff - critical scripts)",
            command=[
                "uv",
                "run",
                "ruff",
                "check",
                "scripts/ops",
                "scripts/ci",
                "scripts/analysis/backtest_runner.py",
                "scripts/monitoring",
                "scripts/production_preflight.py",
            ],
        ),
        PlannedStep(
            label="Format (Black)",
            command=["uv", "run", "black", "--check", "."],
        ),
        PlannedStep(
            label="Guard against orchestration imports (removed in v3.0)",
            command=["python", "scripts/ci/check_orchestration_imports.py"],
        ),
        PlannedStep(
            label="Guard deprecation registry",
            command=["python", "scripts/ci/check_deprecation_registry.py"],
        ),
        PlannedStep(
            label="Docs link audit",
            command=["python", "scripts/maintenance/docs_link_audit.py"],
        ),
        PlannedStep(
            label="Docs reachability check",
            command=["python", "scripts/maintenance/docs_reachability_check.py"],
        ),
        PlannedStep(
            label="Docs currency gate (missing/stale)",
            command=[
                "python",
                "scripts/maintenance/docs_currency_scan.py",
                "--fail-on",
                "missing,stale",
            ],
        ),
        PlannedStep(
            label="Type Check (MyPy)",
            command=["uv", "run", "mypy", "src"],
        ),
        PlannedStep(
            label="Agent health (fast, dev profile)",
            command=["bash", "-lc", AGENT_HEALTH_SCRIPT],
            enabled=args.include_agent_health,
            skip_reason="use --include-agent-health",
        ),
        PlannedStep(
            label="Agent artifacts freshness",
            command=["uv", "run", "agent-regenerate", "--verify"],
            enabled=profile.agent_artifacts_enabled,
            skip_reason=profile.agent_artifacts_skip_reason,
            # Advisory locally: GitHub pull_request CI already reports stale
            # artifacts non-blocking, and a scheduled lane refreshes main.
            # Non-PR GitHub CI remains the blocking enforcement point.
            advisory=True,
        ),
        PlannedStep(
            label="Check test hygiene",
            command=["uv", "run", "python", "scripts/ci/check_test_hygiene.py"],
        ),
        PlannedStep(
            label="Check legacy patterns",
            command=["uv", "run", "python", "scripts/ci/check_legacy_patterns.py"],
        ),
        PlannedStep(
            label="Check import boundaries",
            command=["uv", "run", "python", "scripts/ci/check_import_boundaries.py"],
        ),
        PlannedStep(
            label="Readiness gate (3-day streak)",
            command=[
                "python",
                "scripts/ci/check_readiness_gate.py",
                "--profile",
                "canary",
                "--strict",
            ],
            enabled=profile.readiness_enabled,
            skip_reason=profile.readiness_skip_reason,
        ),
        PlannedStep(
            label="Check legacy triage alignment",
            command=["uv", "run", "python", "scripts/ci/check_legacy_test_triage.py"],
        ),
        PlannedStep(
            label="Check dedupe manifest",
            command=["uv", "run", "python", "scripts/ci/check_dedupe_manifest.py"],
        ),
        PlannedStep(
            label="Check triage backlog",
            command=["uv", "run", "python", "scripts/maintenance/test_legacy_triage.py", "--check"],
        ),
        PlannedStep(
            label="Unit tests (core)",
            command=[
                "uv",
                "run",
                "pytest",
                "tests/unit",
                "-n",
                "auto",
                "-q",
            ],
            env=test_env,
        ),
        PlannedStep(
            label="Stage 1 rails end-to-end smoke",
            command=["uv", "run", "python", "scripts/ops/stage1_rails_smoke.py"],
        ),
        PlannedStep(
            label="Property tests",
            command=["uv", "run", "pytest", "tests/property", "-v"],
            env=test_env,
            enabled=profile.required_suites_enabled or args.include_property_tests,
            skip_reason=profile.required_suites_skip_reason or "use --include-property-tests",
        ),
        PlannedStep(
            label="Contract tests",
            command=["uv", "run", "pytest", "tests/contract", "-v"],
            env=test_env,
            enabled=profile.required_suites_enabled or args.include_contract_tests,
            skip_reason=profile.required_suites_skip_reason or "use --include-contract-tests",
        ),
        PlannedStep(
            label="Integration tests",
            command=[
                "uv",
                "run",
                "pytest",
                "-o",
                "addopts=",
                "-m",
                "integration and not slow and not real_api",
                "tests/integration",
                "-q",
            ],
            env=test_env,
            enabled=profile.required_suites_enabled or args.include_integration_tests,
            skip_reason=profile.required_suites_skip_reason or "use --include-integration-tests",
        ),
    ]
    return steps


def format_command(command: Sequence[str]) -> str:
    return shlex.join(command)


def run_steps(steps: Sequence[PlannedStep], repo_root: Path) -> list[StepResult]:
    results: list[StepResult] = []
    total_enabled = sum(1 for step in steps if step.enabled)
    run_index = 0
    for step in steps:
        if not step.enabled:
            results.append(
                StepResult(label=step.label, status="skip", return_code=None, note=step.skip_reason)
            )
            continue
        run_index += 1
        print(f"\n==> [{run_index}/{total_enabled}] {step.label}", flush=True)
        print(f"$ {format_command(step.command)}", flush=True)
        command_env = os.environ.copy()
        if step.env:
            command_env.update(step.env)
        try:
            completed = subprocess.run(
                step.command,
                check=False,
                cwd=repo_root,
                env=command_env,
            )
        except FileNotFoundError as exc:
            print(f"Command failed to start: {exc}", file=sys.stderr)
            results.append(
                StepResult(
                    label=step.label,
                    status="warn" if step.advisory else "fail",
                    return_code=127,
                    note="command not found",
                )
            )
            continue
        if completed.returncode == 0:
            status = "pass"
        elif step.advisory:
            status = "warn"
            print(
                f"::warning::{step.label} reported issues "
                f"(advisory, non-blocking; exit {completed.returncode})",
                flush=True,
            )
        else:
            status = "fail"
        results.append(
            StepResult(label=step.label, status=status, return_code=completed.returncode)
        )
    return results


def print_summary(results: Sequence[StepResult]) -> None:
    print("\nSummary:", flush=True)
    for result in results:
        status = result.status.upper().ljust(5)
        line = f"{status} {result.label}"
        if result.status in ("fail", "warn") and result.return_code is not None:
            line = f"{line} (exit {result.return_code})"
        if result.status == "warn":
            line = f"{line} (advisory, non-blocking)"
        if result.status == "skip" and result.note:
            line = f"{line} ({result.note})"
        print(line, flush=True)

    failed = [result for result in results if result.status == "fail"]
    warned = [result for result in results if result.status == "warn"]
    if failed:
        print(f"\nLocal CI failed: {len(failed)} check(s) failed.", flush=True)
        if warned:
            print(f"({len(warned)} advisory warning(s) reported — non-blocking.)", flush=True)
    elif warned:
        print(
            f"\nLocal CI passed with {len(warned)} advisory warning(s) — non-blocking.",
            flush=True,
        )
    else:
        print("\nLocal CI passed.", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    profile = resolve_profile(args.profile)
    repo_root = find_repo_root(Path(__file__).resolve())
    os.chdir(repo_root)
    print_profile_banner(args.profile, profile)
    steps = build_steps(profile, args)
    results = run_steps(steps, repo_root)
    print_summary(results)
    return 1 if any(result.status == "fail" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
