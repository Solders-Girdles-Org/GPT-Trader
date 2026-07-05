from __future__ import annotations

from types import SimpleNamespace

from gpt_trader.ci import local_ci


def _make_args(profile: str) -> SimpleNamespace:
    return SimpleNamespace(
        include_property_tests=False,
        include_contract_tests=False,
        include_integration_tests=False,
        include_agent_health=False,
        profile=profile,
    )


def _find_step(steps, label: str):
    return next(step for step in steps if step.label == label)


def test_profile_aliases_map_to_canonical_names() -> None:
    assert local_ci.resolve_profile("pr").canonical_name == "pr"
    assert local_ci.resolve_profile("full").canonical_name == "strict"
    assert local_ci.resolve_profile("dev").canonical_name == "quick"


def test_default_profile_is_pr() -> None:
    args = local_ci.parse_args([])
    assert args.profile == "pr"


def test_pr_profile_matches_pull_request_surface() -> None:
    """The default profile runs the required suites but not the readiness gate."""
    args = _make_args("pr")
    profile = local_ci.resolve_profile(args.profile)
    steps = local_ci.build_steps(profile, args)

    assert _find_step(steps, "Readiness gate (3-day streak)").enabled is False
    artifacts_step = _find_step(steps, "Agent artifacts freshness")
    assert artifacts_step.enabled is True
    assert artifacts_step.advisory is True
    assert _find_step(steps, "Property tests").enabled is True
    assert _find_step(steps, "Contract tests").enabled is True
    assert _find_step(steps, "Integration tests").enabled is True


def test_integration_step_matches_ci_lane_command() -> None:
    args = _make_args("pr")
    profile = local_ci.resolve_profile(args.profile)
    steps = local_ci.build_steps(profile, args)

    integration_step = _find_step(steps, "Integration tests")
    assert integration_step.command == [
        "uv",
        "run",
        "pytest",
        "-o",
        "addopts=",
        "-m",
        "integration and not slow and not real_api",
        "tests/integration",
        "-q",
    ]


def test_quick_profile_skips_required_suites_unless_included() -> None:
    args = _make_args("quick")
    profile = local_ci.resolve_profile(args.profile)
    steps = local_ci.build_steps(profile, args)
    assert _find_step(steps, "Property tests").enabled is False
    assert _find_step(steps, "Integration tests").enabled is False

    args.include_integration_tests = True
    steps = local_ci.build_steps(profile, args)
    assert _find_step(steps, "Integration tests").enabled is True


def test_quick_profile_skips_readiness_and_agent_artifacts() -> None:
    args = _make_args("quick")
    profile = local_ci.resolve_profile(args.profile)
    steps = local_ci.build_steps(profile, args)

    readiness_step = _find_step(steps, "Readiness gate (3-day streak)")
    assert readiness_step.enabled is False
    assert "Use the strict profile when you need the readiness gate" in readiness_step.skip_reason

    artifacts_step = _find_step(steps, "Agent artifacts freshness")
    assert artifacts_step.enabled is False
    assert "Agent artifacts freshness is disabled in quick/dev" in artifacts_step.skip_reason


def test_strict_profile_runs_readiness_and_agent_artifacts() -> None:
    args = _make_args("strict")
    profile = local_ci.resolve_profile(args.profile)
    steps = local_ci.build_steps(profile, args)

    readiness_step = _find_step(steps, "Readiness gate (3-day streak)")
    artifacts_step = _find_step(steps, "Agent artifacts freshness")

    assert readiness_step.enabled is True
    assert readiness_step.skip_reason is None
    assert artifacts_step.enabled is True
    assert artifacts_step.skip_reason is None


def test_strict_profile_description_distinguishes_pr_and_readiness() -> None:
    profile = local_ci.resolve_profile("strict")

    assert "local PR-readiness validation set" in profile.description
    assert "readiness checks beyond GitHub pull_request CI" in profile.description


def test_triage_backlog_step_uses_portable_python_command() -> None:
    args = _make_args("quick")
    profile = local_ci.resolve_profile(args.profile)
    steps = local_ci.build_steps(profile, args)

    triage_step = _find_step(steps, "Check triage backlog")

    assert triage_step.command == [
        "uv",
        "run",
        "python",
        "scripts/maintenance/test_legacy_triage.py",
        "--check",
    ]


def test_print_profile_banner_reports_alias_and_status(capsys) -> None:
    selection = "dev"
    profile = local_ci.resolve_profile(selection)
    local_ci.print_profile_banner(selection, profile)

    output = capsys.readouterr().out
    assert "Local CI profile: quick (alias 'dev')" in output
    assert "Readiness gate: disabled" in output
    assert "Agent artifacts freshness: disabled" in output


def test_strict_profile_banner_distinguishes_pull_request_ci(capsys) -> None:
    profile = local_ci.resolve_profile("strict")
    local_ci.print_profile_banner("strict", profile)
    output = capsys.readouterr().out
    assert "local PR-readiness validation set" in output
    assert "readiness checks beyond GitHub pull_request CI" in output


def test_agent_artifacts_freshness_is_advisory_in_strict_profile() -> None:
    args = _make_args("strict")
    profile = local_ci.resolve_profile(args.profile)
    steps = local_ci.build_steps(profile, args)

    freshness_step = _find_step(steps, "Agent artifacts freshness")

    assert freshness_step.enabled is True
    assert freshness_step.advisory is True


def test_run_steps_marks_advisory_failure_as_warn(tmp_path) -> None:
    step = local_ci.PlannedStep(
        label="Advisory probe",
        command=["python", "-c", "import sys; sys.exit(3)"],
        advisory=True,
    )

    results = local_ci.run_steps([step], tmp_path)

    assert results[0].status == "warn"
    assert results[0].return_code == 3
    # Advisory warnings must not fail the overall run.
    assert not any(result.status == "fail" for result in results)


def test_run_steps_marks_non_advisory_failure_as_fail(tmp_path) -> None:
    step = local_ci.PlannedStep(
        label="Blocking probe",
        command=["python", "-c", "import sys; sys.exit(3)"],
    )

    results = local_ci.run_steps([step], tmp_path)

    assert results[0].status == "fail"
