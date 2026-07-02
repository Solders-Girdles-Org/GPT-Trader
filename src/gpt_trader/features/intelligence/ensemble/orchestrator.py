"""
Ensemble orchestrator for multi-signal trading.

Orchestrates multiple trading strategies with regime-aware dynamic weighting
and confidence-based voting to produce unified trading decisions.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from gpt_trader.features.intelligence.contracts import (
    Action,
    Decision,
    MarketDataContext,
    Product,
    TradingStrategy,
)

from ..regime.detector import MarketRegimeDetector
from ..regime.models import RegimeConfig, RegimeState, RegimeType
from .adaptive import BayesianWeightConfig, BayesianWeightUpdater
from .models import EnsembleConfig, StrategyVote
from .voting import VotingMechanism
from .weighting import DynamicWeightCalculator


class EnsembleOrchestrator:
    """Orchestrates multiple strategies with regime-aware dynamic weighting.

    Implements the TradingStrategy protocol so it can be used as a
    drop-in replacement in TradingEngine.

    Features:
    - Wraps multiple child strategies
    - Regime-aware weight adjustment
    - Confidence-weighted voting
    - Conflict resolution via ConfidenceLeaderVoting
    - State serialization for crash recovery

    Example:
        from gpt_trader.features.live_trade.strategies.baseline import BaselinePerpsStrategy
        from gpt_trader.features.live_trade.strategies.mean_reversion import MeanReversionStrategy

        strategies = {
            "baseline": BaselinePerpsStrategy(config=strategy_config),
            "mean_reversion": MeanReversionStrategy(config=mr_config),
        }

        orchestrator = EnsembleOrchestrator(
            strategies=strategies,
            regime_detector=MarketRegimeDetector(),
            config=EnsembleConfig(),
        )

        # Use like any other strategy
        decision = orchestrator.decide(
            symbol="BTC-USD",
            current_mark=Decimal("50000"),
            position_state=None,
            recent_marks=[...],
            equity=Decimal("10000"),
            product=None,
        )
    """

    def __init__(
        self,
        strategies: dict[str, TradingStrategy],
        regime_detector: MarketRegimeDetector | None = None,
        config: EnsembleConfig | None = None,
        regime_config: RegimeConfig | None = None,
    ):
        """Initialize ensemble orchestrator.

        Args:
            strategies: Dictionary mapping strategy names to instances
            regime_detector: Regime detector (created if None)
            config: Ensemble configuration
            regime_config: Regime detection configuration
        """
        self.strategies = strategies
        self.config = config or EnsembleConfig()

        # Create regime detector if not provided
        if regime_detector is not None:
            self.regime_detector = regime_detector
        else:
            self.regime_detector = MarketRegimeDetector(regime_config or RegimeConfig())

        # Create voting mechanism
        self.voter = VotingMechanism.create(self.config.voting_method)

        # Create weight calculator
        self.weight_calculator = DynamicWeightCalculator(
            base_weights=self.config.base_weights,
            regime_adjustments=self.config.regime_weight_adjustments,
        )

        # Create Bayesian weight updater if adaptive learning enabled
        self._bayesian_updater: BayesianWeightUpdater | None = None
        if self.config.enable_adaptive_learning:
            bayesian_config = BayesianWeightConfig(
                smoothing=self.config.adaptive_smoothing,
                min_weight=self.config.adaptive_min_weight,
                max_weight=self.config.adaptive_max_weight,
            )
            self._bayesian_updater = BayesianWeightUpdater(
                strategy_names=list(strategies.keys()),
                base_weights=self.config.base_weights,
                config=bayesian_config,
            )

        # Track last regime for logging
        self._last_regime: dict[str, RegimeState] = {}

        # Track pending decisions for outcome recording
        self._pending_outcomes: dict[str, dict[str, Any]] = {}

    def decide(
        self,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Product | None,
        market_data: MarketDataContext | None = None,
        candles: Sequence[Any] | None = None,
    ) -> Decision:
        """Generate ensemble decision by aggregating strategy votes.

        Implements the TradingStrategy protocol.

        Flow:
        1. Update regime detector
        2. Calculate dynamic weights based on regime
        3. Collect strategy decisions
        4. Aggregate via voting mechanism
        5. Apply crisis adjustments if needed
        6. Return final decision with metadata

        Args:
            symbol: Trading pair symbol
            current_mark: Current price
            position_state: Current position info, or None
            recent_marks: Historical prices
            equity: Account equity
            product: Product specification
            market_data: Optional enhanced market data (orderbook depth, trade flow)
            candles: Historical candles (optional)

        Returns:
            Aggregated decision with ensemble metadata
        """
        # 1. Update regime detection
        regime_state = self.regime_detector.update(symbol, current_mark)
        self._last_regime[symbol] = regime_state

        # 2. Calculate dynamic weights based on regime
        strategy_names = list(self.strategies.keys())

        # Use Bayesian weights if adaptive learning is enabled
        if self._bayesian_updater is not None:
            weights = self._bayesian_updater.get_weights(
                regime=regime_state.regime,
                confidence=regime_state.confidence,
            )
        else:
            weights = self.weight_calculator.calculate(
                regime=regime_state.regime,
                confidence=regime_state.confidence,
                strategy_names=strategy_names,
            )

        # 3. Collect votes from all strategies
        votes: list[StrategyVote] = []
        for name, strategy in self.strategies.items():
            try:
                kwargs = self._build_decide_kwargs(
                    strategy=strategy,
                    symbol=symbol,
                    current_mark=current_mark,
                    position_state=position_state,
                    recent_marks=recent_marks,
                    equity=equity,
                    product=product,
                    market_data=market_data,
                    candles=candles,
                )
                decision = strategy.decide(**kwargs)
                votes.append(
                    StrategyVote(
                        strategy_name=name,
                        decision=decision,
                        weight=weights.get(name, 0.0),
                    )
                )
            except Exception as e:
                # Log error but continue with other strategies
                # In production, this would use proper logging
                votes.append(
                    StrategyVote(
                        strategy_name=name,
                        decision=Decision(
                            action=Action.HOLD,
                            reason=f"Strategy error: {e}",
                            confidence=0.0,
                            indicators={},
                        ),
                        weight=0.0,  # Zero weight for errored strategy
                    )
                )

        # 4. Aggregate votes
        final_decision = self.voter.aggregate(votes, regime_state)

        # 5. Apply crisis adjustments
        final_decision = self._apply_crisis_adjustment(
            decision=final_decision,
            regime_state=regime_state,
            position_state=position_state,
        )

        # 6. Check minimum confidence threshold
        if final_decision.confidence < self.config.min_ensemble_confidence:
            final_decision = Decision(
                action=Action.HOLD,
                reason=f"Low ensemble confidence ({final_decision.confidence:.2f} < {self.config.min_ensemble_confidence})",
                confidence=final_decision.confidence,
                indicators=final_decision.indicators,
            )

        # 7. Enrich decision with ensemble metadata
        ensemble_meta = {
            "regime": regime_state.to_dict(),
            "weights": {k: round(v, 4) for k, v in weights.items()},
            "votes": [v.to_dict() for v in votes],
            "voting_method": self.config.voting_method,
            "adaptive_learning": self.config.enable_adaptive_learning,
        }

        # Add Bayesian performance info if available
        if self._bayesian_updater is not None:
            success_probs = {}
            for name in self.strategies.keys():
                record = self._bayesian_updater._performance.get(name)
                if record is not None:
                    success_probs[name] = round(
                        record.get_success_probability(regime_state.regime), 4
                    )
                else:
                    success_probs[name] = 0.5
            ensemble_meta["strategy_success_probs"] = success_probs

        final_decision.indicators["ensemble"] = ensemble_meta

        # Store pending outcome for later recording
        if final_decision.action != Action.HOLD:
            self._pending_outcomes[symbol] = {
                "regime": regime_state.regime,
                "action": final_decision.action,
                "price": current_mark,
                "votes": {v.strategy_name: v.decision.action for v in votes},
            }

        return final_decision

    def _build_decide_kwargs(
        self,
        *,
        strategy: TradingStrategy,
        symbol: str,
        current_mark: Decimal,
        position_state: dict[str, Any] | None,
        recent_marks: Sequence[Decimal],
        equity: Decimal,
        product: Product | None,
        market_data: MarketDataContext | None,
        candles: Sequence[Any] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "symbol": symbol,
            "current_mark": current_mark,
            "position_state": position_state,
            "recent_marks": recent_marks,
            "equity": equity,
            "product": product,
        }

        try:
            signature = inspect.signature(strategy.decide)
        except (TypeError, ValueError):
            kwargs["market_data"] = market_data
            kwargs["candles"] = candles
            return kwargs

        if any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
        ):
            kwargs["market_data"] = market_data
            kwargs["candles"] = candles
            return kwargs

        if "market_data" in signature.parameters:
            kwargs["market_data"] = market_data
        if "candles" in signature.parameters:
            kwargs["candles"] = candles
        return kwargs

    def _apply_crisis_adjustment(
        self,
        decision: Decision,
        regime_state: RegimeState,
        position_state: dict[str, Any] | None,
    ) -> Decision:
        """Apply crisis-mode adjustments to decision.

        In crisis mode, we scale down position sizes but don't halt trading
        (as per user's preference for "scaled_down" behavior).
        """
        if not regime_state.is_crisis():
            return decision

        # Crisis mode - adjust based on configuration
        if self.config.crisis_behavior == "scaled_down":
            # Just note the crisis in the reason, actual position scaling
            # happens in the position sizing layer
            if decision.action in (Action.BUY, Action.SELL):
                return Decision(
                    action=decision.action,
                    reason=f"[CRISIS MODE] {decision.reason}",
                    confidence=decision.confidence * self.config.crisis_scale_factor,
                    indicators={
                        **decision.indicators,
                        "crisis_mode": True,
                        "crisis_scale": self.config.crisis_scale_factor,
                    },
                )

        elif self.config.crisis_behavior == "reduce_only":
            # Only allow closing positions
            if position_state is not None and decision.action in (Action.BUY, Action.SELL):
                return Decision(
                    action=Action.HOLD,
                    reason=f"[CRISIS] Reduce-only mode, blocking: {decision.reason}",
                    confidence=0.3,
                    indicators={**decision.indicators, "crisis_blocked": True},
                )

        elif self.config.crisis_behavior == "halt":
            # Stop all trading
            return Decision(
                action=Action.HOLD,
                reason="[CRISIS] Trading halted",
                confidence=0.0,
                indicators={**decision.indicators, "crisis_halted": True},
            )

        return decision

    def get_regime(self, symbol: str) -> RegimeState:
        """Get current regime for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current regime state
        """
        return self._last_regime.get(symbol, RegimeState.unknown())

    def rehydrate(self, events: Sequence[dict[str, Any]]) -> int:
        """Restore state from historical events.

        Args:
            events: List of persisted events

        Returns:
            Number of events processed
        """
        count = 0

        # Rehydrate regime detector
        for event in events:
            if event.get("type") == "regime_state_snapshot":
                data = event.get("data", {})
                if data:
                    self.regime_detector.deserialize_state(data)
                    count += 1
                    break  # Use most recent snapshot

        # Rehydrate child strategies
        for strategy in self.strategies.values():
            if hasattr(strategy, "rehydrate"):
                count += strategy.rehydrate(events)

        return count

    def serialize_state(self) -> dict[str, Any]:
        """Serialize ensemble state for persistence."""
        result = {
            "regime_detector": self.regime_detector.serialize_state(),
            "strategies": {
                name: (s.serialize_state() if hasattr(s, "serialize_state") else {})
                for name, s in self.strategies.items()
            },
            "last_regimes": {
                symbol: state.to_dict() for symbol, state in self._last_regime.items()
            },
        }

        # Include Bayesian updater state if present
        if self._bayesian_updater is not None:
            result["bayesian_updater"] = self._bayesian_updater.serialize()

        return result

    def deserialize_state(self, state: dict[str, Any]) -> None:
        """Restore state from serialized data."""
        # Restore regime detector
        if "regime_detector" in state:
            self.regime_detector.deserialize_state(state["regime_detector"])

        # Restore strategy states
        strategy_states = state.get("strategies", {})
        for name, strategy_state in strategy_states.items():
            strategy = self.strategies.get(name)
            if strategy is not None and hasattr(strategy, "deserialize_state"):
                deserialize_fn = getattr(strategy, "deserialize_state")
                deserialize_fn(strategy_state)

        # Restore Bayesian updater state if present
        if "bayesian_updater" in state and self._bayesian_updater is not None:
            self._bayesian_updater = BayesianWeightUpdater.deserialize(state["bayesian_updater"])

    def get_strategy_weights(self, symbol: str) -> dict[str, float]:
        """Get current strategy weights for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current weights per strategy
        """
        regime_state = self._last_regime.get(symbol, RegimeState.unknown())

        if self._bayesian_updater is not None:
            return self._bayesian_updater.get_weights(
                regime=regime_state.regime,
                confidence=regime_state.confidence,
            )

        return self.weight_calculator.calculate(
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            strategy_names=list(self.strategies.keys()),
        )

    def record_trade_outcome(
        self,
        symbol: str,
        is_success: bool,
        pnl: float = 0.0,
    ) -> None:
        """Record the outcome of a trade for adaptive learning.

        Should be called after a trade is closed to update strategy
        performance statistics and adjust weights accordingly.

        Args:
            symbol: Trading symbol
            is_success: Whether the trade was profitable
            pnl: Profit/loss amount

        Example:
            # After closing a profitable trade
            orchestrator.record_trade_outcome("BTC-USD", is_success=True, pnl=150.0)
        """
        if self._bayesian_updater is None:
            return

        # Get pending outcome info
        pending = self._pending_outcomes.pop(symbol, None)
        if pending is None:
            return

        regime = pending.get("regime")
        votes = pending.get("votes", {})

        # Record outcome for each strategy that voted for the action
        for strategy_name, action in votes.items():
            # Only record for strategies that agreed with the final decision
            # or that had the same directional vote
            if action == pending.get("action"):
                # Ensure regime is valid type
                if isinstance(regime, RegimeType):
                    self._bayesian_updater.record_outcome(
                        strategy_name=strategy_name,
                        regime=regime,
                        is_success=is_success,
                        pnl=pnl,
                    )

    def get_strategy_performance(self) -> dict[str, dict[str, Any]]:
        """Get performance statistics for all strategies.

        Returns:
            Dict mapping strategy names to performance metrics
        """
        if self._bayesian_updater is None:
            return {}

        return self._bayesian_updater.get_all_performance()


__all__ = ["EnsembleOrchestrator"]
