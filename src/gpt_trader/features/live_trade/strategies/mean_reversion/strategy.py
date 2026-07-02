"""
Mean Reversion Strategy using Z-Score and volatility-targeted position sizing.

This strategy:
1. Calculates Z-Score: (Current Price - Rolling Mean) / Rolling StdDev
2. Enters long when Z-Score < -threshold (price is statistically cheap)
3. Enters short when Z-Score > +threshold (price is statistically expensive)
4. Exits when Z-Score crosses near zero (price returned to fair value)
5. Sizes positions based on volatility targeting
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from gpt_trader.app.config import MeanReversionConfig
from gpt_trader.core import Product
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision
from gpt_trader.utilities.logging_patterns import get_logger

if TYPE_CHECKING:
    from gpt_trader.features.live_trade.strategies.base import MarketDataContext

logger = get_logger(__name__, component="mean_reversion")


@dataclass
class ZScoreState:
    """Current Z-Score indicator state."""

    rolling_mean: float | None = None
    rolling_std: float | None = None
    z_score: float | None = None
    daily_volatility: float | None = None
    trend_ma: float | None = None
    trend_pct: float | None = None
    trend_signal: str = "neutral"
    signal: str = "neutral"  # "long", "short", "exit_long", "exit_short", "neutral"


class MeanReversionStrategy:
    """Mean reversion strategy using Z-Score for entry/exit signals.

    Entry Logic:
    - Long when Z-Score < -entry_threshold (price below fair value)
    - Short when Z-Score > +entry_threshold (price above fair value)

    Exit Logic:
    - Exit when |Z-Score| < exit_threshold (price near fair value)

    Position Sizing:
    - Volatility targeting: size = (target_vol / current_vol) * equity * max_position_pct
    """

    def __init__(self, config: MeanReversionConfig) -> None:
        self.config = config
        self._cooldown_remaining = 0
        logger.info(
            f"MeanReversionStrategy initialized: "
            f"z_entry={config.z_score_entry_threshold}, "
            f"z_exit={config.z_score_exit_threshold}, "
            f"window={config.lookback_window}, "
            f"cooldown_bars={config.cooldown_bars}"
        )

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
        """Generate a trading decision based on Z-Score.

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
        min_data = self.config.lookback_window
        if len(recent_marks) < min_data:
            return Decision(
                Action.HOLD,
                f"Insufficient data: {len(recent_marks)}/{min_data} periods",
                confidence=0.0,
                indicators={"data_points": len(recent_marks), "required": min_data},
            )

        # Calculate Z-Score state
        state = self._calculate_z_score(recent_marks)

        # Determine if we have an existing position
        has_position = position_state is not None and position_state.get("quantity", 0) != 0
        position_side = position_state.get("side", "none") if position_state else "none"

        # Generate decision based on position state
        if has_position:
            return self._decide_with_position(
                symbol, current_mark, position_side, state, position_state, equity
            )
        return self._decide_entry(symbol, current_mark, state, equity)

    def _calculate_z_score(self, recent_marks: Sequence[Decimal]) -> ZScoreState:
        """Calculate Z-Score and volatility from price history."""
        state = ZScoreState()

        # Convert to floats for statistics calculations
        prices = [float(p) for p in recent_marks]
        window = min(self.config.lookback_window, len(prices))
        window_prices = prices[-window:]

        # Calculate rolling mean and standard deviation
        state.rolling_mean = statistics.mean(window_prices)

        if len(window_prices) >= 2:
            state.rolling_std = statistics.stdev(window_prices)
        else:
            state.rolling_std = 0.0

        # Calculate Z-Score
        current_price = prices[-1]
        if state.rolling_std and state.rolling_std > 0:
            state.z_score = (current_price - state.rolling_mean) / state.rolling_std
        else:
            state.z_score = 0.0

        # Calculate daily volatility (using returns if enough data)
        if len(prices) >= 2:
            returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
            if len(returns) >= 2:
                state.daily_volatility = statistics.stdev(returns)
            else:
                state.daily_volatility = abs(returns[0]) if returns else 0.01

        # Calculate trend filter inputs
        if self.config.trend_filter_enabled:
            trend_window = min(self.config.trend_window, len(prices))
            if trend_window >= 2:
                trend_prices = prices[-trend_window:]
                state.trend_ma = statistics.mean(trend_prices)
                if state.trend_ma:
                    state.trend_pct = (current_price - state.trend_ma) / state.trend_ma
                    if state.trend_pct > self.config.trend_threshold_pct:
                        state.trend_signal = "bullish"
                    elif state.trend_pct < -self.config.trend_threshold_pct:
                        state.trend_signal = "bearish"
                    else:
                        state.trend_signal = "neutral"

        # Determine signal
        z = state.z_score or 0.0
        entry_threshold = self.config.z_score_entry_threshold
        exit_threshold = self.config.z_score_exit_threshold

        if z < -entry_threshold:
            state.signal = "long"  # Price is cheap, buy
        elif z > entry_threshold:
            state.signal = "short"  # Price is expensive, sell
        elif abs(z) < exit_threshold:
            state.signal = "exit"  # Price at fair value, close positions
        else:
            state.signal = "neutral"

        return state

    def _decide_entry(
        self,
        symbol: str,
        current_mark: Decimal,
        state: ZScoreState,
        equity: Decimal,
    ) -> Decision:
        """Decide on entry when no position exists."""
        indicators = self._build_indicators(state)

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            indicators["cooldown_remaining"] = self._cooldown_remaining
            return Decision(
                Action.HOLD,
                f"Cooldown active ({self._cooldown_remaining} bars remaining)",
                confidence=0.0,
                indicators=indicators,
            )

        if state.signal == "long":
            if self._is_counter_trend(state, "long"):
                override_z = self.config.trend_override_z_score
                if override_z is None or abs(state.z_score or 0.0) < override_z:
                    return Decision(
                        Action.HOLD,
                        "Blocked long entry (trend filter)",
                        confidence=0.0,
                        indicators=indicators,
                    )
                indicators["trend_override_used"] = True
            confidence = self._calculate_confidence(state, "long")
            return Decision(
                Action.BUY,
                f"Z-Score={state.z_score:.2f} below -{self.config.z_score_entry_threshold} (mean reversion long)",
                confidence=confidence,
                indicators=indicators,
            )

        if state.signal == "short" and self.config.enable_shorts:
            if self._is_counter_trend(state, "short"):
                override_z = self.config.trend_override_z_score
                if override_z is None or abs(state.z_score or 0.0) < override_z:
                    return Decision(
                        Action.HOLD,
                        "Blocked short entry (trend filter)",
                        confidence=0.0,
                        indicators=indicators,
                    )
                indicators["trend_override_used"] = True
            confidence = self._calculate_confidence(state, "short")
            return Decision(
                Action.SELL,
                f"Z-Score={state.z_score:.2f} above +{self.config.z_score_entry_threshold} (mean reversion short)",
                confidence=confidence,
                indicators=indicators,
            )

        return Decision(
            Action.HOLD,
            f"Z-Score={state.z_score:.2f} within neutral zone",
            confidence=0.0,
            indicators=indicators,
        )

    def _decide_with_position(
        self,
        symbol: str,
        current_mark: Decimal,
        position_side: str,
        state: ZScoreState,
        position_state: dict[str, Any] | None,
        equity: Decimal,
    ) -> Decision:
        """Decide on exit or hold when position exists."""
        indicators = self._build_indicators(state)
        z = state.z_score or 0.0

        # Check for mean reversion exit (price returned to fair value)
        if state.signal == "exit":
            if self.config.cooldown_bars > 0:
                self._cooldown_remaining = max(self._cooldown_remaining, self.config.cooldown_bars)
                indicators["cooldown_remaining"] = self._cooldown_remaining
            return Decision(
                Action.CLOSE,
                f"Z-Score={z:.2f} near zero (mean reversion complete)",
                confidence=0.8,
                indicators=indicators,
            )

        # Check for stop loss / take profit
        if position_state:
            entry_price = position_state.get("entry_price")
            if entry_price:
                entry = float(entry_price)
                current = float(current_mark)
                pnl_pct = (
                    (current - entry) / entry
                    if position_side == "long"
                    else (entry - current) / entry
                )

                # Stop loss
                if pnl_pct < -self.config.stop_loss_pct:
                    if self.config.cooldown_bars > 0:
                        self._cooldown_remaining = max(
                            self._cooldown_remaining, self.config.cooldown_bars
                        )
                        indicators["cooldown_remaining"] = self._cooldown_remaining
                    return Decision(
                        Action.CLOSE,
                        f"Stop loss triggered: {pnl_pct:.2%} loss",
                        confidence=1.0,
                        indicators=indicators,
                    )

                # Take profit
                if pnl_pct > self.config.take_profit_pct:
                    if self.config.cooldown_bars > 0:
                        self._cooldown_remaining = max(
                            self._cooldown_remaining, self.config.cooldown_bars
                        )
                        indicators["cooldown_remaining"] = self._cooldown_remaining
                    return Decision(
                        Action.CLOSE,
                        f"Take profit triggered: {pnl_pct:.2%} gain",
                        confidence=1.0,
                        indicators=indicators,
                    )

        # Check for signal reversal (optional: close on opposite signal)
        if position_side == "long" and state.signal == "short":
            if self.config.cooldown_bars > 0:
                self._cooldown_remaining = max(self._cooldown_remaining, self.config.cooldown_bars)
                indicators["cooldown_remaining"] = self._cooldown_remaining
            return Decision(
                Action.CLOSE,
                f"Signal reversal: Z-Score={z:.2f} now indicates short",
                confidence=0.7,
                indicators=indicators,
            )
        if position_side == "short" and state.signal == "long":
            if self.config.cooldown_bars > 0:
                self._cooldown_remaining = max(self._cooldown_remaining, self.config.cooldown_bars)
                indicators["cooldown_remaining"] = self._cooldown_remaining
            return Decision(
                Action.CLOSE,
                f"Signal reversal: Z-Score={z:.2f} now indicates long",
                confidence=0.7,
                indicators=indicators,
            )

        return Decision(
            Action.HOLD,
            f"Holding position, Z-Score={z:.2f}",
            confidence=0.5,
            indicators=indicators,
        )

    def _calculate_confidence(self, state: ZScoreState, direction: str) -> float:
        """Calculate confidence based on Z-Score extremity."""
        z = abs(state.z_score or 0.0)
        threshold = self.config.z_score_entry_threshold

        # Base confidence at threshold, scaling up as Z-Score increases
        # Max confidence at 2x threshold
        if z >= threshold:
            excess = z - threshold
            max_excess = threshold  # Double the threshold = max confidence
            confidence = 0.5 + 0.5 * min(excess / max_excess, 1.0)
            return min(confidence, 0.95)
        return 0.0

    def _is_counter_trend(self, state: ZScoreState, direction: str) -> bool:
        """Return True when entries fight the dominant trend."""
        if not self.config.trend_filter_enabled:
            return False
        if state.trend_signal == "neutral":
            return False
        if direction == "long":
            return state.trend_signal == "bearish"
        if direction == "short":
            return state.trend_signal == "bullish"
        return False

    def _build_indicators(self, state: ZScoreState) -> dict[str, Any]:
        """Build indicator dictionary for decision."""
        indicators = {
            "z_score": round(state.z_score or 0.0, 4),
            "rolling_mean": round(state.rolling_mean or 0.0, 2),
            "rolling_std": round(state.rolling_std or 0.0, 4),
            "daily_volatility": round(state.daily_volatility or 0.0, 4),
            "signal": state.signal,
            "strategy": "mean_reversion",
        }
        if self.config.trend_filter_enabled:
            indicators.update(
                {
                    "trend_ma": round(state.trend_ma or 0.0, 4),
                    "trend_pct": round(state.trend_pct or 0.0, 4),
                    "trend_signal": state.trend_signal,
                }
            )
        return indicators

    def calculate_position_size(
        self,
        equity: Decimal,
        current_volatility: float,
    ) -> Decimal:
        """Calculate position size using volatility targeting.

        Formula: size = (target_vol / current_vol) * equity * max_position_pct

        Args:
            equity: Account equity
            current_volatility: Current daily volatility of the asset

        Returns:
            Recommended position size in base currency units
        """
        if current_volatility <= 0:
            current_volatility = 0.01  # Default to 1% if no volatility data

        target_vol = self.config.target_daily_volatility
        max_pct = self.config.max_position_pct

        # Volatility scaling factor
        vol_scale = target_vol / current_volatility

        # Base position size
        base_size = float(equity) * max_pct

        # Apply volatility scaling (reduce size in high vol, increase in low vol)
        # But cap at max_position_pct of equity
        scaled_size = base_size * min(vol_scale, 2.0)  # Cap scaling at 2x

        # Apply maximum constraint
        max_size = float(equity) * max_pct
        final_size = min(scaled_size, max_size)

        return Decimal(str(round(final_size, 2)))

    def rehydrate(self, events: Sequence[dict[str, Any]]) -> int:
        """Restore strategy state from historical events.

        MeanReversionStrategy is stateless - it calculates Z-Score fresh
        from the recent_marks passed to decide(). No internal state
        needs restoration.

        Args:
            events: Historical events from EventStore

        Returns:
            Number of events processed (always 0 for stateless strategy)
        """
        logger.debug("MeanReversionStrategy.rehydrate called (stateless, no-op)")
        return 0
