"""Tests for EnsembleOrchestrator crisis-mode behavior."""

from gpt_trader.features.intelligence.ensemble.models import EnsembleConfig
from gpt_trader.features.intelligence.ensemble.orchestrator import EnsembleOrchestrator
from gpt_trader.features.intelligence.regime import RegimeType
from gpt_trader.features.intelligence.regime.models import RegimeState
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision

from .orchestrator_test_base import MockStrategy


class TestCrisisMode:
    def test_crisis_adjustment_applied(self) -> None:
        """Test that crisis mode adjustment is applied when regime is crisis."""
        crisis_config = EnsembleConfig(
            voting_method="confidence_leader",
            crisis_behavior="scaled_down",
            crisis_scale_factor=0.2,
            min_ensemble_confidence=0.1,  # Low threshold for testing
        )

        strategies = {
            "buyer": MockStrategy("buyer", Action.BUY, 0.9),
        }

        orchestrator = EnsembleOrchestrator(
            strategies=strategies,  # type: ignore[arg-type]
            config=crisis_config,
        )

        crisis_state = RegimeState(
            regime=RegimeType.CRISIS,
            confidence=0.9,
            trend_score=-0.8,
            volatility_percentile=0.95,
            momentum_score=-0.9,
        )

        decision = Decision(Action.BUY, "Test buy", 0.9, {})
        adjusted = orchestrator._apply_crisis_adjustment(
            decision=decision,
            regime_state=crisis_state,
            position_state=None,
        )

        assert adjusted.indicators.get("crisis_mode", False)
        assert adjusted.confidence < decision.confidence
