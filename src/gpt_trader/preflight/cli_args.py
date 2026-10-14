from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gpt_trader.app.config.profile_loader import (
    PREFLIGHT_PROFILE_CHOICES,
)
from gpt_trader.preflight.report import ReportTarget

PROFILE_CHOICES = PREFLIGHT_PROFILE_CHOICES


@dataclass(frozen=True)
class PreflightCliArgs:
    verbose: bool
    profile: str | None
    warn_only: bool
    diagnostics_bundle: bool
    report_dir: Path | None
    report_path: Path | None
    report_target: ReportTarget


def add_preflight_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--profile",
        "-p",
        default=None,
        choices=PROFILE_CHOICES,
        help="Trading profile to validate (default: canary)",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Downgrade diagnostic failures to warnings (also: GPT_TRADER_PREFLIGHT_WARN_ONLY=1)",
    )
    parser.add_argument(
        "--diagnostics-bundle",
        action="store_true",
        help="Emit a compact diagnostics bundle (JSON) instead of the regular report",
    )

    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--report-dir",
        type=Path,
        help="Directory for preflight_report_*.json output (default: current working dir)",
    )
    output_group.add_argument(
        "--report-path",
        type=Path,
        help="Explicit file path for preflight report JSON",
    )
    parser.add_argument(
        "--report-target",
        type=ReportTarget,
        choices=list(ReportTarget),
        # str(ReportTarget.FILE) is "ReportTarget.FILE"; show the accepted values.
        metavar="{" + ",".join(target.value for target in ReportTarget) + "}",
        default=ReportTarget.FILE,
        help="Artifact target for the preflight report (default: file)",
    )


def build_preflight_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Production preflight check for GPT-Trader")
    add_preflight_arguments(parser)
    return parser


def parse_preflight_args(argv: Sequence[str] | None = None) -> PreflightCliArgs:
    parser = build_preflight_parser()
    args = parser.parse_args(argv)
    return _normalize_preflight_args(parser, args)


def _normalize_preflight_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> PreflightCliArgs:
    report_dir = _normalize_report_dir(parser, args.report_dir)
    report_path = _normalize_report_path(parser, args.report_path)
    report_target = args.report_target

    if report_target == ReportTarget.STDOUT and (report_dir is not None or report_path is not None):
        parser.error("--report-target stdout cannot be combined with --report-dir/--report-path")

    return PreflightCliArgs(
        verbose=bool(args.verbose),
        profile=args.profile,
        warn_only=bool(args.warn_only),
        diagnostics_bundle=bool(args.diagnostics_bundle),
        report_dir=report_dir,
        report_path=report_path,
        report_target=report_target,
    )


def _normalize_report_dir(parser: argparse.ArgumentParser, value: Path | None) -> Path | None:
    if value is None:
        return None
    resolved = value.expanduser().resolve(strict=False)
    if resolved.exists() and not resolved.is_dir():
        parser.error(f"Report directory points to a file: {resolved}")
    return resolved


def _normalize_report_path(parser: argparse.ArgumentParser, value: Path | None) -> Path | None:
    if value is None:
        return None
    resolved = value.expanduser().resolve(strict=False)
    if resolved.exists() and resolved.is_dir():
        parser.error(f"Report path must be a file, got directory: {resolved}")
    return resolved
