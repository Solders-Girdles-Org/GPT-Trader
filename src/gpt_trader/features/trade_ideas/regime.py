"""Regime-aware trade-idea proposer.

The proposer keeps the deterministic moving-average baseline as the signal
source, then overlays point-in-time market regime context from the intelligence
slice before records enter the human approval queue.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
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
)
from gpt_trader.features.trade_ideas.eligibility import evaluate_eligibility
from gpt_trader.features.trade_ideas.models import (
    Confidence,
    ConfidenceLabel,
    SizingRecommendation,
    TradeIdea,
)
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
    """Configuration for the regime-aware MA proposer."""

    baseline_config: BaselineProposerConfig = field(default_factory=BaselineProposerConfig)
    regime_config: RegimeConfig = field(default_factory=RegimeConfig)
    suppressed_regimes: tuple[RegimeType, ...] = DEFAULT_SUPPRESSED_REGIMES


class RegimeAwareProposer:
    """MA-crossover proposer enriched with MarketRegimeDetector state."""

    def __init__(
        self,
        config: RegimeAwareProposerConfig | None = None,
        *,
        detector_factory: RegimeDetectorFactory = MarketRegimeDetector,
    ) -> None:
        self._config = config or RegimeAwareProposerConfig()
        self._baseline = BaselineProposer(self._config.baseline_config)
        self._detector_factory = detector_factory
        self._config_fingerprint = _regime_config_fingerprint(self._config.regime_config)

    @property
    def proposer_id(self) -> str:
        baseline = self._config.baseline_config
        return f"regime-aware-ma-{baseline.short_window}-{baseline.long_window}"

    def propose(self, snapshot: MarketSnapshot) -> list[TradeIdea]:
        states = self._regime_states(snapshot)
        ideas: list[TradeIdea] = []
        for idea in self._baseline.propose(snapshot):
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
        digest = hashlib.sha256(
            (
                f"{self.proposer_id}|{symbol}|{as_of.isoformat()}|" f"{self._config_fingerprint}"
            ).encode()
        ).hexdigest()[:8]
        symbol_slug = symbol.lower().replace("-", "")
        return f"trade-{as_of:%Y%m%d}-{symbol_slug}-{digest}"


def _detect_series_regime(detector: RegimeDetector, series: SymbolSeries) -> RegimeState:
    state = RegimeState.unknown()
    for candle in series.candles:
        state = detector.update(series.symbol, candle.close)
    return state


def _regime_config_fingerprint(config: RegimeConfig) -> str:
    payload = json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":"))
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
