"""Unit tests for MeanReversionStrategy exit logic and cooldown enforcement."""

from decimal import Decimal

from gpt_trader.app.config import MeanReversionConfig
from gpt_trader.features.live_trade.strategies.baseline import Action
from gpt_trader.features.live_trade.strategies.mean_reversion import MeanReversionStrategy


class TestExitLogic:
    """Test exit conditions."""

    def test_exit_sets_cooldown(self):
        """Exit should apply cooldown when configured."""
        config = MeanReversionConfig(
            z_score_exit_threshold=0.5,
            lookback_window=20,
            cooldown_bars=3,
        )
        strategy = MeanReversionStrategy(config)

        prices = [Decimal("100")] * 20
        position_state = {
            "quantity": Decimal("0.1"),
            "side": "long",
            "entry_price": Decimal("95"),
        }

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=position_state,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.CLOSE
        assert decision.indicators.get("cooldown_remaining") == 3

    def test_exit_when_z_score_returns_to_mean(self):
        """Should CLOSE position when Z-Score returns near zero."""
        config = MeanReversionConfig(
            z_score_exit_threshold=0.5,
            lookback_window=20,
        )
        strategy = MeanReversionStrategy(config)

        # Z-Score near zero (price at mean)
        prices = [Decimal("100")] * 20

        position_state = {
            "quantity": Decimal("0.1"),
            "side": "long",
            "entry_price": Decimal("95"),
        }

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=position_state,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.CLOSE, f"Expected CLOSE at mean, got {decision.action}"
        assert "mean reversion complete" in decision.reason.lower()

    def test_stop_loss_triggered(self):
        """Should CLOSE position when stop loss is hit."""
        config = MeanReversionConfig(
            stop_loss_pct=0.03,  # 3% stop loss
            lookback_window=5,
        )
        strategy = MeanReversionStrategy(config)

        # Create varied prices to avoid exit signal
        prices = [Decimal("90"), Decimal("92"), Decimal("94"), Decimal("96"), Decimal("91")]

        position_state = {
            "quantity": Decimal("0.1"),
            "side": "long",
            "entry_price": Decimal("100"),  # Entry at 100, now at 91 = -9% loss
        }

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("91"),
            position_state=position_state,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert (
            decision.action == Action.CLOSE
        ), f"Expected CLOSE on stop loss, got {decision.action}"
        assert "stop loss" in decision.reason.lower()

    def test_take_profit_triggered(self):
        """Should CLOSE position when take profit is hit."""
        config = MeanReversionConfig(
            take_profit_pct=0.06,  # 6% take profit
            lookback_window=5,
        )
        strategy = MeanReversionStrategy(config)

        # Create varied prices to avoid exit signal
        prices = [Decimal("100"), Decimal("102"), Decimal("104"), Decimal("106"), Decimal("108")]

        position_state = {
            "quantity": Decimal("0.1"),
            "side": "long",
            "entry_price": Decimal("100"),  # Entry at 100, now at 108 = +8% gain
        }

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("108"),
            position_state=position_state,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert (
            decision.action == Action.CLOSE
        ), f"Expected CLOSE on take profit, got {decision.action}"
        assert "take profit" in decision.reason.lower()


class TestCooldown:
    """Test cooldown enforcement."""

    def test_cooldown_blocks_entry(self):
        """Cooldown prevents new entries until it expires."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            z_score_exit_threshold=0.5,
            lookback_window=20,
            cooldown_bars=2,
        )
        strategy = MeanReversionStrategy(config)

        prices = [Decimal("100")] * 20
        position_state = {
            "quantity": Decimal("0.1"),
            "side": "long",
            "entry_price": Decimal("95"),
        }

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=position_state,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.CLOSE
        assert decision.indicators.get("cooldown_remaining") == 2

        long_prices = [Decimal("100")] * 19 + [Decimal("85")]
        hold_decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("85"),
            position_state=None,
            recent_marks=long_prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert hold_decision.action == Action.HOLD
        assert "cooldown" in hold_decision.reason.lower()
        assert hold_decision.indicators.get("cooldown_remaining") == 1

        second_hold = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("85"),
            position_state=None,
            recent_marks=long_prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert second_hold.action == Action.HOLD
        assert second_hold.indicators.get("cooldown_remaining") == 0

        entry_decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("85"),
            position_state=None,
            recent_marks=long_prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert entry_decision.action == Action.BUY
