"""Startup derivation seam: seed runtime risk limits from the active RiskBudget.

Implements the derivation stage of the accepted decision
``docs/decisions/canonical-risk-limit-vocabulary.md`` (Option A): the
versioned ``RiskBudget`` is canonical for risk appetite, and the live
``RiskConfig`` derives its appetite fields from the budget version current at
engine startup. A budget change mid-run takes effect at approval time
immediately but at runtime only after restart; the seeded version is recorded
in startup telemetry so that drift window stays visible.

The seam is default-on behind ``BotConfig.risk_budget_runtime_seed_enabled``.
The seeded budget defaults are LOOSER than the legacy runtime defaults (10%
daily loss / 100% open notional vs 5% / 80%); the loosened breaker band is
pinned by the MOCK_BROKER regression in
``tests/integration/test_risk_budget_seeded_breaker.py`` (#1120).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gpt_trader.core.risk_units import pct_points_to_fraction
from gpt_trader.features.trade_ideas.budget import DEFAULT_RISK_BUDGET, RiskBudgetLog
from gpt_trader.features.trade_ideas.service import resolve_ideas_root

if TYPE_CHECKING:
    from gpt_trader.app.config import BotConfig

RISK_BUDGET_SEED_EVENT_TYPE = "risk_budget_runtime_seed"


@dataclass(frozen=True, slots=True)
class RiskBudgetRuntimeSeed:
    """Runtime risk-appetite values derived from one RiskBudget version.

    ``daily_loss_limit_pct`` and ``max_exposure_pct`` are unit fractions
    (``RiskConfig`` convention), already normalized from the budget's
    percent-point fields through the canonical converter.
    """

    budget_version: int
    budget_source: str  # "risk_budget_log" | "default"
    daily_loss_limit_pct: float
    max_exposure_pct: float
    allow_futures_leverage: bool
    allow_naked_shorts: bool

    def telemetry_payload(self) -> dict[str, Any]:
        return {
            "budget_version": self.budget_version,
            "budget_source": self.budget_source,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
            "max_exposure_pct": self.max_exposure_pct,
            "allow_futures_leverage": self.allow_futures_leverage,
            "allow_naked_shorts": self.allow_naked_shorts,
        }


def resolve_risk_budget_runtime_seed(ideas_root: Path | None = None) -> RiskBudgetRuntimeSeed:
    """Resolve the active budget version into runtime seed values.

    Reads the same ``risk_budget.jsonl`` the approval gate uses. A missing log
    falls back to ``DEFAULT_RISK_BUDGET`` (matching ``TradeIdeaService``); a
    corrupt log raises so startup fails closed instead of trading on limits
    that cannot be attributed to a budget version.
    """
    root = resolve_ideas_root(ideas_root).expanduser()
    logged = RiskBudgetLog(root / "risk_budget.jsonl").current()
    budget = logged if logged is not None else DEFAULT_RISK_BUDGET
    return RiskBudgetRuntimeSeed(
        budget_version=budget.version,
        budget_source="risk_budget_log" if logged is not None else "default",
        daily_loss_limit_pct=float(pct_points_to_fraction(budget.max_daily_loss_pct)),
        max_exposure_pct=float(pct_points_to_fraction(budget.max_open_notional_pct)),
        allow_futures_leverage=budget.allow_futures_leverage,
        allow_naked_shorts=budget.allow_naked_shorts,
    )


def apply_shorts_permission(config: BotConfig, seed: RiskBudgetRuntimeSeed) -> None:
    """Gate shorts by the budget's ``allow_naked_shorts`` permission.

    The permission only restricts: when the budget forbids naked shorts, the
    per-strategy flags (the canonical source behind
    ``BotConfig.active_enable_shorts``) are forced off so the factory and the
    runtime agree. When the budget allows shorts, strategy preferences are
    left untouched — a permission is not a mandate.
    """
    if seed.allow_naked_shorts:
        return
    config.set_enable_shorts(False)


__all__ = [
    "RISK_BUDGET_SEED_EVENT_TYPE",
    "RiskBudgetRuntimeSeed",
    "apply_shorts_permission",
    "resolve_risk_budget_runtime_seed",
]
