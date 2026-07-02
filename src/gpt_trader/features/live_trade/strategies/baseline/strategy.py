"""
Baseline perpetuals trading strategy with RSI, MA crossover, and confidence scoring.

This strategy combines multiple technical indicators to generate trading signals:
- RSI for overbought/oversold detection
- Moving average crossovers for trend direction
- Confidence scoring based on indicator agreement
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from gpt_trader.core import Action, Decision, Product
from gpt_trader.features.live_trade.indicators import (
    compute_ma_series,
    detect_crossover,
    relative_strength_index,
    simple_moving_average,
)
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.features.live_trade.strategies.base import MarketDataContext

logger = get_logger(__name__, component="baseline_strategy")


@dataclass
class BaseStrategyConfig:
    """Common configuration for all trading strategies."""

    # Moving average settings
    short_ma_period: int = 5
    long_ma_period: int = 20

    # RSI settings
    rsi_period: int = 14
    rsi_overbought: int = 70
    rsi_oversold: int = 30

    # Risk management
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05
    trailing_stop_pct: float | None = None

    # Confidence thresholds
    min_confidence: float = 0.5
    crossover_weight: float = 0.4
    rsi_weight: float = 0.3
    trend_weight: float = 0.3

    # Control flags
    kill_switch_enabled: bool = False
    force_entry_on_trend: bool = False

    # Position sizing
    position_fraction: float | None = None


@dataclass
class SpotStrategyConfig(BaseStrategyConfig):
    """Configuration for spot trading strategies.

    Spot trading has no leverage and no short positions.
    """

    enable_shorts: bool = False

    @property
    def target_leverage(self) -> int:
        """Spot trading always uses 1x leverage."""
        return 1


@dataclass
class PerpsStrategyConfig(BaseStrategyConfig):
    """Configuration for perpetuals trading strategies.

    Supports configurable leverage and short positions.
    """

    target_leverage: int = 5
    enable_shorts: bool = True
    max_leverage: int = 10


@dataclass
class IndicatorState:
    """Current state of all indicators."""

    rsi: Decimal | None = None
    short_ma: Decimal | None = None
    long_ma: Decimal | None = None
    crossover_signal: str = "none"  # "bullish", "bearish", or "none"
    trend: str = "neutral"  # "bullish", "bearish", or "neutral"
    rsi_signal: str = "neutral"  # "oversold", "overbought", or "neutral"


class BaselinePerpsStrategy:
    """Perpetuals trading strategy combining RSI, MA crossover, and trend analysis.

    Signal Generation Logic:
    1. RSI provides overbought/oversold signals
    2. MA crossover provides trend reversal signals
    3. Price vs long MA provides trend direction
    4. Confidence is computed from indicator agreement

    Entry Signals:
    - BUY: RSI oversold + bullish crossover/trend
    - SELL: RSI overbought + bearish crossover/trend

    Exit Signals:
    - Close long: RSI overbought or bearish crossover
    - Close short: RSI oversold or bullish crossover
    """

    def __init__(self, config: BaseStrategyConfig | Any = None, risk_manager: Any = None):
        self.config: BaseStrategyConfig
        if config is None:
            self.config = PerpsStrategyConfig()
        elif isinstance(config, BaseStrategyConfig):
            self.config = config
        else:
            # Convert dict or other config object to PerpsStrategyConfig
            self.config = self._parse_config(config)
        self.risk_manager = risk_manager

    def _parse_config(self, config: Any) -> PerpsStrategyConfig:
        """Parse configuration from various formats."""
        if hasattr(config, "__dict__"):
            kwargs = {}
            for field_name in PerpsStrategyConfig.__dataclass_fields__:
                if hasattr(config, field_name):
                    kwargs[field_name] = getattr(config, field_name)
            return PerpsStrategyConfig(**kwargs)
        return PerpsStrategyConfig()

    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Product | None,
        market_data: "MarketDataContext | None" = None,
        candles: Sequence[Any] | None = None,
    ) -> Decision:
        """Generate a trading decision based on technical indicators.

        Args:
            symbol: Trading pair symbol
            current_mark: Current mark price
            position_state: Current position info (if any)
            recent_marks: Historical mark prices (oldest first)
            equity: Account equity
            product: Product specification
            market_data: Optional enhanced market data (orderbook depth, trade flow)
            candles: Historical candles (optional)

        Returns:
            Decision with action, reason, confidence, and indicator state
        """
        # Check kill switch
        if self.config.kill_switch_enabled:
            return Decision(
                Action.HOLD,
                "Kill switch enabled",
                confidence=0.0,
                indicators={"kill_switch": True},
            )

        # Minimum data requirements
        min_data = max(self.config.long_ma_period, self.config.rsi_period + 1)
        if len(recent_marks) < min_data:
            return Decision(
                Action.HOLD,
                f"Insufficient data: {len(recent_marks)}/{min_data} periods",
                confidence=0.0,
                indicators={"data_points": len(recent_marks), "required": min_data},
            )

        # Calculate indicators
        indicator_state = self._calculate_indicators(recent_marks)

        # Determine if we have an existing position
        has_position = position_state is not None and position_state.get("quantity", 0) != 0
        position_side = position_state.get("side", "none") if position_state else "none"

        # Generate decision based on position state
        if has_position:
            return self._decide_with_position(
                symbol, current_mark, position_side, indicator_state, position_state
            )
        else:
            return self._decide_entry(symbol, current_mark, indicator_state)

    def _calculate_indicators(self, recent_marks: Sequence[Decimal]) -> IndicatorState:
        """Calculate all technical indicators from price history."""
        state = IndicatorState()

        # Calculate RSI
        state.rsi = relative_strength_index(list(recent_marks), period=self.config.rsi_period)

        # Calculate moving averages
        state.short_ma = simple_moving_average(
            list(recent_marks), period=self.config.short_ma_period
        )
        state.long_ma = simple_moving_average(list(recent_marks), period=self.config.long_ma_period)

        # Determine RSI signal
        if state.rsi is not None:
            if state.rsi < self.config.rsi_oversold:
                state.rsi_signal = "oversold"
            elif state.rsi > self.config.rsi_overbought:
                state.rsi_signal = "overbought"
            else:
                state.rsi_signal = "neutral"

        # Determine trend from price vs long MA
        if state.long_ma is not None:
            current_price = recent_marks[-1]
            if current_price > state.long_ma:
                state.trend = "bullish"
            elif current_price < state.long_ma:
                state.trend = "bearish"
            else:
                state.trend = "neutral"

        # Detect MA crossover
        if len(recent_marks) >= self.config.long_ma_period:
            short_ma_series = compute_ma_series(
                list(recent_marks), self.config.short_ma_period, "sma"
            )
            long_ma_series = compute_ma_series(
                list(recent_marks), self.config.long_ma_period, "sma"
            )

            crossover = detect_crossover(short_ma_series, long_ma_series, lookback=3)
            if crossover and crossover.crossed:
                state.crossover_signal = crossover.direction

        return state

    def _decide_entry(
        self,
        symbol: str,
        current_mark: Decimal,
        indicators: IndicatorState,
    ) -> Decision:
        """Generate entry decision when no position exists."""
        signals: list[tuple[str, float]] = []  # (direction, weight)
        reasons: list[str] = []

        # RSI signal
        if indicators.rsi_signal == "oversold":
            signals.append(("buy", self.config.rsi_weight))
            reasons.append(f"RSI oversold ({indicators.rsi:.1f})")
        elif indicators.rsi_signal == "overbought":
            signals.append(("sell", self.config.rsi_weight))
            reasons.append(f"RSI overbought ({indicators.rsi:.1f})")

        # Crossover signal
        if indicators.crossover_signal == "bullish":
            signals.append(("buy", self.config.crossover_weight))
            reasons.append("Bullish MA crossover")
        elif indicators.crossover_signal == "bearish":
            signals.append(("sell", self.config.crossover_weight))
            reasons.append("Bearish MA crossover")

        # Trend alignment
        if indicators.trend == "bullish":
            signals.append(("buy", self.config.trend_weight))
            reasons.append("Bullish trend")
        elif indicators.trend == "bearish":
            signals.append(("sell", self.config.trend_weight))
            reasons.append("Bearish trend")

        # Calculate net signal and confidence
        buy_weight = sum(w for d, w in signals if d == "buy")
        sell_weight = sum(w for d, w in signals if d == "sell")

        indicator_dict = self._indicators_to_dict(indicators)

        # Entry gate: require a dominant side AND enough aggregated weight.
        # With default weights, trend-only signals won't pass without crossover/RSI alignment.
        if buy_weight > sell_weight and buy_weight >= self.config.min_confidence:
            return Decision(
                Action.BUY,
                reason="; ".join(reasons) if reasons else "Buy signal",
                confidence=min(buy_weight, 1.0),
                indicators=indicator_dict,
            )
        elif sell_weight > buy_weight and sell_weight >= self.config.min_confidence:
            return Decision(
                Action.SELL,
                reason="; ".join(reasons) if reasons else "Sell signal",
                confidence=min(sell_weight, 1.0),
                indicators=indicator_dict,
            )

        if reasons or buy_weight or sell_weight:
            if buy_weight == sell_weight:
                blocked_reason = "balanced_signals"
            elif buy_weight > sell_weight:
                blocked_reason = "buy_below_min_confidence"
            else:
                blocked_reason = "sell_below_min_confidence"
            logger.info(
                "Entry gate blocked",
                symbol=symbol,
                blocked_reason=blocked_reason,
                buy_weight=round(buy_weight, 4),
                sell_weight=round(sell_weight, 4),
                min_confidence=self.config.min_confidence,
                rsi=indicator_dict.get("rsi"),
                rsi_signal=indicators.rsi_signal,
                crossover_signal=indicators.crossover_signal,
                trend=indicators.trend,
                short_ma=indicator_dict.get("short_ma"),
                long_ma=indicator_dict.get("long_ma"),
            )

        return Decision(
            Action.HOLD,
            reason="No clear signal" if not reasons else f"Weak signals: {'; '.join(reasons)}",
            confidence=max(buy_weight, sell_weight),
            indicators=indicator_dict,
        )

    def _decide_with_position(
        self,
        symbol: str,
        current_mark: Decimal,
        position_side: str,
        indicators: IndicatorState,
        position_state: dict[str, Any] | None,
    ) -> Decision:
        """Generate decision when a position exists (exit or hold)."""
        indicator_dict = self._indicators_to_dict(indicators)
        reasons: list[str] = []

        # Check for exit signals based on position side
        if position_side == "long":
            # Exit long if RSI overbought or bearish crossover
            exit_confidence = 0.0
            if indicators.rsi_signal == "overbought":
                exit_confidence += self.config.rsi_weight
                reasons.append(f"RSI overbought ({indicators.rsi:.1f})")
            if indicators.crossover_signal == "bearish":
                exit_confidence += self.config.crossover_weight
                reasons.append("Bearish MA crossover")
            if indicators.trend == "bearish":
                exit_confidence += self.config.trend_weight * 0.5  # Partial weight
                reasons.append("Bearish trend developing")

            if exit_confidence >= self.config.min_confidence:
                return Decision(
                    Action.CLOSE,
                    reason="; ".join(reasons),
                    confidence=min(exit_confidence, 1.0),
                    indicators=indicator_dict,
                )

        elif position_side == "short":
            # Exit short if RSI oversold or bullish crossover
            exit_confidence = 0.0
            if indicators.rsi_signal == "oversold":
                exit_confidence += self.config.rsi_weight
                reasons.append(f"RSI oversold ({indicators.rsi:.1f})")
            if indicators.crossover_signal == "bullish":
                exit_confidence += self.config.crossover_weight
                reasons.append("Bullish MA crossover")
            if indicators.trend == "bullish":
                exit_confidence += self.config.trend_weight * 0.5
                reasons.append("Bullish trend developing")

            if exit_confidence >= self.config.min_confidence:
                return Decision(
                    Action.CLOSE,
                    reason="; ".join(reasons),
                    confidence=min(exit_confidence, 1.0),
                    indicators=indicator_dict,
                )

        # Check stop loss / take profit if position_state has entry price
        if position_state:
            entry_price = position_state.get("entry_price")
            if entry_price is not None:
                entry = Decimal(str(entry_price))
                pnl_pct = (current_mark - entry) / entry
                if position_side == "short":
                    pnl_pct = -pnl_pct

                if pnl_pct <= -Decimal(str(self.config.stop_loss_pct)):
                    return Decision(
                        Action.CLOSE,
                        reason=f"Stop loss triggered ({pnl_pct:.2%})",
                        confidence=1.0,
                        indicators=indicator_dict,
                    )
                if pnl_pct >= Decimal(str(self.config.take_profit_pct)):
                    return Decision(
                        Action.CLOSE,
                        reason=f"Take profit triggered ({pnl_pct:.2%})",
                        confidence=1.0,
                        indicators=indicator_dict,
                    )

        return Decision(
            Action.HOLD,
            reason="Holding position",
            confidence=0.5,
            indicators=indicator_dict,
        )

    def _indicators_to_dict(self, indicators: IndicatorState) -> dict[str, Any]:
        """Convert indicator state to dictionary for decision output."""
        return {
            "rsi": float(indicators.rsi) if indicators.rsi is not None else None,
            "short_ma": float(indicators.short_ma) if indicators.short_ma is not None else None,
            "long_ma": float(indicators.long_ma) if indicators.long_ma is not None else None,
            "crossover_signal": indicators.crossover_signal,
            "trend": indicators.trend,
            "rsi_signal": indicators.rsi_signal,
        }

    def _build_default_product(self, symbol: str) -> Any:
        """Build a default product specification."""
        return None

    def rehydrate(self, events: Sequence[dict[str, Any]]) -> int:
        """Restore strategy state from historical events.

        BaselinePerpsStrategy is stateless - it receives price history
        externally via the `recent_marks` parameter. No internal state
        needs restoration.

        Args:
            events: List of persisted events (oldest first)

        Returns:
            Number of events processed (always 0 for this strategy)
        """
        return 0


class SpotStrategy(BaselinePerpsStrategy):
    """Spot trading strategy - no shorting allowed.

    Inherits all technical analysis from BaselinePerpsStrategy but
    converts SELL signals to HOLD since spot markets don't support shorting.
    Always uses SpotStrategyConfig with 1x leverage.
    """

    def __init__(
        self,
        config: SpotStrategyConfig | BaseStrategyConfig | None = None,
        risk_manager: Any = None,
    ):
        if config is None:
            config = SpotStrategyConfig()
        elif not isinstance(config, SpotStrategyConfig):
            # Convert base config to spot config
            config = SpotStrategyConfig(
                short_ma_period=config.short_ma_period,
                long_ma_period=config.long_ma_period,
                rsi_period=config.rsi_period,
                rsi_overbought=config.rsi_overbought,
                rsi_oversold=config.rsi_oversold,
                stop_loss_pct=config.stop_loss_pct,
                take_profit_pct=config.take_profit_pct,
                trailing_stop_pct=config.trailing_stop_pct,
                min_confidence=config.min_confidence,
                crossover_weight=config.crossover_weight,
                rsi_weight=config.rsi_weight,
                trend_weight=config.trend_weight,
                kill_switch_enabled=config.kill_switch_enabled,
                force_entry_on_trend=config.force_entry_on_trend,
                position_fraction=config.position_fraction,
            )
        super().__init__(config=config, risk_manager=risk_manager)

    def _decide_entry(
        self,
        symbol: str,
        current_mark: Decimal,
        indicators: IndicatorState,
    ) -> Decision:
        """Generate entry decision - converts SELL to HOLD for spot markets."""
        decision = super()._decide_entry(symbol, current_mark, indicators)

        # Spot markets don't support shorting - convert SELL to HOLD
        if decision.action == Action.SELL:
            return Decision(
                Action.HOLD,
                reason=f"Spot mode - no shorting ({decision.reason})",
                confidence=decision.confidence,
                indicators=decision.indicators,
            )

        return decision


class PerpsStrategy(BaselinePerpsStrategy):
    """Perpetuals trading strategy with full functionality.

    Supports shorting and configurable leverage.
    This is a semantic alias for BaselinePerpsStrategy for clarity.
    """

    def __init__(
        self,
        config: PerpsStrategyConfig | BaseStrategyConfig | None = None,
        risk_manager: Any = None,
    ):
        if config is None:
            config = PerpsStrategyConfig()
        elif not isinstance(config, PerpsStrategyConfig):
            # Convert base config to perps config with defaults
            config = PerpsStrategyConfig(
                short_ma_period=config.short_ma_period,
                long_ma_period=config.long_ma_period,
                rsi_period=config.rsi_period,
                rsi_overbought=config.rsi_overbought,
                rsi_oversold=config.rsi_oversold,
                stop_loss_pct=config.stop_loss_pct,
                take_profit_pct=config.take_profit_pct,
                trailing_stop_pct=config.trailing_stop_pct,
                min_confidence=config.min_confidence,
                crossover_weight=config.crossover_weight,
                rsi_weight=config.rsi_weight,
                trend_weight=config.trend_weight,
                kill_switch_enabled=config.kill_switch_enabled,
                force_entry_on_trend=config.force_entry_on_trend,
                position_fraction=config.position_fraction,
            )
        super().__init__(config=config, risk_manager=risk_manager)
