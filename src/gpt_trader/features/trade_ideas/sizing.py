"""PositionSizer bridge for trade-idea proposal records.

The intelligence ``PositionSizer`` works in terms of account equity, confidence,
regime, and stop distance. This adapter maps that output into the immutable
``TradeIdea`` record contract without reading live accounts or submitting orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from gpt_trader.core.risk_units import fraction_to_pct_points, pct_points_to_fraction
from gpt_trader.features.intelligence.sizing import (
    PositionSizer,
    PositionSizingConfig,
    SizingResult,
)
from gpt_trader.features.trade_ideas.budget import DEFAULT_RISK_BUDGET, RiskBudget
from gpt_trader.features.trade_ideas.models import ConfidenceLabel, SizingRecommendation

POSITION_SIZER_BRIDGE_VERSION = "position-sizer-bridge-v1"


class PositionSizerLike(Protocol):
    """Minimal PositionSizer surface used by proposers."""

    config: PositionSizingConfig

    def calculate_size(
        self,
        symbol: str,
        current_price: Decimal,
        equity: Decimal,
        decision_confidence: float = 1.0,
        stop_loss_distance: Decimal | None = None,
        take_profit_distance: Decimal | None = None,
        existing_positions: dict[str, float] | None = None,
    ) -> SizingResult:
        """Calculate position sizing from deterministic proposal context."""
        ...


@dataclass(frozen=True, slots=True)
class TradeIdeaSizingConfig:
    """Static, offline sizing inputs for proposer-generated trade ideas."""

    equity: Decimal = Decimal("10000")
    risk_budget: RiskBudget = DEFAULT_RISK_BUDGET
    position_sizing_config: PositionSizingConfig = field(default_factory=PositionSizingConfig)
    quantity_precision: Decimal = Decimal("0.00000001")
    notional_precision: Decimal = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class TradeIdeaSizingContext:
    """Point-in-time inputs extracted from a proposed trade idea."""

    symbol: str
    current_price: Decimal
    confidence_label: ConfidenceLabel
    stop_loss_distance: Decimal
    take_profit_distance: Decimal
    max_loss_pct: Decimal


@dataclass(frozen=True, slots=True)
class TradeIdeaSizingOutput:
    """Sizing recommendation plus reproducibility metadata."""

    recommendation: SizingRecommendation
    data_used: str
    estimated_loss_amount: Decimal
    estimated_loss_pct: Decimal
    # The cap actually applied: min(proposer max_loss_pct, active budget cap).
    # Persisted records must cite this, not the raw proposer config value.
    effective_max_loss_pct: Decimal


class TradeIdeaPositionSizingBridge:
    """Map PositionSizer output into trade-idea sizing fields."""

    def __init__(
        self,
        config: TradeIdeaSizingConfig | None = None,
        *,
        position_sizer: PositionSizerLike | None = None,
    ) -> None:
        self._config = config or TradeIdeaSizingConfig()
        self._position_sizer = position_sizer or PositionSizer(
            config=self._config.position_sizing_config
        )

    def recommend(self, context: TradeIdeaSizingContext) -> TradeIdeaSizingOutput:
        decision_confidence = _confidence_score(context.confidence_label)
        kelly_enabled = self._position_sizer.config.enable_kelly_sizing
        effective_max_loss_pct = _effective_max_loss_pct(
            context.max_loss_pct,
            self._config.risk_budget,
        )
        result = self._position_sizer.calculate_size(
            symbol=context.symbol,
            current_price=context.current_price,
            equity=self._config.equity,
            decision_confidence=decision_confidence,
            stop_loss_distance=context.stop_loss_distance,
            take_profit_distance=context.take_profit_distance,
        )
        capped = _apply_budget_cap(
            result,
            context,
            equity=self._config.equity,
            budget=self._config.risk_budget,
        )
        notional = capped.notional.quantize(self._config.notional_precision)
        quantity = (
            Decimal("0")
            if context.current_price <= 0
            else (notional / context.current_price).quantize(self._config.quantity_precision)
        )
        recommendation = SizingRecommendation(
            quantity=quantity,
            notional=notional,
            rationale=_sizing_rationale(
                result,
                capped,
                budget=self._config.risk_budget,
                effective_max_loss_pct=effective_max_loss_pct,
                kelly_enabled=kelly_enabled,
            ),
        )
        estimated_loss_amount = _estimated_loss_amount(
            notional,
            context.stop_loss_distance,
            context.current_price,
        ).quantize(self._config.notional_precision)
        estimated_loss_pct = _estimated_loss_pct(
            estimated_loss_amount,
            self._config.equity,
        ).quantize(Decimal("0.0001"))
        return TradeIdeaSizingOutput(
            recommendation=recommendation,
            data_used=_sizing_data_used(
                context,
                result,
                capped,
                equity=self._config.equity,
                budget=self._config.risk_budget,
                decision_confidence=decision_confidence,
                kelly_enabled=kelly_enabled,
            ),
            estimated_loss_amount=estimated_loss_amount,
            estimated_loss_pct=estimated_loss_pct,
            effective_max_loss_pct=effective_max_loss_pct,
        )


@dataclass(frozen=True, slots=True)
class _CappedSizing:
    notional: Decimal
    position_fraction: float
    budget_cap_applied: bool
    cap_fraction: float | None
    estimated_risk_fraction: float


def _apply_budget_cap(
    result: SizingResult,
    context: TradeIdeaSizingContext,
    *,
    equity: Decimal,
    budget: RiskBudget,
) -> _CappedSizing:
    notional = result.position_value
    cap_fraction = _budget_cap_fraction(context, budget)
    budget_cap_applied = (
        cap_fraction is not None
        and budget.sizing_capped_by_budget
        and result.position_fraction > cap_fraction
    )
    if budget_cap_applied and cap_fraction is not None:
        notional = equity * Decimal(str(cap_fraction))
        position_fraction = cap_fraction
    else:
        position_fraction = result.position_fraction

    return _CappedSizing(
        notional=notional,
        position_fraction=position_fraction,
        budget_cap_applied=budget_cap_applied,
        cap_fraction=cap_fraction,
        estimated_risk_fraction=_estimated_risk_fraction(
            notional,
            context.stop_loss_distance,
            context.current_price,
            equity,
        ),
    )


def _budget_cap_fraction(
    context: TradeIdeaSizingContext,
    budget: RiskBudget,
) -> float | None:
    if not budget.sizing_capped_by_budget:
        return None
    if context.current_price <= 0 or context.stop_loss_distance <= 0:
        return None

    stop_fraction = context.stop_loss_distance / context.current_price
    if stop_fraction <= 0:
        return None
    max_loss_fraction = pct_points_to_fraction(
        _effective_max_loss_pct(context.max_loss_pct, budget)
    )
    loss_cap_fraction = max_loss_fraction / stop_fraction
    notional_cap_fraction = pct_points_to_fraction(budget.max_open_notional_pct)
    return float(min(loss_cap_fraction, notional_cap_fraction))


def _effective_max_loss_pct(max_loss_pct: Decimal, budget: RiskBudget) -> Decimal:
    return min(max_loss_pct, budget.max_loss_per_idea_pct)


def _estimated_risk_fraction(
    notional: Decimal,
    stop_loss_distance: Decimal,
    current_price: Decimal,
    equity: Decimal,
) -> float:
    if equity <= 0 or current_price <= 0:
        return 0.0
    return float(
        pct_points_to_fraction(
            _estimated_loss_pct(notional * stop_loss_distance / current_price, equity)
        )
    )


def _estimated_loss_amount(
    notional: Decimal,
    stop_loss_distance: Decimal,
    current_price: Decimal,
) -> Decimal:
    if current_price <= 0:
        return Decimal("0")
    return notional * stop_loss_distance / current_price


def _estimated_loss_pct(loss_amount: Decimal, equity: Decimal) -> Decimal:
    if equity <= 0:
        return Decimal("0")
    return fraction_to_pct_points(loss_amount / equity)


def _confidence_score(label: ConfidenceLabel) -> float:
    return {
        ConfidenceLabel.LOW: 0.35,
        ConfidenceLabel.MEDIUM: 0.65,
        ConfidenceLabel.HIGH: 0.9,
    }[label]


def _sizing_rationale(
    result: SizingResult,
    capped: _CappedSizing,
    *,
    budget: RiskBudget,
    effective_max_loss_pct: Decimal,
    kelly_enabled: bool,
) -> str:
    cap_status = "applied" if capped.budget_cap_applied else "not_applied"
    if not budget.sizing_capped_by_budget:
        cap_status = "disabled"
    return (
        f"PositionSizer notional={capped.notional:.2f} "
        f"({capped.position_fraction:.4%} of equity); "
        f"regime={result.regime} regime_factor={result.regime_factor:.4f}; "
        f"volatility_factor={result.volatility_factor:.4f}; "
        f"confidence_factor={result.confidence_factor:.4f}; "
        f"kelly_factor={result.kelly_factor:.4f} "
        f"(kelly_enabled={str(kelly_enabled).lower()}); "
        f"risk_budget_version={budget.version} max_loss_cap={effective_max_loss_pct}% "
        f"budget_cap={cap_status}; {result.reasoning}"
    )


def _sizing_data_used(
    context: TradeIdeaSizingContext,
    result: SizingResult,
    capped: _CappedSizing,
    *,
    equity: Decimal,
    budget: RiskBudget,
    decision_confidence: float,
    kelly_enabled: bool,
) -> str:
    cap_fraction = "none" if capped.cap_fraction is None else f"{capped.cap_fraction:.8f}"
    return (
        f"sizing:{context.symbol}:engine={POSITION_SIZER_BRIDGE_VERSION}:"
        f"equity={equity}:current_price={context.current_price}:"
        f"stop_distance={context.stop_loss_distance}:"
        f"take_profit_distance={context.take_profit_distance}:"
        f"decision_confidence={decision_confidence:.4f}:"
        f"regime={result.regime}:regime_factor={result.regime_factor:.4f}:"
        f"volatility_factor={result.volatility_factor:.4f}:"
        f"confidence_factor={result.confidence_factor:.4f}:"
        f"kelly_factor={result.kelly_factor:.4f}:"
        f"kelly_enabled={str(kelly_enabled).lower()}:"
        f"risk_budget_version={budget.version}:"
        f"max_loss_cap_pct={_effective_max_loss_pct(context.max_loss_pct, budget)}:"
        f"sizing_capped_by_budget={str(budget.sizing_capped_by_budget).lower()}:"
        f"budget_cap_applied={str(capped.budget_cap_applied).lower()}:"
        f"cap_fraction={cap_fraction}:"
        f"estimated_risk_fraction={capped.estimated_risk_fraction:.8f}"
    )


__all__ = [
    "POSITION_SIZER_BRIDGE_VERSION",
    "PositionSizerLike",
    "TradeIdeaPositionSizingBridge",
    "TradeIdeaSizingConfig",
    "TradeIdeaSizingContext",
    "TradeIdeaSizingOutput",
]
