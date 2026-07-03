"""Default-off bridge from strategy decisions to trade-idea proposals.

The adapter is intentionally outside ``features.trade_ideas`` so the
broker-neutral trade-idea slice does not import live strategy modules. It maps
an existing strategy signal into a complete ``TradeIdea`` and, when requested,
submits that idea through ``TradeIdeaService.propose`` only.

Sizing is advisory by default. When a ``TradeIdeaPositionSizingBridge`` is
injected, mapped ideas instead carry executable sizing (positive quantity and
notional, estimated ``max_loss.amount``) computed against the same offline
equity/budget inputs ``BaselineProposer`` uses — never live account state.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas import (
    ActorType,
    AutonomyMode,
    Confidence,
    ConfidenceLabel,
    EntryZone,
    MaxLoss,
    ProductType,
    SizingRecommendation,
    TimeHorizon,
    TradeDirection,
    TradeIdea,
    TradeIdeaPositionSizingBridge,
    TradeIdeaService,
    TradeIdeaSizingContext,
    TradeIdeaView,
    evaluate_eligibility,
)

_SAFE_SLUG = re.compile(r"[^a-z0-9._-]+")


@runtime_checkable
class StrategyDecisionSignal(Protocol):
    """Structural strategy decision shape accepted by the adapter.

    Members are read-only (the adapter only ever reads a decision), which keeps
    the protocol covariant so a concrete ``Decision`` with a mutable
    ``dict[str, Any]`` ``indicators`` field satisfies it.
    """

    @property
    def action(self) -> Any: ...

    @property
    def reason(self) -> str: ...

    @property
    def confidence(self) -> float: ...

    @property
    def indicators(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class StrategySignalContext:
    """Point-in-time context required to turn one strategy signal into an idea."""

    symbol: str
    current_mark: Decimal
    as_of: datetime
    strategy_name: str
    data_source: str = "strategy:decision"
    product_type: ProductType = ProductType.SPOT


@dataclass(frozen=True, slots=True)
class StrategySignalToTradeIdeaAdapterConfig:
    """Configuration for the proposal-only strategy-signal adapter."""

    enabled: bool = False
    proposer_id_prefix: str = "strategy-signal"
    risk_per_idea_pct: Decimal = Decimal("2")
    entry_band_pct: Decimal = Decimal("1")
    stop_loss_pct: Decimal = Decimal("2")
    reward_multiple: Decimal = Decimal("2")
    expiry_hours: int = 48
    expected_hold: str = "1-5 days"
    price_precision: Decimal = Decimal("0.01")


class StrategySignalToTradeIdeaAdapter:
    """Map strategy decisions to proposed trade ideas without execution side effects."""

    def __init__(
        self,
        config: StrategySignalToTradeIdeaAdapterConfig | None = None,
        *,
        sizing_bridge: TradeIdeaPositionSizingBridge | None = None,
    ) -> None:
        self._config = config or StrategySignalToTradeIdeaAdapterConfig()
        self._sizing_bridge = sizing_bridge

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def actor_id(self, strategy_name: str) -> str:
        """Stable AI actor id for a strategy, independent of any single signal."""
        return f"{_safe_slug(self._config.proposer_id_prefix)}-{_safe_slug(strategy_name)}"

    def proposer_id(self, context: StrategySignalContext) -> str:
        """Return the stable AI actor id recorded on proposal events."""
        return self.actor_id(context.strategy_name)

    def map_decision(
        self,
        decision: StrategyDecisionSignal,
        context: StrategySignalContext,
    ) -> TradeIdea | None:
        """Convert a strategy decision into a complete idea when enabled/actionable."""
        if not self._config.enabled:
            return None

        self._validate_context(context)
        action = _action_value(decision.action)
        if action != "buy":
            return None

        confidence_value = _confidence_value(decision.confidence)
        idea = self._build_long_idea(
            decision=decision,
            context=context,
            confidence_value=confidence_value,
        )
        gaps = evaluate_eligibility(idea)
        if gaps:
            raise ValidationError(
                "StrategySignalToTradeIdeaAdapter produced an ineligible idea: " + "; ".join(gaps)
            )
        return idea

    def propose_decision(
        self,
        decision: StrategyDecisionSignal,
        context: StrategySignalContext,
        service: TradeIdeaService,
    ) -> TradeIdeaView | None:
        """Submit a mapped idea through ``TradeIdeaService.propose`` only."""
        idea = self.map_decision(decision, context)
        if idea is None:
            return None

        return service.propose(
            idea,
            actor_id=self.proposer_id(context),
            actor_type=ActorType.AI,
            reason="Strategy signal mapped to a human-review trade idea",
            evidence=idea.data_used,
        )

    def _build_long_idea(
        self,
        *,
        decision: StrategyDecisionSignal,
        context: StrategySignalContext,
        confidence_value: float,
    ) -> TradeIdea:
        config = self._config
        as_of = _utc_aware(context.as_of)
        mark = context.current_mark
        stop_level = (mark * (1 - config.stop_loss_pct / 100)).quantize(config.price_precision)
        entry_lower = (mark * (1 - config.entry_band_pct / 100)).quantize(config.price_precision)
        entry_upper = (mark * (1 + config.entry_band_pct / 100)).quantize(config.price_precision)
        target = (mark + config.reward_multiple * (mark - stop_level)).quantize(
            config.price_precision
        )
        self._validate_price_levels(
            stop_level=stop_level,
            entry_lower=entry_lower,
            entry_upper=entry_upper,
            target=target,
        )
        reason = _idea_reason(decision, context)
        confidence = _confidence(confidence_value)
        data_used = self._data_used(decision, context, as_of, confidence_value)

        if self._sizing_bridge is None:
            max_loss = MaxLoss(
                percent_of_account=config.risk_per_idea_pct,
                assumptions=(
                    f"Strategy bridge uses a {config.stop_loss_pct}% stop from current mark {mark}",
                    "Sizing remains advisory until a human approves the idea",
                ),
            )
            sizing_recommendation = SizingRecommendation(
                rationale=(
                    f"Size so a stop-out near {stop_level} risks no more than "
                    f"{config.risk_per_idea_pct}% of account equity"
                ),
            )
        else:
            # Size against the worst permitted long entry (the top of the entry
            # zone): a fill at entry_upper is explicitly allowed, so the
            # persisted risk snapshot must not assume a cheaper fill at the mark.
            sizing = self._sizing_bridge.recommend(
                TradeIdeaSizingContext(
                    symbol=context.symbol,
                    current_price=entry_upper,
                    confidence_label=confidence.label,
                    stop_loss_distance=entry_upper - stop_level,
                    take_profit_distance=target - entry_upper,
                    max_loss_pct=config.risk_per_idea_pct,
                )
            )
            stop_distance_pct = ((entry_upper - stop_level) / entry_upper * 100).quantize(
                Decimal("0.01")
            )
            max_loss = MaxLoss(
                amount=sizing.estimated_loss_amount,
                percent_of_account=sizing.estimated_loss_pct,
                assumptions=(
                    f"Strategy bridge uses a {config.stop_loss_pct}% stop from current mark {mark}",
                    f"PositionSizer notional {sizing.recommendation.notional} implies "
                    f"an estimated stop-out loss of {sizing.estimated_loss_amount}",
                    f"Risk budget cap remains {sizing.effective_max_loss_pct}% per idea",
                    f"Sized at the worst permitted entry {entry_upper}; stop distance "
                    f"is {stop_distance_pct}% from there",
                ),
            )
            sizing_recommendation = sizing.recommendation
            data_used = (*data_used, sizing.data_used)

        return TradeIdea(
            decision_id=self._decision_id(decision, context, as_of),
            autonomy_mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
            thesis=(
                f"{context.strategy_name} emitted a buy signal for {context.symbol}: " f"{reason}"
            ),
            instrument=context.symbol,
            product_type=context.product_type,
            direction=TradeDirection.LONG,
            entry_zone=EntryZone(lower=entry_lower, upper=entry_upper),
            invalidation=f"Close below the strategy stop level {stop_level}",
            target_exit=f"Take profit near {target} or exit at expiry",
            max_loss=max_loss,
            sizing_recommendation=sizing_recommendation,
            time_horizon=TimeHorizon(
                expected_hold=config.expected_hold,
                expires_at=as_of + timedelta(hours=config.expiry_hours),
            ),
            data_used=data_used,
            confidence=confidence,
            failure_mode=(
                "Strategy signal fails if price rejects the entry zone and closes below "
                "the mapped invalidation level"
            ),
            do_not_trade_if=(
                f"Price has already moved above {entry_upper} before human review",
                "The source strategy has reversed or emitted hold before approval",
            ),
        )

    def _data_used(
        self,
        decision: StrategyDecisionSignal,
        context: StrategySignalContext,
        as_of: datetime,
        confidence_value: float,
    ) -> tuple[str, ...]:
        action = _action_value(decision.action)
        return (
            (
                f"{context.data_source}:{context.symbol}:"
                f"mark={_canonical_decimal(context.current_mark)}:"
                f"as_of={as_of.isoformat()}"
            ),
            (
                f"strategy:{context.strategy_name}:action={action}:"
                f"confidence={confidence_value:.4f}"
            ),
        )

    def _decision_id(
        self,
        decision: StrategyDecisionSignal,
        context: StrategySignalContext,
        as_of: datetime,
    ) -> str:
        action = _action_value(decision.action)
        strategy_slug = _safe_slug(context.strategy_name)
        symbol_slug = _safe_slug(context.symbol)
        digest = hashlib.sha256(
            "|".join(
                (
                    self.proposer_id(context),
                    context.symbol,
                    _canonical_decimal(context.current_mark),
                    as_of.isoformat(),
                    action,
                    _idea_reason(decision, context),
                )
            ).encode()
        ).hexdigest()[:8]
        return f"trade-{as_of:%Y%m%d}-{strategy_slug}-{symbol_slug}-{digest}"

    def _validate_price_levels(
        self,
        *,
        stop_level: Decimal,
        entry_lower: Decimal,
        entry_upper: Decimal,
        target: Decimal,
    ) -> None:
        """Reject ideas where ``price_precision`` is too coarse for the mark.

        For sub-cent symbols the default ``price_precision`` quantizes stop/entry/
        target to ``0.00`` (or collapses them together), which ``evaluate_eligibility``
        cannot detect because the bounds are still non-``None``. Reject here so a
        degenerate, zero-priced idea is never proposed; callers can pass a finer
        ``price_precision`` for low-priced symbols.
        """
        if any(level <= 0 for level in (stop_level, entry_lower, entry_upper, target)):
            raise ValidationError(
                "price_precision is too coarse for current_mark; "
                "quantization erased a price level",
                field="price_precision",
                value=str(self._config.price_precision),
            )
        if not stop_level < entry_lower < entry_upper < target:
            raise ValidationError(
                "price_precision is too coarse to keep stop/entry/target levels distinct",
                field="price_precision",
                value=str(self._config.price_precision),
            )

    def _validate_context(self, context: StrategySignalContext) -> None:
        if not context.symbol.strip():
            raise ValidationError("symbol must be non-empty", field="symbol")
        if not context.strategy_name.strip():
            raise ValidationError("strategy_name must be non-empty", field="strategy_name")
        if context.current_mark <= 0:
            raise ValidationError(
                "current_mark must be positive",
                field="current_mark",
                value=str(context.current_mark),
            )
        if context.product_type is not ProductType.SPOT:
            raise ValidationError(
                "StrategySignalToTradeIdeaAdapter currently supports spot ideas only",
                field="product_type",
                value=context.product_type.value,
            )


def _safe_slug(value: str) -> str:
    slug = _SAFE_SLUG.sub("-", value.strip().lower()).strip("-")
    return slug or "unknown"


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("as_of must be timezone-aware", field="as_of")
    return value.astimezone(UTC)


def _idea_reason(decision: StrategyDecisionSignal, context: StrategySignalContext) -> str:
    return decision.reason.strip() or f"{context.strategy_name} emitted a buy signal"


def _canonical_decimal(value: Decimal) -> str:
    """Render a decimal so equal values share one string (e.g. 60000 == 60000.00)."""
    normalized = value.normalize()
    # ``normalize`` can yield exponent form (6E+4); ``format(..., "f")`` keeps it plain
    # while staying canonical, and preserves sub-cent precision (0.00001234).
    return format(normalized, "f")


def _action_value(action: Any) -> str:
    value = getattr(action, "value", action)
    return str(value).strip().lower()


def _confidence_value(value: float) -> float:
    confidence = float(value)
    if not math.isfinite(confidence):
        raise ValidationError("confidence must be finite", field="confidence", value=value)
    return max(0.0, min(1.0, confidence))


def _confidence(value: float) -> Confidence:
    if value >= 0.75:
        label = ConfidenceLabel.HIGH
    elif value >= 0.5:
        label = ConfidenceLabel.MEDIUM
    else:
        label = ConfidenceLabel.LOW
    return Confidence(
        label=label,
        rationale=f"Mapped from strategy confidence {value:.4f}",
    )
