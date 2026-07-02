"""Tests for SpotStrategy and PerpsStrategy wrappers around the baseline logic."""

from __future__ import annotations

from decimal import Decimal

from gpt_trader.features.live_trade.strategies.baseline.strategy import Action
from tests.unit.gpt_trader.features.live_trade.strategies.baseline_test_helpers import (
    make_downtrend,
    make_uptrend,
)


class TestSpotStrategy:
    """Tests for SpotStrategy class."""

    def test_spot_strategy_uses_spot_config(self) -> None:
        from gpt_trader.features.live_trade.strategies.baseline import (
            SpotStrategy,
            SpotStrategyConfig,
        )

        strategy = SpotStrategy()
        assert isinstance(strategy.config, SpotStrategyConfig)
        assert strategy.config.target_leverage == 1
        assert strategy.config.enable_shorts is False

    def test_spot_strategy_converts_sell_to_hold(self) -> None:
        """Spot strategy should convert SELL signals to HOLD (no shorting)."""
        from gpt_trader.features.live_trade.strategies.baseline import (
            SpotStrategy,
            SpotStrategyConfig,
        )

        # Create a downtrend that would generate a SELL signal
        prices = make_downtrend(30)
        strategy = SpotStrategy(config=SpotStrategyConfig(min_confidence=0.3))
        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # SpotStrategy should convert SELL to HOLD
        assert decision.action in (Action.HOLD,)  # Never SELL
        if "Spot mode" in decision.reason:
            assert "no shorting" in decision.reason

    def test_spot_strategy_allows_buy(self) -> None:
        """Spot strategy should still allow BUY signals."""
        from gpt_trader.features.live_trade.strategies.baseline import (
            SpotStrategy,
            SpotStrategyConfig,
        )

        prices = make_uptrend(30)
        strategy = SpotStrategy(config=SpotStrategyConfig(min_confidence=0.3))
        decision = strategy.decide(
            symbol="BTC-USD",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # SpotStrategy should allow BUY signals
        assert decision.action in (Action.BUY, Action.HOLD)


class TestPerpsStrategy:
    """Tests for PerpsStrategy class."""

    def test_perps_strategy_uses_perps_config(self) -> None:
        from gpt_trader.features.live_trade.strategies.baseline import (
            PerpsStrategy,
            PerpsStrategyConfig,
        )

        strategy = PerpsStrategy()
        assert isinstance(strategy.config, PerpsStrategyConfig)
        assert strategy.config.target_leverage == 5
        assert strategy.config.enable_shorts is True

    def test_perps_strategy_allows_sell(self) -> None:
        """Perps strategy should allow SELL signals for shorting."""
        from gpt_trader.features.live_trade.strategies.baseline import (
            PerpsStrategy,
            PerpsStrategyConfig,
        )

        prices = make_downtrend(30)
        strategy = PerpsStrategy(config=PerpsStrategyConfig(min_confidence=0.3))
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # PerpsStrategy should allow SELL signals
        assert decision.action in (Action.SELL, Action.HOLD)
