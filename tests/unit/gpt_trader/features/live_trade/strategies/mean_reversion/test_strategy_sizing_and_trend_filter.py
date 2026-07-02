"""Unit tests for MeanReversionStrategy sizing and trend filter behavior."""

from decimal import Decimal

from gpt_trader.app.config import MeanReversionConfig
from gpt_trader.features.live_trade.strategies.baseline import Action
from gpt_trader.features.live_trade.strategies.mean_reversion import MeanReversionStrategy


class TestVolatilityTargeting:
    """Test volatility-targeted position sizing."""

    def test_high_volatility_reduces_position(self):
        """Position size should decrease when volatility is high."""
        config = MeanReversionConfig(
            target_daily_volatility=0.02,  # 2% target
            max_position_pct=0.25,
        )
        strategy = MeanReversionStrategy(config)

        equity = Decimal("10000")

        # High volatility (4%) should reduce position size
        high_vol_size = strategy.calculate_position_size(equity, current_volatility=0.04)

        # Low volatility (1%) should increase position size
        low_vol_size = strategy.calculate_position_size(equity, current_volatility=0.01)

        assert (
            high_vol_size < low_vol_size
        ), f"High vol size ({high_vol_size}) should be less than low vol size ({low_vol_size})"

    def test_position_capped_at_max_pct(self):
        """Position size should never exceed max_position_pct of equity."""
        config = MeanReversionConfig(
            target_daily_volatility=0.02,
            max_position_pct=0.25,
        )
        strategy = MeanReversionStrategy(config)

        equity = Decimal("10000")
        max_allowed = float(equity) * 0.25

        # Very low volatility would scale up significantly
        size = strategy.calculate_position_size(equity, current_volatility=0.001)

        assert float(size) <= max_allowed, f"Position {size} exceeds max allowed {max_allowed}"

    def test_zero_volatility_uses_default(self):
        """Should handle zero volatility gracefully."""
        config = MeanReversionConfig()
        strategy = MeanReversionStrategy(config)

        equity = Decimal("10000")

        # Should not raise, should use default volatility
        size = strategy.calculate_position_size(equity, current_volatility=0.0)

        assert size > 0, "Position size should be positive even with zero volatility"


class TestTrendFilter:
    """Test trend filter behavior."""

    def test_blocks_counter_trend_long(self):
        """Blocks longs when price is below trend MA."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            lookback_window=20,
            trend_filter_enabled=True,
            trend_window=10,
            trend_threshold_pct=0.01,
        )
        strategy = MeanReversionStrategy(config)

        # Construct a strong long signal (very negative Z-score) while trend MA remains
        # well above current price (bearish trend), so the trend filter blocks entry.
        prices = [Decimal("200")] * 19 + [Decimal("100")]
        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.HOLD
        assert "trend filter" in decision.reason.lower()
        assert decision.indicators.get("trend_signal") == "bearish"

    def test_allows_counter_trend_long_with_override(self):
        """Allows counter-trend longs when Z-score is extreme enough."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            lookback_window=20,
            trend_filter_enabled=True,
            trend_window=10,
            trend_threshold_pct=0.01,
            trend_override_z_score=3.0,
        )
        strategy = MeanReversionStrategy(config)

        prices = [Decimal("200")] * 19 + [Decimal("100")]
        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("100"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.BUY
        assert decision.indicators.get("trend_signal") == "bearish"
        assert decision.indicators.get("trend_override_used") is True

    def test_blocks_counter_trend_short(self):
        """Blocks shorts when price is above trend MA."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            lookback_window=20,
            trend_filter_enabled=True,
            trend_window=10,
            trend_threshold_pct=0.01,
            enable_shorts=True,
        )
        strategy = MeanReversionStrategy(config)

        # Construct a strong short signal (very positive Z-score) while trend MA remains
        # well below current price (bullish trend), so the trend filter blocks entry.
        prices = [Decimal("100")] * 19 + [Decimal("200")]
        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("200"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.HOLD
        assert "trend filter" in decision.reason.lower()
        assert decision.indicators.get("trend_signal") == "bullish"

    def test_allows_counter_trend_short_with_override(self):
        """Allows counter-trend shorts when Z-score is extreme enough."""
        config = MeanReversionConfig(
            z_score_entry_threshold=2.0,
            lookback_window=20,
            trend_filter_enabled=True,
            trend_window=10,
            trend_threshold_pct=0.01,
            enable_shorts=True,
            trend_override_z_score=3.0,
        )
        strategy = MeanReversionStrategy(config)

        prices = [Decimal("100")] * 19 + [Decimal("200")]
        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=Decimal("200"),
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )

        assert decision.action == Action.SELL
        assert decision.indicators.get("trend_signal") == "bullish"
        assert decision.indicators.get("trend_override_used") is True
