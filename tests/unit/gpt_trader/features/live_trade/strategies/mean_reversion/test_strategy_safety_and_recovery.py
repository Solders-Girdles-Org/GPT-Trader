"""Unit tests for MeanReversionStrategy safety and recovery behaviors."""

from decimal import Decimal

from gpt_trader.app.config import MeanReversionConfig
from gpt_trader.features.live_trade.strategies.baseline import Action
from gpt_trader.features.live_trade.strategies.mean_reversion import MeanReversionStrategy


class TestKillSwitch:
    """Test kill switch behavior."""

    def test_kill_switch_prevents_all_actions(self):
        """When kill switch is on, should always HOLD."""
        config = MeanReversionConfig(
            kill_switch_enabled=True,
            z_score_entry_threshold=2.0,
        )
        strategy = MeanReversionStrategy(config)

        # Even with extreme Z-Score, should HOLD
        prices = [Decimal("100")] * 19 + [Decimal("80")]

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("80"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.HOLD
        assert "kill switch" in decision.reason.lower()


class TestRehydration:
    """Test crash recovery / rehydration."""

    def test_rehydrate_returns_zero_for_stateless_strategy(self):
        """Stateless strategy should return 0 from rehydrate."""
        config = MeanReversionConfig()
        strategy = MeanReversionStrategy(config)

        events = [
            {"type": "price_tick", "symbol": "BTC-USD", "price": "100"},
            {"type": "price_tick", "symbol": "BTC-USD", "price": "101"},
        ]

        count = strategy.rehydrate(events)

        assert count == 0, "Stateless strategy should process 0 events"


class TestInsufficientData:
    """Test behavior with insufficient data."""

    def test_hold_when_insufficient_data(self):
        """Should HOLD when not enough price history."""
        config = MeanReversionConfig(lookback_window=20)
        strategy = MeanReversionStrategy(config)

        # Only 5 prices when 20 are needed
        prices = [Decimal("100")] * 5

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.HOLD
        assert "insufficient data" in decision.reason.lower()
        assert decision.indicators["data_points"] == 5
        assert decision.indicators["required"] == 20
