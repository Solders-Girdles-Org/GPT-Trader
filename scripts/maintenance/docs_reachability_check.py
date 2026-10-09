#!/usr/bin/env python3
"""
Ensure all docs are reachable from docs/README.md and carry required metadata.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "docs"
DOCS_ENTRYPOINT = DOCS_ROOT / "README.md"

LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:")
ALLOWED_STATUSES = {
    "current",
    "draft",
    "deprecated",
    "superseded",
    # Decision-record lifecycle (docs/decisions/*.md); see docs/decisions/README.md.
    "proposed",
    "accepted",
    "rejected",
}
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_KEYS = ("last-updated", "last-reviewed", "last-verified")
METADATA_TEMPLATE = "---\nstatus: current\n---"

SECTION_FALLBACKS = (
    "Quick Links",
    "Additional Resources",
    "Core Documentation",
    "Start Here",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check docs reachability from docs/README.md")
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to scan (default: repo root).",
    )
    return parser.parse_args()


def iter_links(content: str) -> list[str]:
    links: list[str] = []
    for target in LINK_PATTERN.findall(content):
        target = target.strip()
        if not target:
            continue
        if target.startswith(EXTERNAL_PREFIXES):
            continue
        if target.startswith("#"):
            continue
        target = target.split()[0].strip("'\"")
        target = unquote(target)
        target = target.split("#", maxsplit=1)[0]
        if target:
            links.append(target)
    return links


def resolve_target(source: Path, target: str, root: Path) -> Path:
    if target.startswith("/"):
        return (root / target.lstrip("/")).resolve()
    return (source.parent / target).resolve()


def _find_metadata_block(lines: list[str], *, scan_limit: int = 60) -> tuple[int, int] | None:
    limit = min(len(lines), scan_limit)
    for i in range(limit):
        if lines[i].strip() != "---":
            continue
        for j in range(i + 1, min(i + 25, len(lines))):
            if lines[j].strip() == "---":
                return i, j
        return None
    return None


def has_required_metadata(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    bounds = _find_metadata_block(lines)
    if bounds is None:
        return False

    start, end = bounds
    block_lines = [line.strip() for line in lines[start + 1 : end] if line.strip()]
    status_value: str | None = None
    date_values: list[str] = []

    for line in block_lines:
        if line.startswith("status:"):
            status_value = line.split(":", maxsplit=1)[1].strip()
        for key in DATE_KEYS:
            if line.startswith(f"{key}:"):
                date_values.append(line.split(":", maxsplit=1)[1].strip())

    if status_value is None or status_value not in ALLOWED_STATUSES:
        return False
    # Date stamps are optional: git history is the source of truth for "when
    # changed". Any date key that IS present must be a valid ISO date.
    if any(not ISO_DATE_PATTERN.match(value) for value in date_values):
        return False
    return True


def load_readme_sections(readme_path: Path) -> set[str]:
    sections: set[str] = set()
    parent: str | None = None
    try:
        lines = readme_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return sections

    for line in lines:
        if line.startswith("## "):
            parent = line[3:].strip()
            if parent:
                sections.add(parent)
            continue
        if line.startswith("### "):
            child = line[4:].strip()
            if child:
                if parent:
                    sections.add(f"{parent} > {child}")
                else:
                    sections.add(child)
    return sections


def suggest_sections(path: Path, available_sections: set[str]) -> list[str]:
    if not available_sections:
        return []

    rel = path.as_posix().lower()
    name = path.name.lower()
    candidates: list[str] = []

    if "/agents/" in rel or "agent" in name:
        candidates.extend(["Quick Links", "Getting Help"])
    if "/architecture/" in rel or "architecture" in rel:
        candidates.append("Core Documentation > Architecture & Design")
    if any(
        keyword in rel
        for keyword in (
            "production",
            "readiness",
            "monitoring",
            "runbook",
            "observability",
            "reliability",
            "incident",
            "operations",
        )
    ):
        candidates.append("Core Documentation > Trading Operations")
    if any(keyword in rel for keyword in ("coinbase", "broker", "cfm", "intx")):
        candidates.append("Core Documentation > Coinbase Integration")
    if any(
        keyword in rel
        for keyword in (
            "test",
            "testing",
            "development",
            "guidelines",
            "naming",
            "security",
            "deprecation",
            "feature",
        )
    ):
        candidates.append("Core Documentation > Development")
    if any(keyword in rel for keyword in ("getting started", "getting_started", "intro")):
        candidates.append("Core Documentation > Getting Started")
    if any(keyword in rel for keyword in ("config", "environment", "profile", "flag")):
        candidates.append("Configuration")
    if any(keyword in rel for keyword in ("risk", "pnl", "changelog")):
        candidates.append("Additional Resources")

    suggestions: list[str] = []
    for candidate in candidates:
        if candidate in available_sections and candidate not in suggestions:
            suggestions.append(candidate)

    if suggestions:
        return suggestions[:3]

    for fallback in SECTION_FALLBACKS:
        if fallback in available_sections:
            suggestions.append(fallback)
        if len(suggestions) >= 2:
            break

    return suggestions


def collect_docs(docs_root: Path) -> tuple[list[Path], list[Path]]:
    """Return (markdown_docs, non_markdown_files) under docs/."""
    # Prefer tracked docs so local, gitignored/generated files don't break the check.
    try:
        completed = subprocess.run(
            ["git", "ls-files", "docs"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        markdown_docs = [path.resolve() for path in docs_root.rglob("*.md")]
        return markdown_docs, []

    markdown_docs: list[Path] = []
    non_markdown_files: list[Path] = []
    for rel in completed.stdout.splitlines():
        if not rel:
            continue
        path = (REPO_ROOT / rel).resolve()
        if not path.exists():
            continue
        if path.is_dir():
            continue
        if rel.startswith("docs/archive/"):
            print(
                "docs/archive/ is not allowed. Use git history for historical docs.",
                file=sys.stderr,
            )
            return [], [path]
        if path.suffix.lower() != ".md":
            non_markdown_files.append(path)
            continue
        markdown_docs.append(path)
    return markdown_docs, non_markdown_files


def walk_reachable(
    start: Path,
    docs_root: Path,
    repo_root: Path,
) -> set[Path]:
    reachable: set[Path] = set()
    queue = [start.resolve()]

    while queue:
        current = queue.pop()
        if current in reachable:
            continue
        reachable.add(current)

        try:
            content = current.read_text(encoding="utf-8")
        except OSError:
            continue

        for target in iter_links(content):
            resolved = resolve_target(current, target, repo_root)
            if resolved.is_dir():
                candidate = resolved / "README.md"
                if candidate.exists():
                    resolved = candidate.resolve()

            if resolved.suffix.lower() != ".md":
                continue
            if not resolved.exists():
                continue

            try:
                resolved.relative_to(docs_root)
            except ValueError:
                continue

            if resolved not in reachable:
                queue.append(resolved)

    return reachable


def main() -> int:
    args = parse_args()
    repo_root = args.root.resolve()
    docs_root = repo_root / "docs"
    entrypoint = docs_root / "README.md"

    if not entrypoint.exists():
        print(f"Missing docs entrypoint: {entrypoint}", file=sys.stderr)
        return 1

    docs, non_markdown_files = collect_docs(docs_root)
    if non_markdown_files:
        print("Non-markdown files are not allowed under docs/:")
        for path in sorted(non_markdown_files):
            try:
                rel = path.relative_to(repo_root)
            except ValueError:
                rel = path
            print(f"- {rel}")
        return 1

    reachable = walk_reachable(entrypoint, docs_root, repo_root)
    orphaned = sorted(set(docs) - reachable)

    if orphaned:
        sections = load_readme_sections(entrypoint)
        print("Docs not reachable from docs/README.md.")
        print(
            "Reachability rule: every markdown file under docs/ must be reachable by following links "
            "starting at docs/README.md (directly or via other reachable docs)."
        )
        print("\nOrphaned docs:")
        for path in orphaned:
            rel_path = path.relative_to(repo_root)
            suggestions = suggest_sections(path, sections)
            if suggestions:
                suggestions_text = "; ".join(suggestions)
                print(f"- {rel_path} (suggested section(s): {suggestions_text})")
            else:
                print(f"- {rel_path}")
        print(f"\nTotal orphaned: {len(orphaned)}")
        return 1

    missing_metadata = sorted([path for path in docs if not has_required_metadata(path)])
    if missing_metadata:
        date_keys = ", ".join(DATE_KEYS)
        allowed_statuses = ", ".join(sorted(ALLOWED_STATUSES))
        print(
            "Docs missing or invalid metadata block (required: status; "
            "optional: last-updated/last-reviewed/last-verified):"
        )
        for path in missing_metadata:
            print(f"- {path.relative_to(repo_root)}")
        print("\nMetadata requirements:")
        print(f"- Required: status (one of: {allowed_statuses})")
        print(f"- Optional date keys (validated if present): {date_keys}; format YYYY-MM-DD")
        print("\nCopy/paste template:")
        print(METADATA_TEMPLATE)
        print(f"\nTotal missing metadata: {len(missing_metadata)}")
        return 1

    print(f"✓ Docs reachability check passed ({len(docs)} files reachable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
