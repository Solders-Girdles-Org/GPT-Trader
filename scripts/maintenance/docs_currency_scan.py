#!/usr/bin/env python3
"""Extract and verify named references in docs/ against the live repository."""

from __future__ import annotations

import argparse
import importlib
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

Status = Literal["ok", "missing", "stale", "uncertain"]

PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("command", "uv run", re.compile(r"`(uv run [^`]+)`")),
    ("command", "python -m", re.compile(r"`(python -m [^`]+)`")),
    ("command", "python scripts", re.compile(r"`(python scripts/[^`]+)`")),
    ("command", "make", re.compile(r"`(make [a-z][a-z0-9_-]*)`")),
    (
        "path",
        "repo path",
        re.compile(
            r"`((?:src|config|tests|var|scripts|runtime_data|deploy|review_artifacts)/[^`\s)]+)`"
        ),
    ),
    (
        "path",
        "bare path",
        re.compile(
            r"(?<![/\w])((?:src|config|tests|var|scripts|runtime_data|deploy)/[a-zA-Z0-9_./\-*]+)"
        ),
    ),
    ("env_var", "assignment", re.compile(r"`([A-Z][A-Z0-9_]{2,})`")),
    (
        "env_var",
        "assignment",
        re.compile(r"\b([A-Z][A-Z0-9_]{2,})=(?:" + r"[0-9a-zA-Z._/-]+" + r")\b"),
    ),
    ("cli_flag", "flag", re.compile(r"`(-(?:-[a-z][a-z0-9-]+|[a-z]))`")),
    ("cli_flag", "flag", re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]{2,})(?=\s|$|[,\)])")),
    ("module", "gpt_trader", re.compile(r"`(gpt_trader\.[a-zA-Z0-9_.]+)`")),
    ("module", "gpt_trader", re.compile(r"\b(gpt_trader\.[a-zA-Z0-9_.]+)\b")),
    ("script", "scripts/", re.compile(r"`(scripts/[a-zA-Z0-9_./\-]+\.py)`")),
    ("script", "scripts/", re.compile(r"(scripts/[a-zA-Z0-9_./\-]+\.py)")),
]

SKIP_ENV = {
    "HTTP",
    "HTTPS",
    "API",
    "CLI",
    "TUI",
    "DI",
    "CFM",
    "INTX",
    "USD",
    "BTC",
    "ETH",
    "UTC",
    "JSON",
    "YAML",
    "CSV",
    "XLSX",
    "PR",
    "CI",
    "CD",
    "OK",
    "MISSING",
    "EOF",
    "README",
    "AGENTS",
    "CONTRIBUTING",
    "MOCK",
    "DRY",
    "RUN",
    "MODE",
    "MODES",
    "TYPE",
    "STATUS",
    "NOTES",
    "DATE",
    "VAR",
    "PATH",
    "SRC",
    "CONFIG",
    "TESTS",
    "VARNAME",
    "SCRATCH",
    "N",
    "M",
    "E2E",
    "MVP",
    "CFG",
    "UPPER_SNAKE_CASE",
    "KEBAB",
    "CASE",
    "SNAKE",
    "DEFAULT",
    "CSS",
    "HTML",
    "SQL",
    "URL",
    "UUID",
    "OTEL",
    "OTLP",
}
SKIP_FLAGS = {"--check", "--fix", "--help", "--version", "--verbose", "--quiet", "--strict"}
THIRD_PARTY_FLAG_PARENTS = (
    "pytest",
    "pre-commit",
    "gh",
    "docker",
    "pip-audit",
    "bandit",
    "black",
    "ruff",
    "mypy",
)
DEPRECATED_MARKERS = (
    "orchestration",
    "run_spot_profile",
    "sweep_strategies",
    "test_removed_aliases",
)
# Docs whose purpose is to catalog removals: naming a removed module, path, or
# env var here is correct guidance, not drift, so every such reference is treated
# as expected rather than missing/stale.
# Root-level markdown that must stay current alongside docs/ (they carry the
# same class of command/path/module references).
ROOT_DOC_FILES = ("README.md", "AGENTS.md", "CONTRIBUTING.md")
REMOVAL_REGISTRY_DOCS = ("DEPRECATIONS.md",)
# Docs that carry an old->new migration table for known-removed identifiers. Only
# references matching a DEPRECATED_MARKER are exempted here, so unrelated drift in
# these docs is still reported.
MIGRATION_GUIDANCE_DOCS = ("ARCHITECTURE.md",)
# Narrow suppressions for known false-positive missing/stale findings the scanner
# cannot classify from context alone live in config/agents/docs_currency_suppressions.yaml
# (keyed by doc + item, each with a one-line reason). Self-policing: the scanner
# fails if an entry no longer matches a missing/stale finding, so suppressions
# cannot rot silently.
_SUPPRESSIONS_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "agents" / "docs_currency_suppressions.yaml"
)


def _load_suppressions(path: Path = _SUPPRESSIONS_PATH) -> dict[tuple[str, str], str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = payload.get("suppressions") or []
    loaded: dict[tuple[str, str], str] = {}
    for entry in entries:
        doc = str(entry["doc"])
        item = str(entry["item"])
        reason = str(entry["reason"])
        key = (doc, item)
        if key in loaded:
            raise ValueError(f"duplicate suppression entry: {key}")
        loaded[key] = reason
    return loaded


CURRENCY_SUPPRESSIONS: dict[tuple[str, str], str] = _load_suppressions()

REPO_ROOT = Path(__file__).resolve().parents[2]


# Repo files that enumerate documented identifiers as data (this scanner's own
# suppression/marker tables and its test fixtures) rather than using them, so
# grep-based verification must ignore them.
_GREP_EXCLUDE_FILES = (
    "scripts/maintenance/docs_currency_scan.py",
    "tests/unit/scripts/test_docs_currency_scan.py",
    "tests/integration/scripts/test_docs_currency_scan.py",
)


def is_suppressed(source_doc: str, item: str) -> bool:
    return (source_doc, item) in CURRENCY_SUPPRESSIONS


def _is_removal_registry_doc(source_doc: str) -> bool:
    return any(source_doc.endswith(name) for name in REMOVAL_REGISTRY_DOCS)


def _is_documented_removal(item: str, source_doc: str) -> bool:
    """True when ``item`` is referenced by a doc that legitimately documents a
    removal or migration, so a missing/stale verdict would be a false positive."""
    if _is_removal_registry_doc(source_doc):
        return True
    if any(source_doc.endswith(name) for name in MIGRATION_GUIDANCE_DOCS):
        return any(marker in item for marker in DEPRECATED_MARKERS)
    return False


@dataclass
class ExtractedItem:
    source_doc: str
    category: str
    item: str
    item_type: str


@dataclass
class VerificationResult:
    status: Status
    method: str
    notes: str = ""


@dataclass
class ScanState:
    repo_root: Path
    pyproject_text: str = ""
    makefile_text: str = ""
    env_template_text: str = ""
    cli_help: str = ""
    local_ci_help: str = ""
    agent_help: dict[str, str] = field(default_factory=dict)
    make_targets: set[str] = field(default_factory=set)
    make_vars: set[str] = field(default_factory=set)
    project_scripts: set[str] = field(default_factory=set)
    source_cache: dict[str, str] = field(default_factory=dict)


def load_state(repo_root: Path, *, fetch_help: bool = True) -> ScanState:
    state = ScanState(repo_root=repo_root)
    pyproject = repo_root / "pyproject.toml"
    makefile = repo_root / "Makefile"
    env_template = repo_root / "config/environments/.env.template"
    state.pyproject_text = pyproject.read_text() if pyproject.exists() else ""
    state.makefile_text = makefile.read_text() if makefile.exists() else ""
    state.env_template_text = env_template.read_text() if env_template.exists() else ""

    in_scripts = False
    for line in state.pyproject_text.splitlines():
        if line.strip() == "[project.scripts]":
            in_scripts = True
            continue
        if in_scripts:
            if line.startswith("["):
                break
            match = re.match(r"^(\S+)\s*=", line)
            if match:
                state.project_scripts.add(match.group(1))

    for line in state.makefile_text.splitlines():
        target_match = re.match(r"^([a-z][a-z0-9_-]*):", line)
        if target_match:
            state.make_targets.add(target_match.group(1))
        var_match = re.match(r"^([A-Z][A-Z0-9_]+)\?*=", line)
        if var_match:
            state.make_vars.add(var_match.group(1))

    if not fetch_help:
        return state

    for cmd, attr in [("gpt-trader", "cli_help"), ("local-ci", "local_ci_help")]:
        try:
            result = subprocess.run(
                ["uv", "run", cmd, "--help"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=60,
            )
            setattr(state, attr, (result.stdout or "") + (result.stderr or ""))
        except Exception as exc:
            setattr(state, attr, f"HELP_UNAVAILABLE: {exc}")

    for script in sorted(state.project_scripts):
        if script.startswith("agent-"):
            try:
                result = subprocess.run(
                    ["uv", "run", script, "--help"],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                state.agent_help[script] = (result.stdout or "") + (result.stderr or "")
            except Exception:
                pass

    return state


def normalize_item(category: str, raw: str) -> str | None:
    item = raw.strip().rstrip(".,;:)")
    if not item:
        return None
    if category == "env_var":
        if "=" in item:
            item = item.split("=", 1)[0]
        if item in SKIP_ENV or len(item) < 3:
            return None
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", item):
            return None
    if category == "cli_flag" and item in SKIP_FLAGS:
        return None
    if category in {"path", "script"}:
        item = item.replace("//", "/")
        if any(item.endswith(suffix) for suffix in (".md", ".png", ".jpg", ".svg")):
            return None
        if "..." in item:
            return None
    if category == "command" and ("..." in item or "<" in item):
        return None
    if category == "module":
        item = item.rstrip(".")
    return item


def extract_from_doc(doc_path: Path, repo_root: Path) -> list[ExtractedItem]:
    rel = str(doc_path.relative_to(repo_root))
    text = doc_path.read_text(encoding="utf-8", errors="replace")
    items: list[ExtractedItem] = []
    seen: set[tuple[str, str, str]] = set()

    for category, item_type, pattern in PATTERNS:
        for match in pattern.finditer(text):
            norm = normalize_item(category, match.group(1))
            if not norm:
                continue
            key = (rel, category, norm)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                ExtractedItem(
                    source_doc=rel,
                    category=category,
                    item=norm,
                    item_type=item_type,
                )
            )
    return items


def _grep_repo(state: ScanState, needle: str, *, suffixes: tuple[str, ...] = (".py",)) -> list[str]:
    hits: list[str] = []
    for root_name in ("src", "config", "scripts", "tests"):
        root = state.repo_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in suffixes:
                continue
            rel = str(path.relative_to(state.repo_root))
            # The scanner and its tests list documented identifiers (suppressions,
            # markers, fixtures) as data, not as real usages; counting them would
            # let a suppression entry silently reclassify its own finding.
            if rel in _GREP_EXCLUDE_FILES:
                continue
            cached = state.source_cache.get(rel)
            if cached is None:
                try:
                    cached = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    cached = ""
                state.source_cache[rel] = cached
            if needle in cached:
                hits.append(rel)
    return hits


def _is_runtime_or_placeholder_path(item: str) -> bool:
    if item.startswith(
        ("runtime_data/", "var/data/", "var/results/", "var/snapshots/", "review_artifacts/")
    ):
        return True
    if "{" in item or "}" in item or "<" in item or ">" in item:
        return True
    if item.endswith("_") or item.endswith("_.py"):
        return True
    if "*" in item or "…" in item:
        return True
    return False


def verify_path(state: ScanState, item: str, source_doc: str = "") -> VerificationResult:
    if _is_runtime_or_placeholder_path(item):
        return VerificationResult(
            "uncertain",
            "runtime/placeholder path",
            "runtime or templated path; not expected on disk by default",
        )
    full = state.repo_root / item
    if full.exists():
        return VerificationResult("ok", f"test -e {item}", "exists on disk")
    if _is_documented_removal(item, source_doc):
        return VerificationResult("ok", f"test -e {item}", "documented removal/migration reference")
    if "DEPRECATIONS" in item or any(marker in item for marker in DEPRECATED_MARKERS):
        return VerificationResult("stale", f"test -e {item}", "references removed artifact")
    return VerificationResult("missing", f"test -e {item}", "path not found")


def _try_import(state: ScanState, dotted: str) -> VerificationResult:
    try:
        result = subprocess.run(
            ["uv", "run", "python", "-c", f"import {dotted}; print('OK')"],
            cwd=state.repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and "OK" in result.stdout:
            return VerificationResult("ok", f"import {dotted}", "import succeeded")
        return VerificationResult(
            "missing",
            f"import {dotted}",
            (result.stderr or result.stdout).strip()[:200],
        )
    except Exception as exc:
        return VerificationResult("uncertain", f"import {dotted}", str(exc))


def verify_module(state: ScanState, item: str, source_doc: str) -> VerificationResult:
    # Existence-first (matching verify_path/verify_env_var): resolve the symbol
    # before honoring removal/migration exemptions, so a migration-path replacement
    # target named in a removal registry is still verified and a later rename or
    # deletion is caught rather than blanket-exempted.
    result = _resolve_module_symbol(state, item)
    if result.status != "missing":
        return result
    # Unresolved: a module named in a removal registry / migration guide is expected
    # (documented removal) even without a DEPRECATED_MARKERS substring; otherwise a
    # marker-matched name is stale drift.
    if _is_documented_removal(item, source_doc):
        return VerificationResult("ok", "documented removal", "removal/migration guidance")
    if any(marker in item for marker in DEPRECATED_MARKERS):
        return VerificationResult("stale", "deprecated module", "references removed module")
    return result


def _resolve_module_symbol(state: ScanState, item: str) -> VerificationResult:
    parts = item.split(".")
    if parts[-1][0].islower() and len(parts) >= 3:
        module_path = ".".join(parts[:-1])
        attr_name = parts[-1]
        try:
            module = importlib.import_module(module_path)
            if hasattr(module, attr_name):
                return VerificationResult("ok", f"getattr {item}", "callable/attr exists")
        except Exception:
            pass
        module_rel = Path(*module_path.split("."))
        for py_path in (
            state.repo_root / "src" / module_rel.with_suffix(".py"),
            state.repo_root / "src" / module_rel / "__init__.py",
        ):
            if py_path.exists() and attr_name in py_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                return VerificationResult(
                    "uncertain", "source grep", f"function {attr_name} in source"
                )
    if parts[-1][0].isupper():
        module_path = ".".join(parts[:-1])
        class_name = parts[-1]
        try:
            module = importlib.import_module(module_path)
            if hasattr(module, class_name):
                return VerificationResult("ok", f"getattr {item}", "class exists")
        except Exception:
            pass
        try:
            proc = subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    "-c",
                    f"from {module_path} import {class_name}; print('OK')",
                ],
                cwd=state.repo_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                return VerificationResult("ok", f"from-import {item}", "class import succeeded")
        except Exception as exc:
            return VerificationResult("uncertain", f"from-import {item}", str(exc))
        module_rel = Path(*module_path.split("."))
        if any(
            py_path.exists()
            for py_path in (
                state.repo_root / "src" / module_rel.with_suffix(".py"),
                state.repo_root / "src" / module_rel / "__init__.py",
            )
        ):
            return VerificationResult(
                "uncertain", "source file", f"class {class_name} not importable"
            )
        return VerificationResult("missing", f"from-import {item}", "module/class not found")

    result = _try_import(state, item)
    if result.status != "missing":
        return result
    py_parts = item.split(".")
    py_path = state.repo_root / "src" / "/".join(py_parts[:-1]) / f"{py_parts[-1]}.py"
    init_path = state.repo_root / "src" / "/".join(py_parts) / "__init__.py"
    if py_path.exists() or init_path.exists():
        return VerificationResult("uncertain", f"import {item}", "source exists; import failed")
    return result


def verify_env_var(state: ScanState, item: str, source_doc: str = "") -> VerificationResult:
    if item in state.make_vars:
        return VerificationResult("ok", "Makefile var", f"Makefile variable {item}")
    in_template = bool(re.search(rf"^{re.escape(item)}=", state.env_template_text, re.M))
    refs = _grep_repo(state, item)
    if in_template and refs:
        return VerificationResult("ok", "grep template+source", f"template + {len(refs)} refs")
    if in_template:
        return VerificationResult("ok", "grep template", "present in .env.template")
    if refs:
        return VerificationResult(
            "uncertain",
            "grep source only",
            f"not in .env.template; refs: {', '.join(refs[:3])}",
        )
    if _is_removal_registry_doc(source_doc):
        return VerificationResult("ok", "removal registry", "documented as a removed env var")
    return VerificationResult("missing", "grep template+source", "absent from template and source")


def verify_cli_flag(state: ScanState, item: str, source_doc: str) -> VerificationResult:
    helps = [("gpt-trader", state.cli_help), ("local-ci", state.local_ci_help)]
    helps.extend((name, text) for name, text in state.agent_help.items())
    found_in = [label for label, help_text in helps if item in help_text]
    if found_in:
        return VerificationResult("ok", "--help grep", f"found in: {', '.join(found_in)}")

    if source_doc.endswith("testing.md") and item in {
        "--collect-only",
        "--cov",
        "--pdb",
        "--all-files",
        "-n",
        "-m",
        "-k",
        "-q",
        "-v",
        "-x",
    }:
        return VerificationResult("ok", "pytest flag", "pytest CLI flag documented in testing.md")

    if "MONITORING_PLAYBOOK" in source_doc and item in {"--host", "--port", "--project-directory"}:
        return VerificationResult("ok", "docker compose flag", "docker compose CLI flag")

    if any(tool in source_doc or tool in item for tool in THIRD_PARTY_FLAG_PARENTS):
        return VerificationResult("ok", "third-party tool flag", "external tool CLI flag")

    script_hits = _grep_repo(state, item)
    if script_hits:
        return VerificationResult("uncertain", "scripts grep", f"in {script_hits[0]}")
    if "gh pr" in source_doc or "project_review" in source_doc:
        return VerificationResult("uncertain", "gh CLI flag", "GitHub CLI flag; not gpt-trader")
    if re.fullmatch(r"-[a-z]", item):
        return VerificationResult(
            "uncertain", "short flag", "single-dash flag; likely pytest/third-party"
        )
    if not any(
        help_text and not help_text.startswith("HELP_UNAVAILABLE") for _, help_text in helps
    ):
        return VerificationResult(
            "uncertain", "--help unavailable", "CLI help was skipped or unavailable"
        )
    return VerificationResult("missing", "--help grep", "not in CLI help or source")


def verify_command(state: ScanState, item: str) -> VerificationResult:
    if item.startswith("make "):
        target = item.split()[1]
        if target in state.make_targets:
            return VerificationResult("ok", "Makefile target", f"target '{target}' exists")
        return VerificationResult(
            "missing", "Makefile target", f"target '{target}' not in Makefile"
        )

    if item.startswith("python -m "):
        module = item.split()[2]
        module_rel = module.replace(".", "/")
        # `python -m X` runs a module file (`X.py`) or a package's `__main__.py`.
        # A package with only `__init__.py` is importable but NOT runnable via -m.
        main_py = state.repo_root / "src" / module_rel / "__main__.py"
        module_file = state.repo_root / "src" / f"{module_rel}.py"
        if main_py.exists() or module_file.exists():
            return VerificationResult("ok", "python -m module", module)
        if (state.repo_root / "src" / module_rel / "__init__.py").exists():
            return VerificationResult(
                "missing",
                "python -m module",
                f"{module} is a package without __main__.py (not runnable via -m)",
            )
        return _try_import(state, module)

    if item.startswith("uv run "):
        rest = item[len("uv run ") :].strip()
        if rest.startswith("python -m "):
            module = rest.split()[2]
            return verify_command(state, f"python -m {module}")
        if rest.startswith("python "):
            script = rest[len("python ") :].split()[0]
            if (state.repo_root / script).exists():
                return VerificationResult("ok", "script exists", script)
            return VerificationResult("missing", "script exists", f"{script} not found")
        entry = rest.split()[0]
        if entry in state.project_scripts:
            return VerificationResult("ok", "pyproject.scripts", entry)
        if entry in ("pytest", "ruff", "black", "mypy", "pre-commit", "pip-audit", "bandit"):
            return VerificationResult("ok", "dev tool", entry)
        if (state.repo_root / entry).exists():
            return VerificationResult("ok", "path exists", entry)
        return VerificationResult(
            "uncertain", "pyproject+path", f"entry '{entry}' not in project.scripts"
        )

    if item.startswith("python "):
        parts = item.split()
        if len(parts) >= 2 and (state.repo_root / parts[1]).exists():
            return VerificationResult("ok", "script exists", parts[1])
        return VerificationResult("missing", "script exists", item)

    return VerificationResult("uncertain", "manual", f"unparsed command: {item}")


def verify_item(state: ScanState, ext: ExtractedItem) -> VerificationResult:
    if ext.category in {"path", "script"}:
        return verify_path(state, ext.item, ext.source_doc)
    if ext.category == "module":
        return verify_module(state, ext.item, ext.source_doc)
    if ext.category == "env_var":
        return verify_env_var(state, ext.item, ext.source_doc)
    if ext.category == "cli_flag":
        return verify_cli_flag(state, ext.item, ext.source_doc)
    if ext.category == "command":
        return verify_command(state, ext.item)
    return VerificationResult("uncertain", "unknown category", "")


def scan_docs(
    repo_root: Path, *, fetch_help: bool = True
) -> tuple[list[Path], list[ExtractedItem], list[tuple[ExtractedItem, VerificationResult]]]:
    root_docs = [repo_root / name for name in ROOT_DOC_FILES if (repo_root / name).exists()]
    doc_files = sorted([*root_docs, *(repo_root / "docs").rglob("*.md")])
    state = load_state(repo_root, fetch_help=fetch_help)
    extracted: list[ExtractedItem] = []
    for doc in doc_files:
        extracted.extend(extract_from_doc(doc, repo_root))
    discrepancies: list[tuple[ExtractedItem, VerificationResult]] = []
    for item in extracted:
        result = verify_item(state, item)
        if result.status != "ok":
            discrepancies.append((item, result))
    return doc_files, extracted, discrepancies


def render_report(
    *,
    doc_files: list[Path],
    extracted: list[ExtractedItem],
    discrepancies: list[tuple[ExtractedItem, VerificationResult]],
    repo_root: Path,
) -> str:
    ok_count = len(extracted) - len(discrepancies)
    missing = [d for d in discrepancies if d[1].status == "missing"]
    stale = [d for d in discrepancies if d[1].status == "stale"]
    uncertain = [d for d in discrepancies if d[1].status == "uncertain"]
    lines = [
        "# Docs-to-Code Currency Scan Report",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Repo: {repo_root}",
        f"Docs processed: {len(doc_files)}",
        f"Items extracted: {len(extracted)}",
        f"Items verified OK: {ok_count}",
        f"Discrepancies: {len(discrepancies)} (missing={len(missing)}, stale={len(stale)}, uncertain={len(uncertain)})",
        "",
        "## Summary",
        "",
        f"- **Docs scanned**: {len(doc_files)} markdown files under `docs/`",
        f"- **References extracted**: {len(extracted)} named items",
        f"- **Verified OK**: {ok_count}",
        f"- **Missing**: {len(missing)}",
        f"- **Stale**: {len(stale)}",
        f"- **Uncertain**: {len(uncertain)}",
        "",
    ]
    if not discrepancies:
        lines.append(
            "**No discrepancies found.** All extracted named references verified successfully."
        )
        lines.append("")
    else:
        lines.extend(
            [
                "## Discrepancy Table",
                "",
                "| Source Doc | Category | Extracted Item | Type | Verification | Status | Notes |",
                "|------------|----------|----------------|------|--------------|--------|-------|",
            ]
        )
        for ext, result in sorted(
            discrepancies, key=lambda row: (row[1].status, row[0].source_doc, row[0].item)
        ):
            notes = " ".join(result.notes.split()).replace("|", "\\|")[:120]
            lines.append(
                f"| {ext.source_doc} | {ext.category} | `{ext.item}` | {ext.item_type} | {result.method} | **{result.status}** | {notes} |"
            )
        lines.append("")

    lines.extend(["## Per-Doc Extraction Counts", ""])
    by_doc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for entry in extracted:
        by_doc[entry.source_doc][entry.category] += 1
    lines.append("| Doc | commands | paths | env_vars | cli_flags | modules | scripts | total |")
    lines.append("|-----|----------|-------|----------|-----------|---------|---------|-------|")
    for doc in sorted(by_doc):
        counts = by_doc[doc]
        total = sum(counts.values())
        lines.append(
            f"| {doc} | {counts.get('command', 0)} | {counts.get('path', 0)} | {counts.get('env_var', 0)} | {counts.get('cli_flag', 0)} | {counts.get('module', 0)} | {counts.get('script', 0)} | {total} |"
        )
    return "\n".join(lines) + "\n"


Discrepancy = tuple[ExtractedItem, VerificationResult]


def _parse_fail_on(value: str) -> set[Status]:
    """Parse a comma-separated ``--fail-on`` list into a set of statuses."""
    valid: set[str] = {"ok", "missing", "stale", "uncertain"}
    statuses = {token.strip() for token in value.split(",") if token.strip()}
    unknown = statuses - valid
    if unknown:
        raise SystemExit(f"--fail-on: unknown status(es): {', '.join(sorted(unknown))}")
    return statuses  # type: ignore[return-value]


def gating_findings(discrepancies: list[Discrepancy], fail_on: set[Status]) -> list[Discrepancy]:
    """Findings whose status is in ``fail_on`` and are not suppressed."""
    return [
        (ext, result)
        for ext, result in discrepancies
        if result.status in fail_on and not is_suppressed(ext.source_doc, ext.item)
    ]


def unused_suppressions(discrepancies: list[Discrepancy]) -> set[tuple[str, str]]:
    """Suppression keys that no longer match any non-``ok`` finding.

    Used by the scanner tests to keep ``CURRENCY_SUPPRESSIONS`` honest: once a
    suppressed reference is fixed or removed from its doc, its entry must be
    dropped. Any non-``ok`` status counts as live so a suppression does not flap
    stale purely because ``--help`` availability shifts a finding between
    ``missing`` and ``uncertain``.
    """
    live = {(ext.source_doc, ext.item) for ext, _ in discrepancies}
    return set(CURRENCY_SUPPRESSIONS) - live


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan docs/ references against repository state.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, help="Optional path to write the full report.")
    parser.add_argument("--raw-extracts", type=Path)
    parser.add_argument("--skip-help", action="store_true")
    parser.add_argument(
        "--fail-on",
        default="",
        help=(
            "Comma-separated statuses that cause a nonzero exit (e.g. 'missing,stale'). "
            "Suppressed findings (CURRENCY_SUPPRESSIONS) never gate; omit to report only."
        ),
    )
    args = parser.parse_args(argv)
    fail_on = _parse_fail_on(args.fail_on)

    repo_root = args.repo_root.resolve()
    doc_files, extracted, discrepancies = scan_docs(repo_root, fetch_help=not args.skip_help)
    report = render_report(
        doc_files=doc_files,
        extracted=extracted,
        discrepancies=discrepancies,
        repo_root=repo_root,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")

    if args.raw_extracts:
        args.raw_extracts.parent.mkdir(parents=True, exist_ok=True)
        by_doc: dict[str, list[ExtractedItem]] = defaultdict(list)
        for entry in extracted:
            by_doc[entry.source_doc].append(entry)
        chunks = [f"Total docs: {len(doc_files)}", f"Total extracted items: {len(extracted)}", ""]
        for doc in sorted(by_doc):
            chunks.append(f"=== {doc} ({len(by_doc[doc])} items) ===")
            for entry in sorted(by_doc[doc], key=lambda row: (row.category, row.item)):
                chunks.append(f"  [{entry.category}] {entry.item}")
            chunks.append("")
        args.raw_extracts.write_text("\n".join(chunks), encoding="utf-8")

    if args.output:
        print(f"Report written to {args.output}")
    suppressed = sum(
        1
        for ext, result in discrepancies
        if result.status in {"missing", "stale"} and is_suppressed(ext.source_doc, ext.item)
    )
    print(
        f"Docs: {len(doc_files)}, Extracted: {len(extracted)}, "
        f"Discrepancies: {len(discrepancies)} (suppressed missing/stale: {suppressed})"
    )

    if fail_on:
        gating = gating_findings(discrepancies, fail_on)
        orphaned_suppressions = unused_suppressions(discrepancies)
        if gating:
            print(f"\nFAIL: {len(gating)} unsuppressed {'/'.join(sorted(fail_on))} finding(s):")
            for ext, result in sorted(
                gating, key=lambda row: (row[1].status, row[0].source_doc, row[0].item)
            ):
                print(f"  [{result.status}] {ext.source_doc} :: `{ext.item}` — {result.notes}")
            print(
                "Fix the reference in the owning doc, or — if it is a genuine false positive — "
                "add a justified entry to "
                "config/agents/docs_currency_suppressions.yaml."
            )
        if orphaned_suppressions:
            print(
                f"\nFAIL: {len(orphaned_suppressions)} unused suppression entries "
                "(config/agents/docs_currency_suppressions.yaml):"
            )
            for source_doc, item in sorted(orphaned_suppressions):
                print(f"  {source_doc} :: `{item}`")
            print(
                "Remove stale suppression entries after the underlying docs reference is fixed "
                "or removed."
            )
        if gating or orphaned_suppressions:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
