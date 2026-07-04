"""Command line interface entry point for the trading bot."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from gpt_trader.logging import configure_logging
from gpt_trader.utilities.logging_patterns import get_logger

# Preserve host-provided secrets; only fill gaps from .env
load_dotenv()

# Configure logging (rotating files + console)
configure_logging()  # CLI mode: enable console output
logger = get_logger(__name__, component="cli")

# fmt: off
from gpt_trader.cli.commands import (  # noqa: E402
    account,
    broker_connectivity,
    coinbase,
    console,
    controls,
    ideas,
    optimize,
    orders,
    preflight,
    record,
    report,
    run,
    strategy_profile,
)

# fmt: on
from . import services as _cli_services  # noqa: E402, F401
from .response import CliErrorCode, CliResponse, RawCliOutput, format_response  # noqa: E402

COMMAND_NAMES = {
    "run",
    "account",
    "broker-check",
    "coinbase",
    "console",
    "controls",
    "ideas",
    "orders",
    "record",
    "report",
    "optimize",
    "strategy",
    "preflight",
}
__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    normalized = _ensure_command(argv if argv is not None else sys.argv[1:])
    args = parser.parse_args(normalized)
    _maybe_enable_debug_logging()

    # Determine output format from args (may not be set by all commands yet)
    output_format = getattr(args, "output_format", "text")
    output_file: Path | None = getattr(args, "output", None)
    command_name = _get_command_name(args)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.error("No command handler configured.")

    # Execute command with error handling
    try:
        result = handler(args)
        exit_code = _handle_result(result, output_format, output_file, command_name)
    except Exception as e:
        exit_code = _handle_exception(e, output_format, output_file, command_name)

    return exit_code


def _handle_result(
    result: Any, output_format: str, output_file: Path | None, command_name: str
) -> int:
    """Process command result based on type and output format.

    Args:
        result: Command result (CliResponse, int, or None)
        output_format: "text" or "json"
        output_file: Optional file to write output to
        command_name: Command name for envelope

    Returns:
        Exit code
    """
    if isinstance(result, RawCliOutput):
        _write_raw_output(result.content, output_file)
        return result.exit_code
    if isinstance(result, CliResponse):
        # New-style response - format according to output_format
        output = format_response(result, output_format)
        _write_output(output, output_file)
        return result.exit_code
    elif isinstance(result, int):
        # Legacy int return - wrap in envelope for JSON mode
        if output_format == "json":
            response = CliResponse(
                success=result == 0,
                command=command_name,
                exit_code=result,
            )
            _write_output(response.to_json(), output_file)
        # Text mode: command already printed output
        return result
    else:
        # None or unexpected return
        if output_format == "json":
            response = CliResponse.success_response(command_name)
            _write_output(response.to_json(), output_file)
        return 0


def _handle_exception(
    error: Exception, output_format: str, output_file: Path | None, command_name: str
) -> int:
    """Handle unexpected exceptions with structured error output.

    Args:
        error: The exception that occurred
        output_format: "text" or "json"
        output_file: Optional file to write output to
        command_name: Command name for envelope

    Returns:
        Exit code (always 1)
    """
    logger.exception("Command failed with exception")

    if output_format == "json":
        response = CliResponse.error_response(
            command=command_name,
            code=CliErrorCode.INTERNAL_ERROR,
            message=str(error),
            details={"exception_type": type(error).__name__},
        )
        _write_output(response.to_json(), output_file)
    else:
        print(f"Error: {error}", file=sys.stderr)

    return 1


def _write_output(content: str, output_file: Path | None) -> None:
    """Write output to file or stdout."""
    if not content:
        return  # Already handled by rich console

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
    else:
        print(content)


def _write_raw_output(content: str, output_file: Path | None) -> None:
    """Write command-owned output without adding formatting or extra newlines."""
    if not content:
        return

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)


def _get_command_name(args: argparse.Namespace) -> str:
    """Extract full command name from parsed args."""
    parts = [args.command] if hasattr(args, "command") and args.command else []
    if hasattr(args, "subcommand") and args.subcommand:
        parts.append(args.subcommand)
    return " ".join(parts) if parts else "unknown"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coinbase Trading Bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run.register(subparsers)
    record.register(subparsers)
    account.register(subparsers)
    coinbase.register(subparsers)
    console.register(subparsers)
    controls.register(subparsers)
    ideas.register(subparsers)
    orders.register(subparsers)
    report.register(subparsers)
    strategy_profile.register(subparsers)
    optimize.register(subparsers)
    preflight.register(subparsers)
    broker_connectivity.register(subparsers)

    return parser


def _ensure_command(argv: Sequence[str]) -> list[str]:
    if not argv:
        return ["run"]

    if any(token in {"-h", "--help"} for token in argv):
        return list(argv)

    first = argv[0]
    if first in COMMAND_NAMES:
        return list(argv)

    if first.startswith("-"):
        return ["run", *argv]

    # Unrecognised token – default to the run command while preserving args
    return ["run", *argv]


def _maybe_enable_debug_logging() -> None:
    if _env_flag("COINBASE_TRADER_DEBUG") or _env_flag("PERPS_DEBUG"):
        logging.getLogger("gpt_trader.features.brokerages.coinbase").setLevel(logging.DEBUG)
        logging.getLogger("gpt_trader.features.live_trade").setLevel(logging.DEBUG)


def _env_flag(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
