"""Regime-aware trade-idea proposer.

The proposer keeps the deterministic moving-average baseline as the signal
source, then overlays point-in-time market regime context from the intelligence
slice before records enter the human approval queue.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from gpt_trader.errors import ValidationError
from gpt_trader.features.intelligence.regime import (
    MarketRegimeDetector,
    RegimeConfig,
    RegimeState,
    RegimeType,
)
from gpt_trader.features.trade_ideas.baseline import (
    BaselineProposer,
    BaselineProposerConfig,
    ExitLevels,
)
from gpt_trader.features.trade_ideas.eligibility import evaluate_eligibility
from gpt_trader.features.trade_ideas.models import (
    Confidence,
    ConfidenceLabel,
    SizingRecommendation,
    TradeIdea,
)
from gpt_trader.features.trade_ideas.sizing import TradeIdeaPositionSizingBridge
from gpt_trader.features.trade_ideas.snapshot import MarketSnapshot, SymbolSeries

REGIME_DETECTOR_VERSION = "market-regime-detector-v1"
DEFAULT_SUPPRESSED_REGIMES = (RegimeType.CRISIS, RegimeType.BEAR_VOLATILE)


class RegimeDetector(Protocol):
    """Minimal detector surface used by the proposer."""

    config: RegimeConfig

    def update(self, symbol: str, price: Decimal) -> RegimeState:
        """Update point-in-time regime state for one completed candle close."""
        ...


RegimeDetectorFactory = Callable[[RegimeConfig], RegimeDetector]


@dataclass(frozen=True, slots=True)
class RegimeAwareProposerConfig:
    """Configuration for the regime-aware MA proposer.

    ``volatile_stop_distance_multiplier`` widens the baseline stop distance
    (anchored at the last close) when the detected regime is volatile, and
    ``volatile_reward_multiple`` optionally replaces the baseline reward
    multiple there — the regime overlay's replay-visible decision channel
    (#1242). A multiplier of 1 with no reward override reproduces baseline
    levels exactly.
    """

    baseline_config: BaselineProposerConfig = field(default_factory=BaselineProposerConfig)
    regime_config: RegimeConfig = field(default_factory=RegimeConfig)
    suppressed_regimes: tuple[RegimeType, ...] = DEFAULT_SUPPRESSED_REGIMES
    volatile_stop_distance_multiplier: Decimal = Decimal("1.5")
    volatile_reward_multiple: Decimal | None = None

    def __post_init__(self) -> None:
        if self.volatile_stop_distance_multiplier <= 0:
            raise ValidationError(
                "volatile_stop_distance_multiplier must be positive",
                field="volatile_stop_distance_multiplier",
            )
        if self.volatile_reward_multiple is not None and self.volatile_reward_multiple <= 0:
            raise ValidationError(
                "volatile_reward_multiple must be positive",
                field="volatile_reward_multiple",
            )


class RegimeAwareProposer:
    """MA-crossover proposer enriched with MarketRegimeDetector state."""

    def __init__(
        self,
        config: RegimeAwareProposerConfig | None = None,
        *,
        detector_factory: RegimeDetectorFactory = MarketRegimeDetector,
        sizing_bridge: TradeIdeaPositionSizingBridge | None = None,
    ) -> None:
        self._config = config or RegimeAwareProposerConfig()
        self._baseline = BaselineProposer(
            self._config.baseline_config,
            sizing_bridge=sizing_bridge,
        )
        self._detector_factory = detector_factory
        self._config_fingerprint = _regime_config_fingerprint(self._config.regime_config)
        self._identity_fingerprint = _proposer_config_fingerprint(self._config)

    @property
    def proposer_id(self) -> str:
        baseline = self._config.baseline_config
        return f"regime-aware-ma-{baseline.short_window}-{baseline.long_window}"

    def propose(self, snapshot: MarketSnapshot) -> list[TradeIdea]:
        states = self._regime_states(snapshot)

        def overlay_label(symbol: str, confidence: Confidence) -> Confidence:
            # Adjust only the label so sizing uses the post-overlay
            # decision-confidence; _enrich_idea appends the rationale once.
            state = states.get(symbol, RegimeState.unknown())
            return Confidence(
                label=_regime_confidence(confidence, state).label,
                rationale=confidence.rationale,
            )

        def overlay_exit(symbol: str, close: Decimal, levels: ExitLevels) -> ExitLevels:
            state = states.get(symbol, RegimeState.unknown())
            return _regime_exit_levels(levels, close=close, state=state, config=self._config)

        ideas: list[TradeIdea] = []
        for idea in self._baseline.propose(
            snapshot,
            confidence_overlay=overlay_label,
            exit_overlay=overlay_exit,
        ):
            state = states.get(idea.instrument, RegimeState.unknown())
            # UNKNOWN means the detector has not warmed up (long EMA plus
            # persistence ticks); a "regime-aware" idea with a 0.0-confidence
            # overlay would be self-contradictory, so treat it as unready
            # rather than persisting a proposal.
            if state.regime is RegimeType.UNKNOWN:
                continue
            if state.regime in self._config.suppressed_regimes:
                continue
            ideas.append(self._enrich_idea(snapshot, idea, state))
        return ideas

    def _regime_states(self, snapshot: MarketSnapshot) -> dict[str, RegimeState]:
        detector = self._detector_factory(self._config.regime_config)
        states: dict[str, RegimeState] = {}
        for series in snapshot.series:
            states[series.symbol] = _detect_series_regime(detector, series)
        return states

    def _enrich_idea(
        self,
        snapshot: MarketSnapshot,
        idea: TradeIdea,
        state: RegimeState,
    ) -> TradeIdea:
        enriched = replace(
            idea,
            decision_id=self._decision_id(_utc_aware(snapshot.as_of), idea.instrument),
            thesis=_regime_thesis(idea.thesis, idea.instrument, state),
            invalidation=_regime_invalidation(
                idea.invalidation,
                self._config.suppressed_regimes,
            ),
            data_used=(
                *idea.data_used,
                _regime_data_used(idea.instrument, state, self._config_fingerprint),
            ),
            confidence=_regime_confidence(idea.confidence, state),
            sizing_recommendation=_regime_sizing(idea.sizing_recommendation, state),
            do_not_trade_if=(
                *idea.do_not_trade_if,
                *_regime_suppression_do_not_trade_if(self._config.suppressed_regimes),
                "Regime confidence falls below 0.30 before entry",
            ),
        )
        gaps = evaluate_eligibility(enriched)
        if gaps:
            raise ValidationError(
                f"RegimeAwareProposer produced an ineligible idea for "
                f"'{idea.instrument}': " + "; ".join(gaps)
            )
        return enriched

    def _decision_id(self, as_of: datetime, symbol: str) -> str:
        # The digest must cover every output-affecting knob (baseline config,
        # suppression policy, detector config): two differently configured runs
        # over the same snapshot must never collide on decision_id.
        digest = hashlib.sha256(
            (
                f"{self.proposer_id}|{symbol}|{as_of.isoformat()}|" f"{self._identity_fingerprint}"
            ).encode()
        ).hexdigest()[:8]
        symbol_slug = symbol.lower().replace("-", "")
        return f"trade-{as_of:%Y%m%d}-{symbol_slug}-{digest}"


def _detect_series_regime(detector: RegimeDetector, series: SymbolSeries) -> RegimeState:
    state = RegimeState.unknown()
    for candle in series.candles:
        state = detector.update(series.symbol, candle.close)
    return state


def _regime_exit_levels(
    levels: ExitLevels,
    *,
    close: Decimal,
    state: RegimeState,
    config: RegimeAwareProposerConfig,
) -> ExitLevels:
    """Widen the stop distance (anchored at the close) in volatile regimes.

    UNKNOWN and quiet regimes pass baseline levels through untouched, so any
    level difference from baseline is attributable to a volatile
    classification at proposal time.
    """
    if state.regime is RegimeType.UNKNOWN or not state.is_volatile():
        return levels
    multiplier = config.volatile_stop_distance_multiplier
    reward_multiple = (
        config.volatile_reward_multiple
        if config.volatile_reward_multiple is not None
        else levels.reward_multiple
    )
    if multiplier == 1 and reward_multiple == levels.reward_multiple:
        return levels
    return ExitLevels(
        stop_level=close - multiplier * (close - levels.stop_level),
        reward_multiple=reward_multiple,
        stop_basis=(
            f"the volatility-adjusted stop ({multiplier}x the distance to " f"{levels.stop_basis})"
        ),
    )


def _regime_config_fingerprint(config: RegimeConfig) -> str:
    payload = json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _proposer_config_fingerprint(config: RegimeAwareProposerConfig) -> str:
    baseline = config.baseline_config
    payload = json.dumps(
        {
            "baseline": {spec.name: str(getattr(baseline, spec.name)) for spec in fields(baseline)},
            "regime": config.regime_config.to_dict(),
            "suppressed_regimes": [regime.name for regime in config.suppressed_regimes],
            "volatile_stop_distance_multiplier": str(config.volatile_stop_distance_multiplier),
            "volatile_reward_multiple": (
                str(config.volatile_reward_multiple)
                if config.volatile_reward_multiple is not None
                else None
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _regime_thesis(thesis: str, instrument: str, state: RegimeState) -> str:
    return (
        f"{thesis}. Regime overlay classified {instrument} as {state.regime.name} "
        f"(confidence {state.confidence:.2f}, trend {state.trend_score:.2f}, "
        f"volatility {state.volatility_percentile:.2f})."
    )


def _regime_names(regimes: tuple[RegimeType, ...]) -> str:
    return " or ".join(regime.name for regime in regimes)


def _regime_suppression_do_not_trade_if(regimes: tuple[RegimeType, ...]) -> tuple[str, ...]:
    names = _regime_names(regimes)
    if not names:
        return ()
    return (f"Regime overlay is {names} before review",)


def _regime_invalidation(invalidation: str, suppressed_regimes: tuple[RegimeType, ...]) -> str:
    names = _regime_names(suppressed_regimes)
    if not names:
        return invalidation
    return f"{invalidation}; invalidate before entry if regime overlay shifts to {names}"


def _regime_confidence(confidence: Confidence, state: RegimeState) -> Confidence:
    label = confidence.label
    if state.regime is RegimeType.BULL_QUIET and label is ConfidenceLabel.LOW:
        label = ConfidenceLabel.MEDIUM
    elif state.regime is RegimeType.UNKNOWN or state.is_bearish() or state.is_volatile():
        label = ConfidenceLabel.LOW

    return Confidence(
        label=label,
        rationale=(
            f"{confidence.rationale}. Regime overlay={state.regime.name}, "
            f"classifier_confidence={state.confidence:.2f}, "
            f"transition_probability={state.transition_probability:.2f}."
        ),
    )


def _regime_sizing(sizing: SizingRecommendation, state: RegimeState) -> SizingRecommendation:
    return replace(
        sizing,
        rationale=(
            f"{sizing.rationale}; regime overlay {state.regime.name} requires human "
            "review to confirm sizing remains appropriate before approval"
        ),
    )


def _regime_data_used(
    instrument: str,
    state: RegimeState,
    config_fingerprint: str,
) -> str:
    return (
        f"regime:{instrument}:detector={REGIME_DETECTOR_VERSION}:"
        f"config_sha256={config_fingerprint}:state={state.regime.name}:"
        f"confidence={state.confidence:.4f}:trend_score={state.trend_score:.4f}:"
        f"volatility_percentile={state.volatility_percentile:.4f}:"
        f"momentum_score={state.momentum_score:.4f}"
    )


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


__all__ = [
    "DEFAULT_SUPPRESSED_REGIMES",
    "REGIME_DETECTOR_VERSION",
    "RegimeAwareProposer",
    "RegimeAwareProposerConfig",
    "RegimeDetector",
    "RegimeDetectorFactory",
]
