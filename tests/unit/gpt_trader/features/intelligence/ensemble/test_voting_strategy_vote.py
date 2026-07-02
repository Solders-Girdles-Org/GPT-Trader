"""Tests for StrategyVote model."""

from gpt_trader.features.intelligence.ensemble.models import StrategyVote
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision


class TestStrategyVote:
    """Test StrategyVote model."""

    def test_weighted_confidence(self):
        """Test weighted_confidence property."""
        vote = StrategyVote(
            strategy_name="test",
            decision=Decision(Action.BUY, "Test", 0.8, {}),
            weight=0.5,
        )

        assert vote.weighted_confidence == 0.4  # 0.8 * 0.5

    def test_to_dict(self):
        """Test serialization."""
        vote = StrategyVote(
            strategy_name="baseline",
            decision=Decision(Action.SELL, "Overbought", 0.75, {}),
            weight=0.6,
        )

        d = vote.to_dict()
        assert d["strategy_name"] == "baseline"
        assert d["action"] == "sell"
        assert d["confidence"] == 0.75
        assert d["weight"] == 0.6
        assert d["weighted_confidence"] == 0.45
