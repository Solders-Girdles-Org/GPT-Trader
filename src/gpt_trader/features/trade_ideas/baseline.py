"""Deterministic baseline proposer: the benchmark every fancier proposer must beat.

Long-only moving-average crossover on point-in-time snapshots. Intentionally
simple and fully deterministic — identical snapshots produce byte-identical
records (stable decision ids and record hashes), which makes replay scoring
and proposer-vs-proposer comparison trivial. If a future LLM proposer cannot
outscore this on the same replayed snapshots, it is noise.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gpt_trader.errors import ValidationError
from gpt_trader.features.trade_ideas.eligibility import evaluate_eligibility
from gpt_trader.features.trade_ideas.models import (
    AutonomyMode,
    Confidence,
    ConfidenceLabel,
    EntryZone,
    ExitPlan,
    MaxLoss,
    ProductType,
    TimeHorizon,
    TradeDirection,
    TradeIdea,
)
from gpt_trader.features.trade_ideas.sizing import (
    TradeIdeaPositionSizingBridge,
    TradeIdeaSizingConfig,
    TradeIdeaSizingContext,
)
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot, SymbolSeries

# Per-run hook letting wrapping proposers adjust confidence before sizing.
ConfidenceOverlay = Callable[[str, Confidence], Confidence]


@dataclass(frozen=True, slots=True)
class BaselineProposerConfig:
    short_window: int = 10
    long_window: int = 50
    crossover_lookback: int = 3
    risk_per_idea_pct: Decimal = Decimal("2")
    entry_band_pct: Decimal = Decimal("1")
    reward_multiple: Decimal = Decimal("2")
    expiry_hours: int = 48
    expected_hold: str = "5-15 days"
    price_precision: Decimal = Decimal("0.01")
    sizing_config: TradeIdeaSizingConfig = field(default_factory=TradeIdeaSizingConfig)


def _moving_average(closes: list[Decimal], window: int, end_index: int) -> Decimal:
    start = end_index - window + 1
    return sum(closes[start : end_index + 1], Decimal("0")) / Decimal(window)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


class BaselineProposer:
    """Long-only MA-crossover proposer over spot symbols in a snapshot."""

    def __init__(
        self,
        config: BaselineProposerConfig | None = None,
        *,
        sizing_bridge: TradeIdeaPositionSizingBridge | None = None,
    ) -> None:
        self._config = config or BaselineProposerConfig()
        self._sizing_bridge = sizing_bridge or TradeIdeaPositionSizingBridge(
            self._config.sizing_config
        )

    @property
    def proposer_id(self) -> str:
        return f"baseline-ma-{self._config.short_window}-{self._config.long_window}"

    def propose(
        self,
        snapshot: MarketSnapshot,
        *,
        confidence_overlay: ConfidenceOverlay | None = None,
    ) -> list[TradeIdea]:
        ideas: list[TradeIdea] = []
        for series in snapshot.series:
            idea = self._propose_for_series(snapshot, series, confidence_overlay=confidence_overlay)
            if idea is not None:
                ideas.append(idea)
        return ideas

    def _propose_for_series(
        self,
        snapshot: MarketSnapshot,
        series: SymbolSeries,
        *,
        confidence_overlay: ConfidenceOverlay | None = None,
    ) -> TradeIdea | None:
        config = self._config
        as_of = _utc_aware(snapshot.as_of)
        required = config.long_window + config.crossover_lookback
        if len(series.candles) < required:
            return None

        closes = [candle.close for candle in series.candles]
        last = len(closes) - 1
        short_now = _moving_average(closes, config.short_window, last)
        long_now = _moving_average(closes, config.long_window, last)
        if short_now <= long_now:
            return None

        crossed_recently = any(
            _moving_average(closes, config.short_window, last - offset)
            <= _moving_average(closes, config.long_window, last - offset)
            for offset in range(1, config.crossover_lookback + 1)
        )
        if not crossed_recently:
            return None

        close = closes[-1]
        stop_level = long_now.quantize(config.price_precision)
        if close <= stop_level:
            return None

        entry_lower = (close * (1 - config.entry_band_pct / 100)).quantize(config.price_precision)
        entry_upper = (close * (1 + config.entry_band_pct / 100)).quantize(config.price_precision)
        target = (close + config.reward_multiple * (close - stop_level)).quantize(
            config.price_precision
        )
        # On low-priced symbols the price precision can round the stop, entry
        # midpoint, and target into a set with no defined risk per unit (e.g.
        # a $0.82 close within half a cent of the long average at 0.01
        # precision). A trade whose stop is not strictly below the entry
        # midpoint and target is not proposable.
        entry_midpoint = (entry_lower + entry_upper) / 2
        if not stop_level < entry_midpoint < target:
            return None
        # Size against the worst permitted long entry (the top of the entry
        # zone): a fill at entry_upper is explicitly allowed, so the persisted
        # risk snapshot must not assume a cheaper fill at the last close.
        stop_distance_pct = ((entry_upper - stop_level) / entry_upper * 100).quantize(
            Decimal("0.01")
        )

        volumes = [candle.volume for candle in series.candles[-config.long_window :]]
        average_volume = sum(volumes, Decimal("0")) / Decimal(len(volumes))
        volume_confirmed = series.candles[-1].volume > average_volume
        confidence = Confidence(
            label=ConfidenceLabel.MEDIUM if volume_confirmed else ConfidenceLabel.LOW,
            rationale=(
                "Latest volume is above the long-window average, supporting the move"
                if volume_confirmed
                else "Crossover lacks volume confirmation; treat as a weaker signal"
            ),
        )
        # Overlays (e.g. the regime-aware proposer) must adjust the visible
        # confidence BEFORE sizing so the decision-confidence factor and the
        # persisted confidence label agree.
        if confidence_overlay is not None:
            confidence = confidence_overlay(series.symbol, confidence)
        sizing = self._sizing_bridge.recommend(
            TradeIdeaSizingContext(
                symbol=series.symbol,
                current_price=entry_upper,
                confidence_label=confidence.label,
                stop_loss_distance=entry_upper - stop_level,
                take_profit_distance=target - entry_upper,
                max_loss_pct=config.risk_per_idea_pct,
            )
        )

        idea = TradeIdea(
            decision_id=self._decision_id(as_of, series.symbol),
            autonomy_mode=AutonomyMode.HUMAN_APPROVED_EXECUTION,
            thesis=(
                f"{series.symbol} {config.short_window}-bar average crossed above the "
                f"{config.long_window}-bar average within the last "
                f"{config.crossover_lookback} bars and closed at {close} above the "
                f"long-term average {stop_level}, signalling upward momentum"
            ),
            instrument=series.symbol,
            product_type=ProductType.SPOT,
            direction=TradeDirection.LONG,
            entry_zone=EntryZone(lower=entry_lower, upper=entry_upper),
            invalidation=f"Close below the {config.long_window}-bar average ({stop_level})",
            target_exit=(
                f"Take profit near {target} ({config.reward_multiple}R) or exit at expiry"
            ),
            max_loss=MaxLoss(
                amount=sizing.estimated_loss_amount,
                percent_of_account=sizing.estimated_loss_pct,
                assumptions=(
                    f"PositionSizer notional {sizing.recommendation.notional} implies "
                    f"an estimated stop-out loss of {sizing.estimated_loss_amount}",
                    f"Risk budget cap remains {sizing.effective_max_loss_pct}% per idea",
                    f"Sized at the worst permitted entry {entry_upper}; stop distance "
                    f"is {stop_distance_pct}% from there",
                ),
            ),
            sizing_recommendation=sizing.recommendation,
            time_horizon=TimeHorizon(
                expected_hold=config.expected_hold,
                expires_at=as_of + timedelta(hours=config.expiry_hours),
            ),
            data_used=(
                f"{snapshot.source}:{series.symbol}:{series.granularity}"
                f":as_of={as_of.isoformat()}",
                sizing.data_used,
            ),
            confidence=confidence,
            failure_mode=(
                "False breakout: the short-term average rolls back below the "
                "long-term average shortly after entry"
            ),
            do_not_trade_if=(
                f"Price has already moved above {entry_upper} before entry",
                "The crossover has reversed on the most recent completed bar",
            ),
            # Machine-readable levels so a filled position can be resolved
            # against later marks (issue #1218); the same values the free-text
            # invalidation/target_exit above describe.
            exit_plan=ExitPlan(stop=stop_level, target=target),
        )

        gaps = evaluate_eligibility(idea)
        if gaps:
            raise ValidationError(
                f"BaselineProposer produced an ineligible idea for '{series.symbol}': "
                + "; ".join(gaps)
            )
        return idea

    def _decision_id(self, as_of: datetime, symbol: str) -> str:
        digest = hashlib.sha256(
            f"{self.proposer_id}|{symbol}|{as_of.isoformat()}".encode()
        ).hexdigest()[:8]
        symbol_slug = symbol.lower().replace("-", "")
        return f"trade-{as_of:%Y%m%d}-{symbol_slug}-{digest}"
