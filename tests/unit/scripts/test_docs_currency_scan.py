from __future__ import annotations

from pathlib import Path

import pytest
from scripts.maintenance import docs_currency_scan as scan


def test_normalize_item_filters_glossary_noise() -> None:
    assert scan.normalize_item("env_var", "MVP") is None
    assert scan.normalize_item("env_var", "MOCK_BROKER") == "MOCK_BROKER"
    assert (
        scan.normalize_item("path", "runtime_data/canary/reports") == "runtime_data/canary/reports"
    )


def test_verify_path_marks_runtime_paths_uncertain(tmp_path: Path) -> None:
    state = scan.ScanState(repo_root=tmp_path)
    result = scan.verify_path(state, "runtime_data/canary/reports/daily_report_2026-01-01.txt")
    assert result.status == "uncertain"


def test_verify_env_var_accepts_makefile_variable(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text(
        "READINESS_REPORT_DIR?=runtime_data/reports\n", encoding="utf-8"
    )
    state = scan.load_state(tmp_path, fetch_help=False)
    result = scan.verify_env_var(state, "READINESS_REPORT_DIR")
    assert result.status == "ok"
    assert "Makefile" in result.method


def test_verify_command_accepts_python_module(tmp_path: Path) -> None:
    module_dir = tmp_path / "src" / "gpt_trader" / "cli"
    module_dir.mkdir(parents=True)
    (module_dir / "__init__.py").write_text("", encoding="utf-8")
    (module_dir / "__main__.py").write_text("print('ok')\n", encoding="utf-8")
    state = scan.ScanState(repo_root=tmp_path)
    result = scan.verify_command(state, "python -m gpt_trader.cli")
    assert result.status == "ok"


def test_verify_command_accepts_python_module_file(tmp_path: Path) -> None:
    pkg = tmp_path / "src" / "gpt_trader"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "tool.py").write_text("print('ok')\n", encoding="utf-8")
    state = scan.ScanState(repo_root=tmp_path)
    result = scan.verify_command(state, "python -m gpt_trader.tool")
    assert result.status == "ok"


def test_verify_command_rejects_python_package_without_main(tmp_path: Path) -> None:
    # A package with only __init__.py is importable but NOT runnable via `python -m`.
    pkg = tmp_path / "src" / "gpt_trader" / "pkgonly"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    state = scan.ScanState(repo_root=tmp_path)
    result = scan.verify_command(state, "python -m gpt_trader.pkgonly")
    assert result.status == "missing"


def test_scan_docs_on_fixture_repo_produces_report(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "sample.md").write_text(
        "Run `uv run gpt-trader run` and read `src/gpt_trader/app/missing_module.py`.\n",
        encoding="utf-8",
    )
    doc_files, extracted, discrepancies = scan.scan_docs(tmp_path, fetch_help=False)
    assert [path.name for path in doc_files] == ["sample.md"]
    assert extracted, "expected at least one extracted reference"
    assert any(item.category == "path" for item in extracted)
    report = scan.render_report(
        doc_files=doc_files,
        extracted=extracted,
        discrepancies=discrepancies,
        repo_root=tmp_path,
    )
    assert "# Docs-to-Code Currency Scan Report" in report
    assert "Docs processed:" in report
    out = tmp_path / "report.md"
    out.write_text(report, encoding="utf-8")
    assert out.stat().st_size > 0


@pytest.mark.parametrize(
    ("item", "expected_status"),
    [
        ("gpt_trader.orchestration", "stale"),
        ("scripts/run_spot_profile.py", "stale"),
    ],
)
def test_verify_module_or_path_marks_removed_items_stale(item: str, expected_status: str) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    state = scan.load_state(repo_root, fetch_help=False)
    # A doc that is neither a removal registry nor a migration guide: a removed
    # identifier here is genuine drift.
    if item.startswith("scripts/"):
        result = scan.verify_path(state, item, "docs/SOME_GUIDE.md")
    else:
        result = scan.verify_module(state, item, "docs/SOME_GUIDE.md")
    assert result.status == expected_status


@pytest.mark.parametrize(
    ("category", "item", "source_doc"),
    [
        # DEPRECATIONS.md is a removal registry: any removed module/path named
        # there is expected guidance, not drift.
        ("module", "gpt_trader.orchestration", "docs/DEPRECATIONS.md"),
        ("path", "src/gpt_trader/orchestration/", "docs/DEPRECATIONS.md"),
        # A removed module named in the registry whose name contains no
        # DEPRECATED_MARKERS substring must still be exempted (regression guard:
        # verify_module previously only honored removals inside the marker branch).
        ("module", "gpt_trader.legacy_pipeline.runner", "docs/DEPRECATIONS.md"),
        # ARCHITECTURE.md carries an old->new migration table; marker-matched
        # (known-removed) identifiers there are exempt.
        ("module", "gpt_trader.orchestration.execution.degradation", "docs/ARCHITECTURE.md"),
    ],
)
def test_documented_removals_are_not_flagged(category: str, item: str, source_doc: str) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    state = scan.load_state(repo_root, fetch_help=False)
    if category == "module":
        result = scan.verify_module(state, item, source_doc)
    else:
        result = scan.verify_path(state, item, source_doc)
    assert result.status == "ok"


def test_removed_env_var_in_registry_doc_is_ok(tmp_path: Path, monkeypatch) -> None:
    # An env var absent from template and source, named in the removal registry,
    # is expected guidance (not "missing"). Force "absent from source" so the
    # test does not depend on the literal appearing elsewhere in the repo.
    repo_root = Path(__file__).resolve().parents[3]
    state = scan.load_state(repo_root, fetch_help=False)
    monkeypatch.setattr(scan, "_grep_repo", lambda *args, **kwargs: [])

    missing = scan.verify_env_var(state, "SOME_REMOVED_ENV_VAR", "docs/OTHER.md")
    assert missing.status == "missing"

    registry = scan.verify_env_var(state, "SOME_REMOVED_ENV_VAR", "docs/DEPRECATIONS.md")
    assert registry.status == "ok"


def test_short_cli_flags_are_extracted(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("Use `-k` to filter, `--profile` to select.\n", encoding="utf-8")
    flags = {
        item.item for item in scan.extract_from_doc(doc, tmp_path) if item.category == "cli_flag"
    }
    assert "-k" in flags
    assert "--profile" in flags


def test_unknown_short_flag_is_uncertain_not_missing(tmp_path: Path) -> None:
    state = scan.ScanState(repo_root=tmp_path)
    result = scan.verify_cli_flag(state, "-k", "docs/some.md")
    assert result.status == "uncertain"


def test_unknown_flag_is_uncertain_when_help_skipped(tmp_path: Path) -> None:
    state = scan.load_state(tmp_path, fetch_help=False)
    result = scan.verify_cli_flag(state, "--made-up-flag", "docs/some.md")
    assert result.status == "uncertain"


def test_verify_command_handles_uv_run_python_module(tmp_path: Path) -> None:
    module_dir = tmp_path / "src" / "gpt_trader" / "cli"
    module_dir.mkdir(parents=True)
    (module_dir / "__init__.py").write_text("", encoding="utf-8")
    (module_dir / "__main__.py").write_text("print('ok')\n", encoding="utf-8")
    state = scan.ScanState(repo_root=tmp_path)
    result = scan.verify_command(state, "uv run python -m gpt_trader.cli")
    assert result.status == "ok"


def test_verify_module_source_fallback_resolves_flat_module(tmp_path: Path) -> None:
    pkg = tmp_path / "src" / "myproj" / "features"
    pkg.mkdir(parents=True)
    (pkg / "symbols.py").write_text("def derive_thing():\n    return 1\n", encoding="utf-8")
    state = scan.ScanState(repo_root=tmp_path)
    result = scan.verify_module(state, "myproj.features.symbols.derive_thing", "docs/x.md")
    assert result.status == "uncertain"
    assert result.method == "source grep"


def test_live_replacement_target_in_registry_is_verified_not_exempted(tmp_path: Path) -> None:
    # A migration-path replacement target that still exists must be verified via
    # existence, not blanket-exempted just because its doc is a removal registry --
    # otherwise a later rename/deletion would be silently missed.
    pkg = tmp_path / "src" / "myproj" / "features"
    pkg.mkdir(parents=True)
    (pkg / "symbols.py").write_text("def derive_thing():\n    return 1\n", encoding="utf-8")
    state = scan.ScanState(repo_root=tmp_path)

    live = scan.verify_module(state, "myproj.features.symbols.derive_thing", "docs/DEPRECATIONS.md")
    assert live.status == "uncertain"
    assert live.method == "source grep"  # resolved by existence, not "documented removal"

    # The same-registry name that does NOT resolve is still exempted as a removal.
    removed = scan.verify_module(state, "myproj.features.gone.helper", "docs/DEPRECATIONS.md")
    assert removed.status == "ok"
    assert removed.method == "documented removal"


def test_render_report_collapses_newlines_in_notes(tmp_path: Path) -> None:
    ext = scan.ExtractedItem(
        source_doc="docs/x.md",
        category="module",
        item="gpt_trader.foo",
        item_type="gpt_trader",
    )
    result = scan.VerificationResult("missing", "import", "Traceback:\nLine1\nLine2")
    report = scan.render_report(
        doc_files=[], extracted=[ext], discrepancies=[(ext, result)], repo_root=tmp_path
    )
    rows = [line for line in report.splitlines() if "**missing**" in line]
    assert len(rows) == 1
    assert "Traceback: Line1 Line2" in rows[0]


def _discrepancy(source_doc: str, category: str, item: str, status: str) -> scan.Discrepancy:
    ext = scan.ExtractedItem(source_doc, category, item, "assignment")
    return ext, scan.VerificationResult(status, "test", status)


def test_parse_fail_on_parses_and_rejects_unknown() -> None:
    assert scan._parse_fail_on("missing,stale") == {"missing", "stale"}
    assert scan._parse_fail_on(" missing , stale ") == {"missing", "stale"}
    assert scan._parse_fail_on("") == set()
    with pytest.raises(SystemExit):
        scan._parse_fail_on("bogus")


def test_is_suppressed_matches_only_exact_pairs() -> None:
    doc, item = next(iter(scan.CURRENCY_SUPPRESSIONS))
    assert scan.is_suppressed(doc, item) is True
    assert scan.is_suppressed(doc, "--definitely-not-suppressed") is False
    assert scan.is_suppressed("docs/not-a-real-doc.md", item) is False


def test_gating_findings_excludes_suppressed_and_non_gated_statuses() -> None:
    doc, item = next(iter(scan.CURRENCY_SUPPRESSIONS))
    discrepancies = [
        _discrepancy("docs/x.md", "module", "gpt_trader.does_not_exist", "missing"),
        _discrepancy(doc, "cli_flag", item, "missing"),  # suppressed
        _discrepancy("docs/y.md", "cli_flag", "--maybe", "uncertain"),  # not gated
    ]
    gating = scan.gating_findings(discrepancies, {"missing", "stale"})
    assert [ext.item for ext, _ in gating] == ["gpt_trader.does_not_exist"]


def test_unused_suppressions_reports_orphans() -> None:
    # An empty scan orphans every suppression entry.
    assert scan.unused_suppressions([]) == set(scan.CURRENCY_SUPPRESSIONS)
    # A live finding for one entry removes it from the orphan set.
    doc, item = next(iter(scan.CURRENCY_SUPPRESSIONS))
    disc = [_discrepancy(doc, "cli_flag", item, "missing")]
    assert (doc, item) not in scan.unused_suppressions(disc)


def _patch_scan(monkeypatch, discrepancies: list[scan.Discrepancy]) -> None:
    monkeypatch.setattr(scan, "scan_docs", lambda *a, **k: ([], [], discrepancies))
    monkeypatch.setattr(scan, "render_report", lambda **k: "")


def test_main_fail_on_exits_nonzero_for_unsuppressed_missing(monkeypatch) -> None:
    _patch_scan(monkeypatch, [_discrepancy("docs/x.md", "module", "gpt_trader.gone", "missing")])
    assert scan.main(["--fail-on", "missing,stale", "--skip-help"]) == 1


def test_main_fail_on_zero_when_only_suppressed_or_uncertain(monkeypatch) -> None:
    doc, item = next(iter(scan.CURRENCY_SUPPRESSIONS))
    monkeypatch.setattr(scan, "CURRENCY_SUPPRESSIONS", {(doc, item): "test suppression"})
    _patch_scan(
        monkeypatch,
        [
            _discrepancy(doc, "cli_flag", item, "missing"),  # suppressed
            _discrepancy("docs/y.md", "path", "some/uncertain/path", "uncertain"),
        ],
    )
    assert scan.main(["--fail-on", "missing,stale", "--skip-help"]) == 0


def test_main_fail_on_exits_nonzero_for_unused_suppression(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        scan, "CURRENCY_SUPPRESSIONS", {("docs/x.md", "REMOVED_REFERENCE"): "test suppression"}
    )
    _patch_scan(monkeypatch, [])

    assert scan.main(["--fail-on", "missing,stale", "--skip-help"]) == 1
    output = capsys.readouterr().out
    assert "unused suppression entries" in output
    assert "docs/x.md :: `REMOVED_REFERENCE`" in output


def test_main_without_fail_on_is_report_only(monkeypatch) -> None:
    _patch_scan(monkeypatch, [_discrepancy("docs/x.md", "module", "gpt_trader.gone", "missing")])
    assert scan.main(["--skip-help"]) == 0
