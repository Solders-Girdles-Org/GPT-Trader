from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from gpt_trader.app.config.profile_loader import (
    DEFAULT_PREFLIGHT_PROFILE_NAME,
    ProfileOverrideDecision,
    get_env_profile_override,
    resolve_profile_override,
)

from .check_graph import (
    CORE_PREFLIGHT_CHECKS,
    PreflightCheckGraphError,
    assemble_preflight_check_graph,
)
from .cli_args import PreflightCliArgs, parse_preflight_args
from .context import Colors
from .core import PreflightCheck
from .diagnostics_bundle import build_diagnostics_bundle

load_dotenv: Callable[..., bool] | None
try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover - optional dependency for CLI convenience
    load_dotenv = None
else:
    load_dotenv = _load_dotenv


def _header(profile: str) -> None:
    print(f"{Colors.BOLD}{Colors.MAGENTA}")
    print("=" * 70)
    print("GPT-TRADER PRODUCTION PREFLIGHT CHECK")
    print(f"Profile: {profile}")
    print(f"Time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)
    print(f"{Colors.RESET}")


def _resolve_preflight_profile(args: PreflightCliArgs) -> ProfileOverrideDecision:
    return resolve_profile_override(
        cli_profile=args.profile,
        file_profile=None,
        env_profile=get_env_profile_override(),
        default_profile=DEFAULT_PREFLIGHT_PROFILE_NAME,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point for production preflight command."""
    args = parse_preflight_args(argv)
    return _run_preflight(args)


def _run_preflight(args: PreflightCliArgs) -> int:
    if load_dotenv is not None:
        load_dotenv()

    profile_decision = _resolve_preflight_profile(args)

    if args.diagnostics_bundle:
        return _emit_diagnostics_bundle(args)

    previous_warn_only = os.environ.get("GPT_TRADER_PREFLIGHT_WARN_ONLY")
    if args.warn_only:
        os.environ["GPT_TRADER_PREFLIGHT_WARN_ONLY"] = "1"

    try:
        _header(profile_decision.profile)

        checker = PreflightCheck(verbose=args.verbose, profile=profile_decision.profile)
        try:
            check_functions = _resolve_preflight_checks(checker)
        except PreflightCheckGraphError as exc:
            checker.context.set_current_check("preflight_graph")
            checker.log_error(str(exc))
            checker.context.set_current_check(None)
            checker.generate_report(
                report_dir=args.report_dir,
                report_path=args.report_path,
                report_target=args.report_target,
            )
            return 1

        for check in check_functions:
            check_name = getattr(check, "__name__", type(check).__name__)
            checker.context.set_current_check(check_name)
            try:
                check()
            except Exception as exc:  # pragma: no cover - defensive runtime safeguard
                checker.log_error(f"Check failed with exception: {exc}")
            finally:
                checker.context.set_current_check(None)

        success, _status = checker.generate_report(
            report_dir=args.report_dir,
            report_path=args.report_path,
            report_target=args.report_target,
        )
        return 0 if success else 1
    finally:
        if args.warn_only:
            if previous_warn_only is None:
                os.environ.pop("GPT_TRADER_PREFLIGHT_WARN_ONLY", None)
            else:
                os.environ["GPT_TRADER_PREFLIGHT_WARN_ONLY"] = previous_warn_only


def _emit_diagnostics_bundle(args: PreflightCliArgs) -> int:
    try:
        profile_decision = _resolve_preflight_profile(args)
        bundle = build_diagnostics_bundle(
            profile=profile_decision.profile,
            verbose=args.verbose,
            warn_only=args.warn_only,
        )
        print(json.dumps(bundle, indent=2))
        return 0
    except Exception as exc:  # pragma: no cover - defensive safeguard
        print(f"Error generating diagnostics bundle: {exc}", file=sys.stderr)
        return 1


def _resolve_preflight_checks(checker: PreflightCheck) -> Sequence[Callable[[], bool]]:
    ordered_nodes = assemble_preflight_check_graph(CORE_PREFLIGHT_CHECKS)
    checks: list[Callable[[], bool]] = []
    for node in ordered_nodes:
        check = getattr(checker, node.name, None)
        if check is None:
            raise PreflightCheckGraphError(f"Preflight check implementation missing: {node.name}")
        checks.append(check)
    return tuple(checks)
