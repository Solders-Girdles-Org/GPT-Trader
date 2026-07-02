"""Tests for WeightedMajorityVoting."""

import pytest

from gpt_trader.features.intelligence.ensemble.models import StrategyVote
from gpt_trader.features.intelligence.ensemble.voting import WeightedMajorityVoting
from gpt_trader.features.intelligence.regime.models import RegimeState, RegimeType
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision


@pytest.fixture
def regime_state() -> RegimeState:
    """Create a test regime state."""
    return RegimeState(
        regime=RegimeType.BULL_QUIET,
        confidence=0.8,
        trend_score=0.5,
        volatility_percentile=0.3,
        momentum_score=0.4,
    )


class TestWeightedMajorityVoting:
    """Test WeightedMajorityVoting."""

    @pytest.fixture
    def voter(self) -> WeightedMajorityVoting:
        """Create voter instance."""
        return WeightedMajorityVoting()

    def test_empty_votes_returns_hold(
        self, voter: WeightedMajorityVoting, regime_state: RegimeState
    ):
        """Test that empty votes returns HOLD."""
        decision = voter.aggregate([], regime_state)
        assert decision.action == Action.HOLD
        assert decision.confidence == 0.0

    def test_unanimous_buy(self, voter: WeightedMajorityVoting, regime_state: RegimeState):
        """Test unanimous BUY votes."""
        votes = [
            StrategyVote(
                strategy_name="strategy_a",
                decision=Decision(Action.BUY, "Buy signal", 0.8, {}),
                weight=0.5,
            ),
            StrategyVote(
                strategy_name="strategy_b",
                decision=Decision(Action.BUY, "Buy signal", 0.7, {}),
                weight=0.5,
            ),
        ]

        decision = voter.aggregate(votes, regime_state)
        assert decision.action == Action.BUY
        assert decision.confidence > 0.5

    def test_weighted_aggregation(self, voter: WeightedMajorityVoting, regime_state: RegimeState):
        """Test that weights affect outcome."""
        votes = [
            StrategyVote(
                strategy_name="high_weight",
                decision=Decision(Action.BUY, "Buy signal", 0.8, {}),
                weight=0.9,
            ),
            StrategyVote(
                strategy_name="low_weight",
                decision=Decision(Action.SELL, "Sell signal", 0.9, {}),
                weight=0.1,
            ),
        ]

        decision = voter.aggregate(votes, regime_state)
        # High weight BUY should win despite lower confidence
        assert decision.action == Action.BUY

    def test_equal_weights_confidence_matters(
        self, voter: WeightedMajorityVoting, regime_state: RegimeState
    ):
        """Test that confidence matters with equal weights."""
        votes = [
            StrategyVote(
                strategy_name="high_conf",
                decision=Decision(Action.BUY, "Buy signal", 0.9, {}),
                weight=0.5,
            ),
            StrategyVote(
                strategy_name="low_conf",
                decision=Decision(Action.SELL, "Sell signal", 0.3, {}),
                weight=0.5,
            ),
        ]

        decision = voter.aggregate(votes, regime_state)
        # Higher confidence BUY should win
        assert decision.action == Action.BUY
