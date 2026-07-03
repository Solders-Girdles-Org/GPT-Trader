"""Tests for the risk-budget -> runtime-limit derivation seam (#1120 stage 2)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.app.risk_budget_seed import (
    RiskBudgetRuntimeSeed,
    apply_shorts_permission,
    resolve_risk_budget_runtime_seed,
)
from gpt_trader.features.trade_ideas.audit import ActorType
from gpt_trader.features.trade_ideas.budget import (
    DEFAULT_RISK_BUDGET,
    BudgetLogEntry,
    RiskBudgetLog,
)


def _append_budget_version(ideas_root: Path, **overrides: object) -> None:
    log = RiskBudgetLog(ideas_root / "risk_budget.jsonl")
    current = log.current()
    version = 1 if current is None else current.version + 1
    budget = replace(DEFAULT_RISK_BUDGET, version=version, **overrides)  # type: ignore[arg-type]
    log.append(
        BudgetLogEntry(
            timestamp=datetime(2026, 7, 3, tzinfo=UTC),
            actor_type=ActorType.HUMAN,
            actor_id="rj",
            budget=budget,
        )
    )


class TestResolveRiskBudgetRuntimeSeed:
    def test_missing_log_falls_back_to_default_budget(self, tmp_path: Path) -> None:
        seed = resolve_risk_budget_runtime_seed(tmp_path)

        assert seed.budget_version == DEFAULT_RISK_BUDGET.version
        assert seed.budget_source == "default"
        # DEFAULT budget: 10% daily loss, 100% open notional (percent points)
        # normalized to the RiskConfig fraction convention.
        assert seed.daily_loss_limit_pct == pytest.approx(0.10)
        assert seed.max_exposure_pct == pytest.approx(1.0)
        assert seed.allow_futures_leverage is False
        assert seed.allow_naked_shorts is False

    def test_seeded_log_uses_current_version(self, tmp_path: Path) -> None:
        _append_budget_version(tmp_path)
        _append_budget_version(
            tmp_path,
            max_daily_loss_pct=Decimal("7"),
            max_open_notional_pct=Decimal("60"),
            allow_futures_leverage=True,
            allow_naked_shorts=True,
        )

        seed = resolve_risk_budget_runtime_seed(tmp_path)

        assert seed.budget_version == 2
        assert seed.budget_source == "risk_budget_log"
        assert seed.daily_loss_limit_pct == pytest.approx(0.07)
        assert seed.max_exposure_pct == pytest.approx(0.60)
        assert seed.allow_futures_leverage is True
        assert seed.allow_naked_shorts is True

    def test_corrupt_log_fails_closed(self, tmp_path: Path) -> None:
        (tmp_path / "risk_budget.jsonl").write_text("not json\n", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            resolve_risk_budget_runtime_seed(tmp_path)

    def test_env_root_resolution(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _append_budget_version(tmp_path, max_daily_loss_pct=Decimal("3"))
        monkeypatch.setenv("GPT_TRADER_IDEAS_ROOT", str(tmp_path))

        seed = resolve_risk_budget_runtime_seed()

        assert seed.budget_version == 1
        assert seed.daily_loss_limit_pct == pytest.approx(0.03)

    def test_telemetry_payload_round_trips_fields(self) -> None:
        seed = RiskBudgetRuntimeSeed(
            budget_version=3,
            budget_source="risk_budget_log",
            daily_loss_limit_pct=0.07,
            max_exposure_pct=0.6,
            allow_futures_leverage=True,
            allow_naked_shorts=False,
        )

        assert seed.telemetry_payload() == {
            "budget_version": 3,
            "budget_source": "risk_budget_log",
            "daily_loss_limit_pct": 0.07,
            "max_exposure_pct": 0.6,
            "allow_futures_leverage": True,
            "allow_naked_shorts": False,
        }


def _seed(allow_naked_shorts: bool) -> RiskBudgetRuntimeSeed:
    return RiskBudgetRuntimeSeed(
        budget_version=1,
        budget_source="default",
        daily_loss_limit_pct=0.10,
        max_exposure_pct=1.0,
        allow_futures_leverage=False,
        allow_naked_shorts=allow_naked_shorts,
    )


class TestApplyShortsPermission:
    def test_forbidden_shorts_forces_all_flags_off(self) -> None:
        config = BotConfig(enable_shorts=True)
        config.strategy.enable_shorts = True
        config.mean_reversion.enable_shorts = True

        apply_shorts_permission(config, _seed(allow_naked_shorts=False))

        assert config.enable_shorts is False
        assert config.strategy.enable_shorts is False
        assert config.mean_reversion.enable_shorts is False
        # active_enable_shorts derives from strategy config; the gate must
        # leave no mismatch for it to warn about.
        assert config.active_enable_shorts is False

    def test_permission_is_not_a_mandate(self) -> None:
        config = BotConfig(enable_shorts=False)
        config.strategy.enable_shorts = False
        config.mean_reversion.enable_shorts = True

        apply_shorts_permission(config, _seed(allow_naked_shorts=True))

        assert config.enable_shorts is False
        assert config.strategy.enable_shorts is False
        assert config.mean_reversion.enable_shorts is True
