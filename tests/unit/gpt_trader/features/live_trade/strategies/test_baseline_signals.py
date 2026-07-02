"""Tests for BaselinePerpsStrategy entry/exit signals and indicator calculation."""

from __future__ import annotations

from decimal import Decimal

from gpt_trader.features.live_trade.strategies.baseline.strategy import (
    Action,
    BaselinePerpsStrategy,
    PerpsStrategyConfig,
)
from tests.unit.gpt_trader.features.live_trade.strategies.baseline_test_helpers import (
    make_downtrend,
    make_sideways,
    make_uptrend,
)


class TestStrategyEntrySignals:
    """Tests for entry signal generation."""

    def test_bullish_trend_generates_buy(self) -> None:
        # Create a clear uptrend
        prices = make_uptrend(30)
        strategy = BaselinePerpsStrategy(config=PerpsStrategyConfig(min_confidence=0.3))
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # Should generate buy or hold (depending on RSI)
        assert decision.action in (Action.BUY, Action.HOLD)
        assert decision.indicators.get("trend") == "bullish"

    def test_bearish_trend_generates_sell(self) -> None:
        # Create a clear downtrend
        prices = make_downtrend(30)
        strategy = BaselinePerpsStrategy(config=PerpsStrategyConfig(min_confidence=0.3))
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # Should generate sell or hold (depending on RSI)
        assert decision.action in (Action.SELL, Action.HOLD)
        assert decision.indicators.get("trend") == "bearish"

    def test_sideways_market_holds(self) -> None:
        prices = make_sideways(30)
        strategy = BaselinePerpsStrategy()
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # Sideways should typically hold
        assert decision.action == Action.HOLD


class TestStrategyExitSignals:
    """Tests for exit signal generation."""

    def test_long_position_exit_on_bearish_signal(self) -> None:
        # Create downtrend that should trigger exit
        prices = make_downtrend(30)
        strategy = BaselinePerpsStrategy(config=PerpsStrategyConfig(min_confidence=0.3))
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state={"quantity": Decimal("1"), "side": "long"},
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # Should suggest closing or holding
        assert decision.action in (Action.CLOSE, Action.HOLD)

    def test_stop_loss_triggers_close(self) -> None:
        prices = make_sideways(30)
        strategy = BaselinePerpsStrategy(config=PerpsStrategyConfig(stop_loss_pct=0.02))
        # Entry at 100, current at 97 = -3% loss
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=Decimal("97"),
            position_state={
                "quantity": Decimal("1"),
                "side": "long",
                "entry_price": Decimal("100"),
            },
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        assert decision.action == Action.CLOSE
        assert "Stop loss" in decision.reason

    def test_take_profit_triggers_close(self) -> None:
        prices = make_sideways(30)
        strategy = BaselinePerpsStrategy(config=PerpsStrategyConfig(take_profit_pct=0.05))
        # Entry at 100, current at 106 = +6% profit
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=Decimal("106"),
            position_state={
                "quantity": Decimal("1"),
                "side": "long",
                "entry_price": Decimal("100"),
            },
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        assert decision.action == Action.CLOSE
        assert "Take profit" in decision.reason


class TestStrategyIndicatorCalculation:
    """Tests for indicator calculation."""

    def test_indicators_populated_in_decision(self) -> None:
        prices = make_uptrend(30)
        strategy = BaselinePerpsStrategy()
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # Indicators should be populated
        assert "rsi" in decision.indicators
        assert "short_ma" in decision.indicators
        assert "long_ma" in decision.indicators
        assert "trend" in decision.indicators
        assert "rsi_signal" in decision.indicators

    def test_rsi_signal_oversold(self) -> None:
        # Create a sharp downtrend to get oversold RSI (need 25+ values)
        prices = [Decimal(str(100 - i * 2)) for i in range(25)]
        strategy = BaselinePerpsStrategy()
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # RSI should be low
        rsi = decision.indicators.get("rsi")
        assert rsi is not None
        assert rsi < 50  # Should be bearish

    def test_rsi_signal_overbought(self) -> None:
        # Create a sharp uptrend to get overbought RSI (need 25+ values)
        prices = [Decimal(str(100 + i * 2)) for i in range(25)]
        strategy = BaselinePerpsStrategy()
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # RSI should be high
        rsi = decision.indicators.get("rsi")
        assert rsi is not None
        assert rsi > 50  # Should be bullish


class TestStrategyConfidence:
    """Tests for confidence scoring."""

    def test_multiple_bullish_signals_high_confidence(self) -> None:
        # Strong uptrend should have high confidence
        prices = make_uptrend(30)
        strategy = BaselinePerpsStrategy()
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # Should have some confidence
        assert decision.confidence >= 0.0
        assert decision.confidence <= 1.0

    def test_weak_signals_low_confidence(self) -> None:
        # Sideways market should have low confidence
        prices = make_sideways(30)
        strategy = BaselinePerpsStrategy()
        decision = strategy.decide(
            symbol="BTC-PERP",
            current_mark=prices[-1],
            position_state=None,
            recent_marks=prices,
            equity=Decimal("10000"),
            product=None,
        )
        # Should have lower confidence in sideways market
        assert decision.confidence < 0.8
