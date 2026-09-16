"""Scan the repository for naming anti-patterns.

Usage::

    python scripts/agents/naming_inventory.py \
        --summary docs/agents/naming_inventory.md \
        --json var/results/naming_inventory.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FALLBACK_PATTERNS = (
    "cfg",  # naming: allow
    "svc",  # naming: allow
    "mgr",  # naming: allow
    "util",  # naming: allow
    "utils",  # naming: allow
    "amt",  # naming: allow
    "calc",  # naming: allow
    "upd",  # naming: allow
)
FALLBACK_PATHS = ("src", "tests", "scripts", "config")
FALLBACK_SKIP_LINE_TOKEN = "# naming: allow"


def load_config() -> dict[str, Any]:
    config_path = REPO_ROOT / "config" / "agents" / "naming_patterns.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _coerce_sequence(value: Any, fallback: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return tuple(items) if items else tuple(fallback)
    return tuple(fallback)


def _coerce_token(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return fallback


_CONFIG = load_config()
DEFAULT_PATTERNS = _coerce_sequence(_CONFIG.get("banned_patterns"), FALLBACK_PATTERNS)
DEFAULT_PATHS = _coerce_sequence(_CONFIG.get("scan_paths"), FALLBACK_PATHS)
SKIP_PATHS = _coerce_sequence(_CONFIG.get("skip_paths"), ())
SKIP_LINE_TOKEN = _coerce_token(_CONFIG.get("skip_line_token"), FALLBACK_SKIP_LINE_TOKEN)


@dataclass(order=True)
class MatchRecord:
    path: Path
    line: int
    pattern: str
    context: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "path": self.path.as_posix(),
            "line": self.line,
            "pattern": self.pattern,
            "context": self.context,
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan for banned naming patterns.")
    parser.add_argument(
        "--patterns",
        default=",".join(DEFAULT_PATTERNS),
        help=(
            "Comma-separated list of patterns to search "
            "(defaults to config/agents/naming_patterns.yaml)."
        ),
    )
    parser.add_argument(
        "--paths",
        default=",".join(DEFAULT_PATHS),
        help=(
            "Comma-separated list of paths to scan "
            "(defaults to config/agents/naming_patterns.yaml)."
        ),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("docs/agents/naming_inventory.md"),
        help="Optional markdown summary output path.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("var/results/naming_inventory.json"),
        help="Optional JSON report output path.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout summary output.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any violations are found (for pre-commit hooks).",
    )
    return parser.parse_args(argv)


def compile_patterns(patterns: Sequence[str]) -> dict[str, re.Pattern[str]]:
    compiled: dict[str, re.Pattern[str]] = {}
    for pattern in patterns:
        trimmed = pattern.strip()
        if not trimmed:
            continue
        # Use custom boundaries so patterns match identifiers like foo_utils without  # naming: allow
        # requiring whitespace separation. For example, (?<![A-Za-z0-9])utils detects  # naming: allow
        # both `utils` and `foo_utils`, while still avoiding partial matches inside  # naming: allow
        # words such as `utilization`.  # naming: allow
        compiled[trimmed] = re.compile(rf"(?<![A-Za-z0-9]){re.escape(trimmed)}(?![A-Za-z0-9])")
    return compiled


def _is_under_skip_path(path: Path) -> bool:
    """Check if path is under a configured skip path."""
    if not SKIP_PATHS:
        return False
    try:
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        for skip_path in SKIP_PATHS:
            if relative_path.startswith(skip_path.rstrip("/") + "/") or relative_path == skip_path:
                return True
    except ValueError:
        pass  # Path not relative to REPO_ROOT
    return False


def iter_files(paths: Sequence[str]) -> Iterator[Path]:
    def should_skip(path: Path) -> bool:
        if any(part.endswith(".egg-info") for part in path.parts):
            return True
        if any(part.endswith(".dist-info") for part in path.parts):
            return True
        if any(part == "__pycache__" for part in path.parts):
            return True
        if path.suffix in {".pyc", ".pyo"}:
            return True
        # Skip markdown files - they're documentation and often reference existing filenames
        if path.suffix in {".md", ".rst"}:
            return True
        # Skip paths from config (e.g., tests/_triage contains manifests with existing paths)
        if _is_under_skip_path(path):
            return True
        return False

    for relative in paths:
        rel = relative.strip()
        if not rel:
            continue
        root = (REPO_ROOT / rel).resolve()
        if not root.exists():
            continue
        if root.is_file():
            if not should_skip(root):
                yield root
            continue
        for candidate in root.rglob("*"):
            if candidate.is_file() and not should_skip(candidate):
                yield candidate


def scan_file(path: Path, patterns: dict[str, re.Pattern[str]]) -> Iterable[MatchRecord]:
    matches: list[MatchRecord] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for idx, line in enumerate(handle, start=1):
                if SKIP_LINE_TOKEN in line:
                    continue
                for name, regex in patterns.items():
                    if regex.search(line):
                        matches.append(
                            MatchRecord(
                                path=path.relative_to(REPO_ROOT),
                                line=idx,
                                pattern=name,
                                context=line.rstrip(),
                            )
                        )
    except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive guard
        return []
    return matches


def scan(paths: Sequence[str], pattern_map: dict[str, re.Pattern[str]]) -> list[MatchRecord]:
    records: list[MatchRecord] = []
    for file_path in iter_files(paths):
        records.extend(scan_file(file_path, pattern_map))
    records.sort()
    return records


def ensure_parent(path: Path) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, records: Sequence[MatchRecord]) -> None:
    ensure_parent(path)
    payload = [rec.to_dict() for rec in records]
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def format_summary(records: Sequence[MatchRecord], patterns: Sequence[str]) -> str:
    lines: list[str] = []
    lines.append("# Naming Inventory Summary")
    lines.append("")
    lines.append(f"Total findings: {len(records)}")
    lines.append("")
    totals = Counter(rec.pattern for rec in records)
    by_pattern_and_subsystem: dict[str, Counter[str]] = defaultdict(Counter)
    by_pattern_and_path: dict[str, Counter[str]] = defaultdict(Counter)

    for rec in records:
        subsystem = rec.path.parts[0] if rec.path.parts else "."
        by_pattern_and_subsystem[rec.pattern][subsystem] += 1
        by_pattern_and_path[rec.pattern][rec.path.as_posix()] += 1

    for pattern in patterns:
        total = totals.get(pattern, 0)
        if total == 0:
            continue
        lines.append(f"## Pattern `{pattern}` – {total}")
        subsystem_counts = by_pattern_and_subsystem.get(pattern, Counter())
        for subsystem, count in subsystem_counts.most_common():
            lines.append(f"- **{subsystem}**: {count}")
        top_files = by_pattern_and_path.get(pattern, Counter())
        if top_files:
            lines.append("  - Top files:")
            for path, count in top_files.most_common(5):
                lines.append(f"    - `{path}` ({count})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_summary(path: Path, summary: str) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(summary)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    pattern_list = [p for p in args.patterns.split(",") if p.strip()]
    pattern_map = compile_patterns(pattern_list)
    if not pattern_map:
        raise SystemExit("No patterns supplied")

    paths = [p for p in args.paths.split(",") if p.strip()]
    records = scan(paths, pattern_map)

    if args.json:
        write_json(args.json, records)
    if args.summary:
        summary = format_summary(records, pattern_list)
        write_summary(args.summary, summary)

    if not args.quiet:
        if records:
            totals = Counter(rec.pattern for rec in records)
            print("Findings by pattern:")
            for pattern in pattern_list:
                count = totals.get(pattern, 0)
                if count:
                    print(f"  {pattern}: {count}")
            print(f"Saved JSON report to {args.json}")
            print(f"Saved summary to {args.summary}")
        else:
            print("No matches found for provided patterns.")

    # In strict mode, return non-zero exit code if violations found
    if args.strict and records:
        print(f"\n[!] Strict mode: {len(records)} naming violation(s) found. Commit blocked.")
        for rec in records:
            print(f"{rec.path}:{rec.line}: found '{rec.pattern}' in: {rec.context.strip()}")
        print("\nAdd '# naming: allow' to suppress specific lines, or fix the violations.")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
