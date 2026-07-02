"""
Unit tests for MeanReversionStrategy Z-Score and signal generation.
"""

from decimal import Decimal

from gpt_trader.app.config import MeanReversionConfig
from gpt_trader.features.live_trade.strategies.baseline import Action
from gpt_trader.features.live_trade.strategies.mean_reversion import MeanReversionStrategy


class TestZScoreCalculation:
    """Test Z-Score calculation accuracy."""

    def test_z_score_at_mean_is_zero(self):
        """Z-Score should be ~0 when price equals the rolling mean."""
        config = MeanReversionConfig(lookback_window=5)
        strategy = MeanReversionStrategy(config)

        # Create prices where the last price equals the mean
        # Mean of [100, 102, 98, 101, 99] = 100
        prices = [Decimal("100"), Decimal("102"), Decimal("98"), Decimal("101"), Decimal("100")]

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        z_score = decision.indicators.get("z_score", 0)
        assert abs(z_score) < 0.5, f"Z-Score should be near zero, got {z_score}"

    def test_z_score_positive_when_above_mean(self):
        """Z-Score should be positive when price is above the mean."""
        config = MeanReversionConfig(lookback_window=5)
        strategy = MeanReversionStrategy(config)

        # Create prices with a high final value
        # Mean of [100, 100, 100, 100, 110] ≈ 102
        prices = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("110")]

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("110"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        z_score = decision.indicators.get("z_score", 0)
        assert z_score > 0, f"Z-Score should be positive, got {z_score}"

    def test_z_score_negative_when_below_mean(self):
        """Z-Score should be negative when price is below the mean."""
        config = MeanReversionConfig(lookback_window=5)
        strategy = MeanReversionStrategy(config)

        # Create prices with a low final value
        prices = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("90")]

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("90"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        z_score = decision.indicators.get("z_score", 0)
        assert z_score < 0, f"Z-Score should be negative, got {z_score}"


class TestSignalGeneration:
    """Test signal generation at threshold boundaries."""

    def test_long_signal_below_negative_threshold(self):
        """Should generate BUY signal when Z-Score < -threshold."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            lookback_window=20,
        )
        strategy = MeanReversionStrategy(config)

        # Create stable prices then a significant drop (Z-Score < -2)
        prices = [Decimal("100")] * 19 + [Decimal("85")]

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("85"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.BUY, f"Expected BUY, got {decision.action}"
        assert "mean reversion long" in decision.reason.lower()

    def test_short_signal_above_positive_threshold(self):
        """Should generate SELL signal when Z-Score > +threshold."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            lookback_window=20,
            enable_shorts=True,
        )
        strategy = MeanReversionStrategy(config)

        # Create stable prices then a significant spike (Z-Score > +2)
        prices = [Decimal("100")] * 19 + [Decimal("115")]

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("115"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.SELL, f"Expected SELL, got {decision.action}"
        assert "mean reversion short" in decision.reason.lower()

    def test_no_short_when_disabled(self):
        """Should not generate SELL when shorts are disabled."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            lookback_window=20,
            enable_shorts=False,
        )
        strategy = MeanReversionStrategy(config)

        prices = [Decimal("100")] * 19 + [Decimal("115")]

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("115"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert (
            decision.action == Action.HOLD
        ), f"Expected HOLD (shorts disabled), got {decision.action}"

    def test_hold_in_neutral_zone(self):
        """Should HOLD when Z-Score is within neutral zone."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            lookback_window=20,
        )
        strategy = MeanReversionStrategy(config)

        # Create prices with reasonable variance where final price is near mean
        # Mean ≈ 100, std ≈ 5, price at 103 gives Z-Score ≈ 0.6 (in neutral zone)
        prices = [
            Decimal("95"),
            Decimal("98"),
            Decimal("102"),
            Decimal("105"),
            Decimal("100"),
            Decimal("97"),
            Decimal("103"),
            Decimal("99"),
            Decimal("101"),
            Decimal("98"),
            Decimal("104"),
            Decimal("96"),
            Decimal("100"),
            Decimal("102"),
            Decimal("98"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
            Decimal("102"),
            Decimal("100"),
        ]

        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        # Z-Score should be near 0 (within -2 to +2 range)
        z_score = decision.indicators.get("z_score", 0)
        assert abs(z_score) < 2.0, f"Z-Score {z_score} should be in neutral zone"
        assert decision.action == Action.HOLD, f"Expected HOLD, got {decision.action}"
