from __future__ import annotations

from pathlib import Path

import scripts.ci.check_import_boundaries as check_import_boundaries


def _configure_rule(monkeypatch: object, repo_root: Path) -> Path:
    src_root = repo_root / "src"
    rule = check_import_boundaries.ImportRule(
        name="features_no_entrypoint_imports",
        description="Feature slices must not import entrypoint layers or the DI container.",
        source_root=src_root / "gpt_trader" / "features",
        forbidden_prefixes=check_import_boundaries.ENTRYPOINT_IMPORT_PREFIXES,
    )
    monkeypatch.setattr(check_import_boundaries, "REPO_ROOT", repo_root)
    monkeypatch.setattr(check_import_boundaries, "SRC_ROOT", src_root)
    monkeypatch.setattr(check_import_boundaries, "RULES", (rule,))
    return rule.source_root


def _write_file(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_absolute_import_violation(tmp_path, monkeypatch, capsys) -> None:
    features_root = _configure_rule(monkeypatch, tmp_path)
    _write_file(
        tmp_path,
        "src/gpt_trader/features/alpha/absolute_violation.py",
        "from gpt_trader.cli.widgets import Widget\n",
    )

    result = check_import_boundaries.scan([str(features_root)])
    captured = capsys.readouterr()

    assert result == 1
    assert "imports gpt_trader.cli.widgets.Widget" in captured.out
    assert "violation(s) found" in captured.out


def test_relative_import_violation(tmp_path, monkeypatch, capsys) -> None:
    features_root = _configure_rule(monkeypatch, tmp_path)
    _write_file(
        tmp_path,
        "src/gpt_trader/features/alpha/relative_violation.py",
        "from ...cli.widgets import Widget\n",
    )

    result = check_import_boundaries.scan([str(features_root)])
    captured = capsys.readouterr()

    assert result == 1
    assert "imports gpt_trader.cli.widgets.Widget" in captured.out
    assert "violation(s) found" in captured.out


def test_allowed_imports_pass(tmp_path, monkeypatch, capsys) -> None:
    features_root = _configure_rule(monkeypatch, tmp_path)
    _write_file(
        tmp_path,
        "src/gpt_trader/features/alpha/allowed_absolute.py",
        "from gpt_trader.features.alpha import service\n",
    )
    _write_file(
        tmp_path,
        "src/gpt_trader/features/alpha/allowed_relative.py",
        "from ..beta import helper\n",
    )

    result = check_import_boundaries.scan([str(features_root)])
    captured = capsys.readouterr()

    assert result == 0
    assert "Import boundary guard passed." in captured.out


def test_container_import_violation(tmp_path, monkeypatch, capsys) -> None:
    features_root = _configure_rule(monkeypatch, tmp_path)
    _write_file(
        tmp_path,
        "src/gpt_trader/features/alpha/container_violation.py",
        "from gpt_trader.app.container import ApplicationContainer\n",
    )

    result = check_import_boundaries.scan([str(features_root)])
    captured = capsys.readouterr()

    assert result == 1
    assert "imports gpt_trader.app.container.ApplicationContainer" in captured.out


def _configure_monitoring_rule(monkeypatch: object, repo_root: Path) -> Path:
    src_root = repo_root / "src"
    rule = check_import_boundaries.ImportRule(
        name="monitoring_no_feature_runtime_imports",
        description="Monitoring must not import feature slices at runtime.",
        source_root=src_root / "gpt_trader" / "monitoring",
        forbidden_prefixes=("gpt_trader.features",),
        allowlist_edges=(
            (
                "src/gpt_trader/monitoring/health_checks.py",
                "gpt_trader.features.brokerages.core.protocols",
            ),
        ),
        ignore_type_checking_imports=True,
    )
    monkeypatch.setattr(check_import_boundaries, "REPO_ROOT", repo_root)
    monkeypatch.setattr(check_import_boundaries, "SRC_ROOT", src_root)
    monkeypatch.setattr(check_import_boundaries, "RULES", (rule,))
    return rule.source_root


def test_monitoring_runtime_feature_import_fails(tmp_path, monkeypatch, capsys) -> None:
    monitoring_root = _configure_monitoring_rule(monkeypatch, tmp_path)
    _write_file(
        tmp_path,
        "src/gpt_trader/monitoring/new_module.py",
        "from gpt_trader.features.live_trade.bot import TradingBot\n",
    )

    result = check_import_boundaries.scan([str(monitoring_root)])
    captured = capsys.readouterr()

    assert result == 1
    assert "imports gpt_trader.features.live_trade.bot.TradingBot" in captured.out


def test_monitoring_type_checking_feature_import_passes(tmp_path, monkeypatch, capsys) -> None:
    monitoring_root = _configure_monitoring_rule(monkeypatch, tmp_path)
    _write_file(
        tmp_path,
        "src/gpt_trader/monitoring/typed_only.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from gpt_trader.features.live_trade.bot import TradingBot\n",
    )

    result = check_import_boundaries.scan([str(monitoring_root)])
    captured = capsys.readouterr()

    assert result == 0
    assert "Import boundary guard passed." in captured.out


def test_monitoring_allowlisted_edge_passes_only_in_that_file(
    tmp_path, monkeypatch, capsys
) -> None:
    monitoring_root = _configure_monitoring_rule(monkeypatch, tmp_path)
    allowed_import = (
        "from gpt_trader.features.brokerages.core.protocols import TickerFreshnessProvider\n"
    )
    _write_file(tmp_path, "src/gpt_trader/monitoring/health_checks.py", allowed_import)
    _write_file(tmp_path, "src/gpt_trader/monitoring/other_module.py", allowed_import)

    result = check_import_boundaries.scan([str(monitoring_root)])
    captured = capsys.readouterr()

    assert result == 1
    assert "other_module.py" in captured.out
    assert "health_checks.py" not in captured.out


def _configure_trade_ideas_rule(monkeypatch: object, repo_root: Path) -> Path:
    src_root = repo_root / "src"
    rule = check_import_boundaries.ImportRule(
        name="trade_ideas_frozen_dependencies",
        description="trade_ideas may only import core, errors, and itself.",
        source_root=src_root / "gpt_trader" / "features" / "trade_ideas",
        forbidden_prefixes=("gpt_trader",),
        allowlist_import_prefixes=check_import_boundaries.TRADE_IDEAS_ALLOWED_IMPORT_PREFIXES,
    )
    monkeypatch.setattr(check_import_boundaries, "REPO_ROOT", repo_root)
    monkeypatch.setattr(check_import_boundaries, "SRC_ROOT", src_root)
    monkeypatch.setattr(check_import_boundaries, "RULES", (rule,))
    return rule.source_root


def test_trade_ideas_frozen_set_passes(tmp_path, monkeypatch, capsys) -> None:
    trade_ideas_root = _configure_trade_ideas_rule(monkeypatch, tmp_path)
    _write_file(
        tmp_path,
        "src/gpt_trader/features/trade_ideas/service.py",
        "from gpt_trader.core import Candle\n"
        "from gpt_trader.errors import ValidationError\n"
        "from gpt_trader.features.trade_ideas.audit import ActorType\n"
        "from .workflow import advance\n",
    )

    result = check_import_boundaries.scan([str(trade_ideas_root)])
    captured = capsys.readouterr()

    assert result == 0
    assert "Import boundary guard passed." in captured.out


def test_trade_ideas_import_outside_frozen_set_fails(tmp_path, monkeypatch, capsys) -> None:
    trade_ideas_root = _configure_trade_ideas_rule(monkeypatch, tmp_path)
    _write_file(
        tmp_path,
        "src/gpt_trader/features/trade_ideas/new_module.py",
        "from gpt_trader.utilities.logging_patterns import get_logger\n",
    )

    result = check_import_boundaries.scan([str(trade_ideas_root)])
    captured = capsys.readouterr()

    assert result == 1
    assert "imports gpt_trader.utilities.logging_patterns.get_logger" in captured.out


def _configure_cross_slice_rule(monkeypatch: object, repo_root: Path, slice_name: str) -> Path:
    src_root = repo_root / "src"
    monkeypatch.setattr(check_import_boundaries, "REPO_ROOT", repo_root)
    monkeypatch.setattr(check_import_boundaries, "SRC_ROOT", src_root)
    monkeypatch.setattr(
        check_import_boundaries,
        "_FEATURES_ROOT",
        src_root / "gpt_trader" / "features",
    )
    rule = check_import_boundaries._cross_slice_rule(slice_name)
    monkeypatch.setattr(check_import_boundaries, "RULES", (rule,))
    return rule.source_root


def test_cross_slice_allowlisted_edge_passes(tmp_path, monkeypatch, capsys) -> None:
    slice_root = _configure_cross_slice_rule(monkeypatch, tmp_path, "optimize")
    _write_file(
        tmp_path,
        "src/gpt_trader/features/optimize/walk_forward.py",
        "from gpt_trader.features.live_trade.strategies.base import StrategyProtocol\n"
        "from gpt_trader.features.optimize.runner import batch_runner\n",
    )

    result = check_import_boundaries.scan([str(slice_root)])
    captured = capsys.readouterr()

    assert result == 0
    assert "Import boundary guard passed." in captured.out


def test_cross_slice_new_edge_fails_with_allowlist_pointer(tmp_path, monkeypatch, capsys) -> None:
    slice_root = _configure_cross_slice_rule(monkeypatch, tmp_path, "data")
    _write_file(
        tmp_path,
        "src/gpt_trader/features/data/feed.py",
        "from gpt_trader.features.live_trade.bot import TradingBot\n",
    )

    result = check_import_boundaries.scan([str(slice_root)])
    captured = capsys.readouterr()

    assert result == 1
    assert "imports gpt_trader.features.live_trade.bot.TradingBot" in captured.out
    assert "CROSS_SLICE_ALLOWED_EDGES" in captured.out
    assert "docs/ARCHITECTURE.md" in captured.out


def test_cross_slice_allowlist_is_frozen_topology() -> None:
    # The ratchet encodes today's verified slice-to-slice edges. Adding an edge
    # here requires an architecture rationale (docs/ARCHITECTURE.md); removing
    # one is progress and should just update this test.
    assert check_import_boundaries.CROSS_SLICE_ALLOWED_EDGES == frozenset(
        {
            ("intelligence", "live_trade"),
            ("live_trade", "brokerages"),
            ("live_trade", "intelligence"),
            ("live_trade", "strategy_tools"),
            ("live_trade", "trade_ideas"),
            # RegimeAwareProposer overlays intelligence regime state onto proposal text.
            ("trade_ideas", "intelligence"),
            ("optimize", "live_trade"),
            ("strategy_tools", "trade_ideas"),
        }
    )


def test_trade_ideas_allowed_prefixes_are_frozen() -> None:
    assert check_import_boundaries.TRADE_IDEAS_ALLOWED_IMPORT_PREFIXES == (
        "gpt_trader.core",
        "gpt_trader.errors",
        # Regime-aware proposer reads MarketRegimeDetector before human approval.
        "gpt_trader.features.intelligence.regime",
        "gpt_trader.features.trade_ideas",
    )


def test_default_rules_cover_lower_layer_entrypoint_guards() -> None:
    rule_names = {rule.name for rule in check_import_boundaries.RULES}

    # Feature slices plus every shared infrastructure package must be guarded so
    # the dependency direction (lower layers never import entrypoints) cannot
    # silently regress.
    expected = {
        "features_no_entrypoint_imports",
        "monitoring_no_entrypoint_imports",
        "persistence_no_entrypoint_imports",
        "security_no_entrypoint_imports",
        "core_no_entrypoint_imports",
        "logging_no_entrypoint_imports",
        "utilities_no_entrypoint_imports",
        "validation_no_entrypoint_imports",
        "errors_no_entrypoint_imports",
        "backtesting_no_entrypoint_imports",
        "config_no_entrypoint_imports",
    }
    assert expected <= rule_names


def test_default_rules_cover_ratchet_families() -> None:
    rule_names = {rule.name for rule in check_import_boundaries.RULES}

    assert "monitoring_no_feature_runtime_imports" in rule_names
    assert "trade_ideas_frozen_dependencies" in rule_names

    # Every feature slice on disk gets a cross-slice ratchet rule automatically,
    # so a brand-new slice cannot silently grow unlisted dependencies.
    slices = check_import_boundaries._discover_feature_slices()
    assert slices, "expected at least one feature slice on disk"
    for slice_name in slices:
        assert f"features_{slice_name}_cross_slice_imports" in rule_names


def test_every_guarded_package_exists_on_disk() -> None:
    # A typo'd package name would create a rule whose source_root never exists,
    # silently guarding nothing. Each guarded package must be a real directory.
    for rule in check_import_boundaries.RULES:
        assert rule.source_root.is_dir(), f"missing guarded package: {rule.source_root}"
