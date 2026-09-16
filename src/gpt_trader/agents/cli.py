#!/usr/bin/env python3
"""CLI entry points for agent tools.

Wraps scripts/agents/*.py to expose as `uv run` commands.
Each function corresponds to an entry point in pyproject.toml.

Usage:
    uv run agent-naming --strict --quiet
    uv run agent-pr-ready --format markdown
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _get_scripts_dir() -> Path:
    """Get the scripts/agents directory path."""
    # Navigate from src/gpt_trader/agents/cli.py to project root
    return Path(__file__).parent.parent.parent.parent / "scripts" / "agents"


def _run_script(script_name: str) -> int:
    """Run a script from scripts/agents/ with forwarded args.

    Args:
        script_name: Name of the script file (e.g., "pr_readiness.py")

    Returns:
        Exit code from the script
    """
    scripts_dir = _get_scripts_dir()
    script = scripts_dir / script_name

    if not script.exists():
        print(f"Error: Script not found: {script}", file=sys.stderr)
        return 1

    # Run from project root for consistent path resolution
    project_root = scripts_dir.parent.parent
    result = subprocess.run(
        [sys.executable, str(script)] + sys.argv[1:],
        cwd=project_root,
    )
    return result.returncode


def naming() -> int:
    """Check naming standards.

    Entry point: agent-naming

    Scans for naming convention violations.

    Examples:
        uv run agent-naming                   # Full scan
        uv run agent-naming --strict          # Fail on violations
        uv run agent-naming --quiet           # Suppress stdout
    """
    return _run_script("naming_inventory.py")


def pr_ready() -> int:
    """Reconcile a PR's real mergeability against green CI (transparency, not a gate).

    Entry point: agent-pr-ready

    Surfaces what "checks are green" hides: required-check state, mergeStateStatus,
    unresolved review threads (with severity), and branch-protection drift.
    Always exits 0 by default; pass --exit-on-not-ready for an opt-in advisory gate.

    Examples:
        uv run agent-pr-ready                      # auto-detect PR for current branch
        uv run agent-pr-ready --pr 1056
        uv run agent-pr-ready --format markdown    # receipt for the PR body
        uv run agent-pr-ready --format json
        uv run agent-pr-ready --no-github          # skip gh; local-only report
        uv run agent-pr-ready --exit-on-not-ready  # opt-in advisory gate
    """
    return _run_script("pr_readiness.py")
