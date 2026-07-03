#!/usr/bin/env python3
"""Import boundary guard for architecture slices.

Current rule families:
- Entrypoint guard: feature slices and infrastructure layers must not import
  entrypoint layers (CLI/preflight) or the DI container.
- Monitoring/features guard: gpt_trader.monitoring must not import
  gpt_trader.features at runtime (TYPE_CHECKING-only imports are allowed);
  the existing debt edges are frozen in an explicit allowlist.
- trade_ideas dependency freeze: gpt_trader.features.trade_ideas may only
  import gpt_trader.core, gpt_trader.errors, and itself.
- Cross-slice ratchet: any gpt_trader.features.<a> -> gpt_trader.features.<b>
  (a != b) import must be an edge in CROSS_SLICE_ALLOWED_EDGES.

The allowlists encode today's actual topology; they are a ratchet, not an
endorsement. Shrink them when the underlying coupling is removed. Do not add
edges without an architecture rationale (see docs/ARCHITECTURE.md, "Import
boundaries").

Usage:
    python scripts/ci/check_import_boundaries.py [paths...]

To extend the rule set:
- For the common case (a package that must not import entrypoint layers), add a
  (package, label) entry to _ENTRYPOINT_GUARDED_PACKAGES.
- For a custom rule, append an ImportRule to RULES with its own forbidden_prefixes
  and, optionally, allowlist_files, allowlist_import_prefixes, or allowlist_edges
  for exceptions.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
ENTRYPOINT_IMPORT_PREFIXES = (
    "gpt_trader.app.container",
    "gpt_trader.cli",
    "gpt_trader.preflight",
)


@dataclass(frozen=True)
class ImportRule:
    name: str
    description: str
    source_root: Path
    forbidden_prefixes: tuple[str, ...]
    allowlist_files: frozenset[Path] = field(default_factory=frozenset)
    allowlist_import_prefixes: tuple[str, ...] = ()
    # (repo-relative posix file path, import prefix) pairs: the single file may
    # import that prefix even though the rule forbids it elsewhere.
    allowlist_edges: tuple[tuple[str, str], ...] = ()
    # When True, imports inside `if TYPE_CHECKING:` blocks are exempt (they do
    # not create runtime coupling).
    ignore_type_checking_imports: bool = False


@dataclass(frozen=True)
class ImportViolation:
    path: Path
    line: int
    import_path: str
    rule: ImportRule


def _entrypoint_guard_rule(package: str, label: str) -> ImportRule:
    """Build a rule forbidding a lower-layer package from importing entrypoints."""
    return ImportRule(
        name=f"{package}_no_entrypoint_imports",
        description=f"{label} must not import entrypoint layers or the DI container.",
        source_root=REPO_ROOT / "src" / "gpt_trader" / package,
        forbidden_prefixes=ENTRYPOINT_IMPORT_PREFIXES,
    )


# Lower layers (feature slices + shared infrastructure) must never import the
# entrypoint layers (CLI/preflight) or the DI container. Listing each guarded
# package here keeps the architecture's dependency direction enforced in CI.
# Intentionally excluded: app/ (the composition root, which wires entrypoints),
# and dev/agent tooling (agents/, ci/) that legitimately drives those layers.
_ENTRYPOINT_GUARDED_PACKAGES: tuple[tuple[str, str], ...] = (
    ("features", "Feature slices"),
    ("monitoring", "Monitoring infrastructure"),
    ("persistence", "Persistence infrastructure"),
    ("security", "Security infrastructure"),
    ("core", "Core domain primitives"),
    ("logging", "Logging infrastructure"),
    ("utilities", "Shared utilities"),
    ("validation", "Validation infrastructure"),
    ("errors", "Error-handling infrastructure"),
    ("backtesting", "Backtesting framework"),
    ("config", "Configuration infrastructure"),
)

_FEATURES_ROOT = REPO_ROOT / "src" / "gpt_trader" / "features"

# Frozen runtime debt edges from monitoring into feature slices. Each entry is
# (repo-relative file, import prefix). TYPE_CHECKING-only imports never need an
# entry here. Empty since the broker protocols moved to gpt_trader.core
# (2026-07-02); keep the mechanism for future frozen edges.
_MONITORING_FEATURES_RUNTIME_ALLOWLIST: tuple[tuple[str, str], ...] = ()

_MONITORING_FEATURES_RULE = ImportRule(
    name="monitoring_no_feature_runtime_imports",
    description=(
        "Monitoring infrastructure must not import feature slices at runtime "
        "(TYPE_CHECKING-only imports are allowed). Existing debt edges are frozen in "
        "_MONITORING_FEATURES_RUNTIME_ALLOWLIST in scripts/ci/check_import_boundaries.py; "
        "see docs/ARCHITECTURE.md ('Import boundaries') before adding a new edge."
    ),
    source_root=REPO_ROOT / "src" / "gpt_trader" / "monitoring",
    forbidden_prefixes=("gpt_trader.features",),
    allowlist_edges=_MONITORING_FEATURES_RUNTIME_ALLOWLIST,
    ignore_type_checking_imports=True,
)

# The trade_ideas slice is deliberately dependency-light: it may import only
# core domain primitives and the error hierarchy (plus itself). Verified as the
# slice's complete gpt_trader import surface on 2026-07-01 — this freezes it.
TRADE_IDEAS_ALLOWED_IMPORT_PREFIXES: tuple[str, ...] = (
    "gpt_trader.core",
    "gpt_trader.errors",
    # Architecture rationale: the default-off regime-aware proposer and
    # PositionSizer bridge enrich trade-idea records from the intelligence
    # slice (MarketRegimeDetector state, deterministic sizing metadata) before
    # human approval; they do not call broker, order, or live-trading layers.
    "gpt_trader.features.intelligence.regime",
    "gpt_trader.features.intelligence.sizing",
    "gpt_trader.features.trade_ideas",
)

_TRADE_IDEAS_RULE = ImportRule(
    name="trade_ideas_frozen_dependencies",
    description=(
        "features/trade_ideas may only import gpt_trader.core, gpt_trader.errors, and "
        "itself. Extend TRADE_IDEAS_ALLOWED_IMPORT_PREFIXES in "
        "scripts/ci/check_import_boundaries.py only with an architecture rationale; "
        "see docs/ARCHITECTURE.md ('Import boundaries')."
    ),
    source_root=_FEATURES_ROOT / "trade_ideas",
    forbidden_prefixes=("gpt_trader",),
    allowlist_import_prefixes=TRADE_IDEAS_ALLOWED_IMPORT_PREFIXES,
)

# Frozen record of today's cross-slice topology (verified by AST scan,
# 2026-07-01). Every (source_slice, target_slice) import edge between feature
# slices must appear here; anything else fails CI. This is a ratchet: shrink
# it as couplings are removed, and do not add edges without an architecture
# rationale (see docs/ARCHITECTURE.md, "Import boundaries").
CROSS_SLICE_ALLOWED_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        # regime/* reuse live_trade stateful indicators.
        ("intelligence", "live_trade"),
        # execution guards/validation depend on broker protocols and coinbase specs.
        ("live_trade", "brokerages"),
        # factory + regime_switcher strategy use regime detection.
        ("live_trade", "intelligence"),
        # bot.py composition root constructs recorder-owned tick state; the
        # engine consumes it injected (docs/decisions/adopt-five-role-composition.md).
        ("live_trade", "recorder"),
        # engines/strategy.py signal -> trade-idea adapter.
        ("live_trade", "strategy_tools"),
        # engines/strategy.py trade-idea proposal workflow service.
        ("live_trade", "trade_ideas"),
        # Paper execution lane consumes APPROVED ideas via TradeIdeaService and
        # drives paper/mock brokers only (docs/decisions/adopt-five-role-composition.md).
        ("idea_execution", "trade_ideas"),
        ("idea_execution", "brokerages"),
        # RegimeAwareProposer overlays regime state; PositionSizer bridge
        # enriches sizing on trade-idea proposal records.
        ("trade_ideas", "intelligence"),
        # walk_forward/batch_runner reuse strategy protocol and baseline types.
        ("optimize", "live_trade"),
        # Recorder owns market-data acquisition over the read-only Coinbase
        # candle transport (docs/decisions/adopt-five-role-composition.md).
        ("recorder", "brokerages"),
        # Recorder produces the MarketSnapshot artifact defined by the
        # trade-idea contract; the frozen trade_ideas dependency set keeps the
        # dependency one-way (producer imports contract, never the reverse).
        ("recorder", "trade_ideas"),
        # trade_idea_adapter builds trade-idea records.
        ("strategy_tools", "trade_ideas"),
    }
)

# Some slice-to-slice edges are narrower than the target slice root. Each entry
# is (source_slice, target_slice, import prefix) and replaces the broad
# gpt_trader.features.<target_slice> allowance for that source/target pair.
CROSS_SLICE_NARROW_IMPORT_PREFIXES: frozenset[tuple[str, str, str]] = frozenset(
    {
        (
            "idea_execution",
            "brokerages",
            "gpt_trader.features.brokerages.mock",
        ),
        (
            "idea_execution",
            "brokerages",
            "gpt_trader.features.brokerages.paper",
        ),
        (
            "recorder",
            "brokerages",
            "gpt_trader.features.brokerages.coinbase",
        ),
    }
)

for _source, _target, _prefix in CROSS_SLICE_NARROW_IMPORT_PREFIXES:
    if (_source, _target) not in CROSS_SLICE_ALLOWED_EDGES:
        raise ValueError(
            f"Narrow cross-slice prefix for {_source}->{_target} requires a matching "
            "CROSS_SLICE_ALLOWED_EDGES entry."
        )


def _discover_feature_slices() -> tuple[str, ...]:
    """Enumerate feature slice packages so new slices are guarded automatically."""
    if not _FEATURES_ROOT.exists():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in _FEATURES_ROOT.iterdir()
            if entry.is_dir() and not entry.name.startswith(("_", "."))
        )
    )


def _cross_slice_rule(slice_name: str) -> ImportRule:
    """Build the cross-slice ratchet rule for one feature slice."""
    narrow_prefixes = sorted(
        prefix
        for source, _target, prefix in CROSS_SLICE_NARROW_IMPORT_PREFIXES
        if source == slice_name
    )
    narrow_targets = {
        target
        for source, target, _prefix in CROSS_SLICE_NARROW_IMPORT_PREFIXES
        if source == slice_name
    }
    allowed_targets = sorted(
        target
        for source, target in CROSS_SLICE_ALLOWED_EDGES
        if source == slice_name and target not in narrow_targets
    )
    return ImportRule(
        name=f"features_{slice_name}_cross_slice_imports",
        description=(
            f"Cross-slice imports from features/{slice_name} are frozen. A new "
            "slice-to-slice dependency must be added to CROSS_SLICE_ALLOWED_EDGES in "
            "scripts/ci/check_import_boundaries.py with an architecture rationale; "
            "see docs/ARCHITECTURE.md ('Import boundaries')."
        ),
        source_root=_FEATURES_ROOT / slice_name,
        forbidden_prefixes=("gpt_trader.features",),
        allowlist_import_prefixes=tuple(
            f"gpt_trader.features.{target}" for target in (slice_name, *allowed_targets)
        )
        + tuple(narrow_prefixes),
    )


RULES: tuple[ImportRule, ...] = (
    *(_entrypoint_guard_rule(package, label) for package, label in _ENTRYPOINT_GUARDED_PACKAGES),
    _MONITORING_FEATURES_RULE,
    _TRADE_IDEAS_RULE,
    *(_cross_slice_rule(slice_name) for slice_name in _discover_feature_slices()),
)


def _collect_explicit_files(paths: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    for entry in paths:
        path = Path(entry)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if path.is_dir():
            files.extend(path.rglob("*.py"))
        elif path.suffix == ".py":
            files.append(path)
    return files


def _module_for_path(path: Path) -> str | None:
    """Return the absolute module path for a file under src/ (best-effort)."""
    try:
        rel = path.resolve().relative_to(SRC_ROOT)
    except ValueError:
        return None

    if rel.suffix != ".py":
        return None

    parts = list(rel.with_suffix("").parts)
    if not parts:
        return None

    # __init__.py represents the package itself.
    if parts[-1] == "__init__":
        parts = parts[:-1]

    if not parts:
        return None

    return ".".join(parts)


def _package_parts_for_path(path: Path) -> list[str] | None:
    """Return package parts for a file (directory module path)."""
    mod = _module_for_path(path)
    if not mod:
        return None

    parts = mod.split(".")
    if path.name != "__init__.py":
        # Drop the module name to get package parts.
        parts = parts[:-1]

    return parts


def _resolve_relative_import(path: Path, node: ast.ImportFrom) -> str | None:
    """Resolve a relative ImportFrom to an absolute module prefix.

    This is intentionally conservative and only meant for boundary checks.
    """
    pkg = _package_parts_for_path(path)
    if pkg is None:
        return None

    if node.level <= 0:
        return node.module

    # level=1 => current package; level=2 => parent, etc.
    trim = node.level - 1
    if trim > len(pkg):
        return None

    base = pkg[: len(pkg) - trim]

    if node.module:
        base.extend(node.module.split("."))

    return ".".join(base) if base else None


def _is_type_checking_guard(test: ast.expr) -> bool:
    """Detect `if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` guards."""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _iter_imports(path: Path, tree: ast.AST) -> Iterable[tuple[int, str, bool]]:
    """Yield (line, import_path, type_checking_only) for every import in the tree."""

    def _walk(node: ast.AST, in_type_checking: bool) -> Iterable[tuple[int, str, bool]]:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, in_type_checking
            return
        if isinstance(node, ast.ImportFrom):
            module = _resolve_relative_import(path, node)
            if not module:
                return
            for alias in node.names:
                if alias.name == "*":
                    yield node.lineno, module, in_type_checking
                else:
                    yield node.lineno, f"{module}.{alias.name}", in_type_checking
            return
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for body_statement in node.body:
                yield from _walk(body_statement, True)
            for orelse_statement in node.orelse:
                yield from _walk(orelse_statement, in_type_checking)
            return
        for child in ast.iter_child_nodes(node):
            yield from _walk(child, in_type_checking)

    yield from _walk(tree, False)


def _matches_prefix(import_path: str, prefix: str) -> bool:
    return import_path == prefix or import_path.startswith(f"{prefix}.")


def _is_allowed(import_path: str, rule: ImportRule) -> bool:
    for allowed in rule.allowlist_import_prefixes:
        if _matches_prefix(import_path, allowed):
            return True
    return False


def _is_allowlisted_edge(path: Path, import_path: str, rule: ImportRule) -> bool:
    if not rule.allowlist_edges:
        return False
    try:
        relative = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return False
    return any(
        relative == edge_file and _matches_prefix(import_path, edge_prefix)
        for edge_file, edge_prefix in rule.allowlist_edges
    )


def _scan_rule(rule: ImportRule, explicit_files: list[Path] | None) -> list[ImportViolation]:
    if not rule.source_root.exists():
        return []

    if explicit_files is None:
        candidates = list(rule.source_root.rglob("*.py"))
    else:
        candidates = []
        for path in explicit_files:
            try:
                path.relative_to(rule.source_root)
            except ValueError:
                continue
            candidates.append(path)

    violations: list[ImportViolation] = []
    seen: set[tuple[Path, int, str, str]] = set()

    for path in candidates:
        if path in rule.allowlist_files:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(
                ImportViolation(
                    path=path,
                    line=1,
                    import_path=f"<unreadable: {exc}>",
                    rule=rule,
                )
            )
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            violations.append(
                ImportViolation(
                    path=path,
                    line=exc.lineno or 1,
                    import_path="<parse error>",
                    rule=rule,
                )
            )
            continue

        for line, import_path, type_checking_only in _iter_imports(path, tree):
            if type_checking_only and rule.ignore_type_checking_imports:
                continue
            if _is_allowed(import_path, rule):
                continue
            if _is_allowlisted_edge(path, import_path, rule):
                continue
            if any(_matches_prefix(import_path, prefix) for prefix in rule.forbidden_prefixes):
                key = (path, line, import_path, rule.name)
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    ImportViolation(
                        path=path,
                        line=line,
                        import_path=import_path,
                        rule=rule,
                    )
                )

    return violations


def scan(paths: Sequence[str] | None = None) -> int:
    explicit_files = _collect_explicit_files(paths or []) if paths else None
    all_violations: list[ImportViolation] = []

    for rule in RULES:
        all_violations.extend(_scan_rule(rule, explicit_files))

    if all_violations:
        print("ERRORS:")
        for violation in sorted(all_violations, key=lambda v: (str(v.path), v.line)):
            rel = violation.path.resolve().relative_to(REPO_ROOT)
            print(
                f"  ERROR {rel}:{violation.line}: imports {violation.import_path} "
                f"(rule: {violation.rule.name})"
            )
            print(f"     {violation.rule.description}")
        print(f"\n{len(all_violations)} violation(s) found.")
        return 1

    print("Import boundary guard passed.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional files/directories to scan (defaults to rule source roots).",
    )
    args = parser.parse_args(argv)
    return scan(args.paths)


if __name__ == "__main__":
    raise SystemExit(main())
