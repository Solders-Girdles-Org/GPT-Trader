"""Tests for ConfidenceLeaderVoting."""

import pytest

from gpt_trader.features.intelligence.ensemble.models import StrategyVote
from gpt_trader.features.intelligence.ensemble.voting import ConfidenceLeaderVoting
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


class TestConfidenceLeaderVoting:
    """Test ConfidenceLeaderVoting."""

    @pytest.fixture
    def voter(self) -> ConfidenceLeaderVoting:
        """Create voter instance."""
        return ConfidenceLeaderVoting()

    def test_empty_votes_returns_hold(
        self, voter: ConfidenceLeaderVoting, regime_state: RegimeState
    ):
        """Test that empty votes returns HOLD."""
        decision = voter.aggregate([], regime_state)
        assert decision.action == Action.HOLD

    def test_unanimous_boosted_confidence(
        self, voter: ConfidenceLeaderVoting, regime_state: RegimeState
    ):
        """Test that unanimous agreement boosts confidence."""
        votes = [
            StrategyVote(
                strategy_name="strategy_a",
                decision=Decision(Action.BUY, "Buy signal", 0.7, {}),
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
        # Should be boosted from 0.7
        assert decision.confidence > 0.7
        assert decision.indicators.get("unanimous") is True

    def test_conflict_follows_leader(
        self, voter: ConfidenceLeaderVoting, regime_state: RegimeState
    ):
        """Test that conflict follows highest weighted confidence."""
        votes = [
            StrategyVote(
                strategy_name="confident_buyer",
                decision=Decision(Action.BUY, "Strong buy", 0.95, {}),
                weight=0.4,
            ),
            StrategyVote(
                strategy_name="moderate_seller",
                decision=Decision(Action.SELL, "Weak sell", 0.5, {}),
                weight=0.6,
            ),
        ]

        decision = voter.aggregate(votes, regime_state)
        # BUY has higher weighted_confidence: 0.95 * 0.4 = 0.38
        # vs SELL: 0.5 * 0.6 = 0.30
        assert decision.action == Action.BUY
        assert decision.indicators.get("leader") == "confident_buyer"

    def test_conflict_penalizes_confidence(
        self, voter: ConfidenceLeaderVoting, regime_state: RegimeState
    ):
        """Test that disagreement reduces final confidence."""
        unanimous_votes = [
            StrategyVote(
                strategy_name="a",
                decision=Decision(Action.BUY, "Buy", 0.8, {}),
                weight=0.5,
            ),
            StrategyVote(
                strategy_name="b",
                decision=Decision(Action.BUY, "Buy", 0.8, {}),
                weight=0.5,
            ),
        ]

        conflict_votes = [
            StrategyVote(
                strategy_name="a",
                decision=Decision(Action.BUY, "Buy", 0.8, {}),
                weight=0.5,
            ),
            StrategyVote(
                strategy_name="b",
                decision=Decision(Action.SELL, "Sell", 0.4, {}),
                weight=0.5,
            ),
        ]

        unanimous_decision = voter.aggregate(unanimous_votes, regime_state)
        conflict_decision = voter.aggregate(conflict_votes, regime_state)

        # Conflict should have lower confidence
        assert conflict_decision.confidence < unanimous_decision.confidence
