"""Standard CLI response envelope for AI agent consumption.

Provides structured, machine-readable output for CLI commands with:
- Consistent success/error indication
- Typed error codes for programmatic handling
- Warnings separate from errors
- Operation metadata (timestamps, no-op detection)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class CliErrorCode(str, Enum):
    """Standardized error codes for CLI operations.

    Using str inheritance allows JSON serialization as plain strings.
    """

    # General errors
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # Resource errors
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    CONFIG_INVALID = "CONFIG_INVALID"
    STUDY_NOT_FOUND = "STUDY_NOT_FOUND"
    IDEA_NOT_FOUND = "IDEA_NOT_FOUND"

    # Operation errors
    INSUFFICIENT_RUNS = "INSUFFICIENT_RUNS"
    STUDY_INCOMPLETE = "STUDY_INCOMPLETE"
    NO_BEST_PARAMS = "NO_BEST_PARAMS"
    OPERATION_FAILED = "OPERATION_FAILED"
    POLICY_VIOLATION = "POLICY_VIOLATION"

    # API/External errors
    API_ERROR = "API_ERROR"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    NETWORK_ERROR = "NETWORK_ERROR"
    RATE_LIMITED = "RATE_LIMITED"

    # Input errors
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    MISSING_ARGUMENT = "MISSING_ARGUMENT"


@dataclass
class CliError:
    """Structured error for CLI responses.

    Attributes:
        code: Machine-readable error code from CliErrorCode
        message: Human-readable error description
        details: Additional context (e.g., invalid field name, run_id)
    """

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result

    @classmethod
    def from_code(
        cls,
        code: CliErrorCode,
        message: str,
        **details: Any,
    ) -> CliError:
        """Create error from error code enum."""
        return cls(code=code.value, message=message, details=details)


@dataclass
class CliResponse:
    """Standard response envelope for CLI commands.

    All commands in JSON mode return this structure:

    Success example:
        {
            "success": true,
            "exit_code": 0,
            "command": "optimize list",
            "data": {"runs": [...]},
            "errors": [],
            "warnings": [],
            "metadata": {"timestamp": "...", "was_noop": false, "version": "1.0"}
        }

    Error example:
        {
            "success": false,
            "exit_code": 1,
            "command": "optimize view",
            "data": null,
            "errors": [{"code": "RUN_NOT_FOUND", "message": "..."}],
            "warnings": [],
            "metadata": {...}
        }
    """

    success: bool
    command: str
    data: Any = None
    errors: list[CliError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exit_code: int = 0
    was_noop: bool = False

    # Internal - set automatically
    _timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Set exit_code based on success if not explicitly set."""
        if not self.success and self.exit_code == 0:
            self.exit_code = 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "command": self.command,
            "data": self.data,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": self.warnings,
            "metadata": {
                "timestamp": self._timestamp.isoformat(),
                "was_noop": self.was_noop,
                "version": "1.0",
            },
        }

    def to_json(self, compact: bool = False) -> str:
        """Serialize to JSON string.

        Args:
            compact: If True, output without indentation

        Returns:
            JSON string representation
        """
        indent = None if compact else 2
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def add_warning(self, message: str) -> CliResponse:
        """Add a warning message (fluent API)."""
        self.warnings.append(message)
        return self

    def add_error(self, error: CliError) -> CliResponse:
        """Add an error (fluent API)."""
        self.errors.append(error)
        self.success = False
        self.exit_code = 1
        return self

    @classmethod
    def success_response(
        cls,
        command: str,
        data: Any = None,
        warnings: list[str] | None = None,
        was_noop: bool = False,
    ) -> CliResponse:
        """Create a success response.

        Args:
            command: Command name (e.g., "optimize list")
            data: Command result data
            warnings: Optional warning messages
            was_noop: Whether this was a no-op (e.g., dry-run)
        """
        return cls(
            success=True,
            command=command,
            data=data,
            warnings=warnings or [],
            exit_code=0,
            was_noop=was_noop,
        )

    @classmethod
    def error_response(
        cls,
        command: str,
        code: CliErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> CliResponse:
        """Create an error response.

        Args:
            command: Command name
            code: Error code
            message: Human-readable error message
            details: Additional error context
            warnings: Any warnings accumulated before the error
        """
        error = CliError.from_code(code, message, **(details or {}))
        return cls(
            success=False,
            command=command,
            data=None,
            errors=[error],
            warnings=warnings or [],
            exit_code=1,
        )


@dataclass(frozen=True, slots=True)
class RawCliOutput:
    """Command output that must be written exactly as provided."""

    content: str
    exit_code: int = 0


def format_response(response: CliResponse, output_format: str = "text") -> str:
    """Format a CLI response for output.

    Args:
        response: The CLI response to format
        output_format: "json" for machine-readable, "text" for human-readable

    Returns:
        Formatted string output (or None if printed directly via rich)
    """
    if output_format == "json":
        return response.to_json()

    # Text format - use rich for pretty printing
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax

    console = Console()

    if response.success:
        if response.data is None:
            return "Operation completed successfully."

        if isinstance(response.data, str):
            return response.data

        # For complex data, pretty print as JSON syntax
        json_str = json.dumps(response.data, indent=2, default=str)
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)
        with console.capture() as capture:
            console.print(syntax)
        return capture.get()
    else:
        # Format errors for text output
        if isinstance(response.data, str):
            return response.data

        lines = []
        for error in response.errors:
            lines.append(f"[bold red]Error [{error.code}]:[/bold red] {error.message}")
            if error.details:
                for key, value in error.details.items():
                    lines.append(f"  [dim]{key}: {value}[/dim]")

        error_text = "\n".join(lines)
        with console.capture() as capture:
            console.print(Panel(error_text, title="Command Failed", border_style="red"))
        return capture.get()
