"""Batch backtest runner for optimization trials."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from gpt_trader.backtesting.engine.bar_runner import ClockedBarRunner, IHistoricalDataProvider
from gpt_trader.backtesting.metrics.risk import RiskMetrics, calculate_risk_metrics
from gpt_trader.backtesting.metrics.statistics import TradeStatistics, calculate_trade_statistics
from gpt_trader.backtesting.simulation.broker import SimulatedBroker
from gpt_trader.backtesting.types import BacktestResult, SimulationConfig
from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.live_trade.strategies.base import StrategyProtocol
from gpt_trader.features.live_trade.strategies.baseline.strategy import Action, Decision
from gpt_trader.features.optimize.objectives.base import ObjectiveFunction
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="batch_runner")


@dataclass
class TrialResult:
    """Result of a single optimization trial."""

    trial_number: int
    parameters: dict[str, Any]
    objective_value: float
    is_feasible: bool
    backtest_result: BacktestResult | None = None
    risk_metrics: RiskMetrics | None = None
    trade_statistics: TradeStatistics | None = None
    duration_seconds: float = 0.0
    error_message: str | None = None


class BatchBacktestRunner:
    """
    Executes backtests for optimization trials.

    Responsible for:
    1. Configuring strategy and simulation from parameters
    2. Running the backtest loop (feeding data to broker/strategy)
    3. Executing strategy decisions
    4. Calculating metrics
    5. Evaluating the objective function
    """

    def __init__(
        self,
        data_provider: IHistoricalDataProvider,
        symbols: list[str],
        granularity: str,
        start_date: datetime,
        end_date: datetime,
        strategy_factory: Callable[[dict[str, Any]], StrategyProtocol],
        objective: ObjectiveFunction,
        base_simulation_config: SimulationConfig | None = None,
    ):
        """
        Initialize batch runner.

        Args:
            data_provider: Provider for historical candle data
            symbols: List of symbols to trade
            granularity: Candle granularity (e.g., "FIVE_MINUTE")
            start_date: Backtest start date
            end_date: Backtest end date
            strategy_factory: Function creating a strategy instance from a config dict
            objective: Objective function to optimize
            base_simulation_config: Base simulation configuration (overridden by params)
        """
        self.data_provider = data_provider
        self.symbols = symbols
        self.granularity = granularity
        self.start_date = start_date
        self.end_date = end_date
        self.strategy_factory = strategy_factory
        self.objective = objective
        self.base_simulation_config = base_simulation_config or SimulationConfig(
            start_date=start_date,
            end_date=end_date,
            granularity=granularity,
            initial_equity_usd=Decimal("100000"),
        )

    async def run_trial(self, trial_number: int, parameters: dict[str, Any]) -> TrialResult:
        """
        Run a single backtest trial.

        Args:
            trial_number: Trial identifier
            parameters: Dictionary of parameters for this trial

        Returns:
            Result of the trial including objective value and metrics
        """
        start_time = time.time()

        try:
            # 1. Separate parameters
            strategy_params = {
                k: v for k, v in parameters.items() if k not in self._get_simulation_param_names()
            }
            simulation_params = {
                k: v for k, v in parameters.items() if k in self._get_simulation_param_names()
            }

            # 2. Build configurations
            strategy = self.strategy_factory(strategy_params)

            sim_config = self._update_simulation_config(
                self.base_simulation_config, simulation_params
            )

            # 3. Create broker and runner
            broker = SimulatedBroker(
                initial_equity_usd=sim_config.initial_equity_usd,
                fee_tier=sim_config.fee_tier,
                config=sim_config,
            )
            broker.connect()

            runner = ClockedBarRunner(
                data_provider=self.data_provider,
                symbols=self.symbols,
                granularity=self.granularity,
                start_date=self.start_date,
                end_date=self.end_date,
            )

            # 4. Run backtest loop
            async for current_time, bars, quotes in runner.run():
                # Update broker with market data
                for symbol in self.symbols:
                    if symbol in bars:
                        broker.update_bar(symbol, bars[symbol])

                broker.update_equity_curve()

                # Run strategy for each symbol
                for symbol in self.symbols:
                    await self._run_strategy_cycle(symbol, strategy, broker)

            # 5. Calculate metrics
            risk_metrics = calculate_risk_metrics(broker)
            trade_stats = calculate_trade_statistics(broker)

            # Create BacktestResult summary
            backtest_result = self._create_backtest_result(broker, risk_metrics, trade_stats)

            # 6. Evaluate objective
            is_feasible = self.objective.is_feasible(backtest_result, risk_metrics, trade_stats)
            if is_feasible:
                objective_value = self.objective.calculate(
                    backtest_result, risk_metrics, trade_stats
                )
            else:
                # Penalize infeasible solutions
                objective_value = (
                    float("-inf") if self.objective.direction == "maximize" else float("inf")
                )

            duration = time.time() - start_time

            return TrialResult(
                trial_number=trial_number,
                parameters=parameters,
                objective_value=objective_value,
                is_feasible=is_feasible,
                backtest_result=backtest_result,
                risk_metrics=risk_metrics,
                trade_statistics=trade_stats,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.error(f"Trial {trial_number} failed: {e}", exc_info=True)
            return TrialResult(
                trial_number=trial_number,
                parameters=parameters,
                objective_value=(
                    float("-inf") if self.objective.direction == "maximize" else float("inf")
                ),
                is_feasible=False,
                duration_seconds=time.time() - start_time,
                error_message=str(e),
            )

    async def _run_strategy_cycle(
        self, symbol: str, strategy: StrategyProtocol, broker: SimulatedBroker
    ) -> None:
        """Execute one strategy cycle for a symbol."""
        # Get market data
        mark = broker.get_mark_price(symbol)
        if mark is None:
            return

        product = broker.get_product(symbol)

        # Get position state
        position = broker.get_position(symbol)
        pos_state = None
        if position:
            pos_state = {
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "unrealized_pnl": position.unrealized_pnl,
                "side": position.side,
                "leverage": position.leverage,
            }

        # Get historical data (simplified: using broker's candle history)
        # Note: Strategy expects Sequence[Decimal] of marks/closes
        candles = broker.get_candles(symbol, limit=100)
        recent_marks = [c.close for c in candles]

        # Generate decision
        decision = strategy.decide(
            symbol=symbol,
            current_mark=mark,
            position_state=pos_state,
            recent_marks=recent_marks,
            equity=broker.equity,
            product=product,
        )

        # Execute decision
        await self._execute_decision(symbol, decision, broker, strategy)

    async def _execute_decision(
        self,
        symbol: str,
        decision: Decision,
        broker: SimulatedBroker,
        strategy: Any,  # typed as Any to access config if available
    ) -> None:
        """Execute the strategy decision."""
        if decision.action == Action.HOLD:
            return

        if decision.action == Action.CLOSE:
            position = broker.get_position(symbol)
            if position:
                # Close entire position
                side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
                broker.place_order(
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    quantity=abs(position.quantity),
                    reduce_only=True,
                )
            return

        # Handle BUY/SELL (Entry)
        if decision.action in (Action.BUY, Action.SELL):
            # Determine size
            # Try to get position_fraction from strategy config
            position_fraction = 0.2  # Default
            if hasattr(strategy, "config") and hasattr(strategy.config, "position_fraction"):
                if strategy.config.position_fraction:
                    position_fraction = strategy.config.position_fraction

            # Determine leverage
            leverage = 1
            if hasattr(strategy, "config"):
                if hasattr(strategy.config, "target_leverage"):
                    leverage = strategy.config.target_leverage

            equity = broker.equity
            mark = broker.get_mark_price(symbol) or Decimal("0")
            if mark == 0:
                return

            # Calculate quantity
            # quantity = (equity * fraction * leverage) / price
            target_notional = equity * Decimal(str(position_fraction)) * Decimal(str(leverage))
            quantity = target_notional / mark

            side = OrderSide.BUY if decision.action == Action.BUY else OrderSide.SELL

            try:
                broker.place_order(
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    quantity=quantity,
                    leverage=leverage,
                )
            except Exception as e:
                logger.warning(f"Failed to place order: {e}")

    def _get_simulation_param_names(self) -> set[str]:
        """Get names of simulation parameters."""
        return {"fee_tier", "slippage_bps", "spread_impact_pct"}

    def _update_simulation_config(
        self, base: SimulationConfig, params: dict[str, Any]
    ) -> SimulationConfig:
        """Create a new SimulationConfig with updated parameters."""
        # Create a copy with updated values
        # Using replace from dataclasses since SimulationConfig is a dataclass
        valid_fields = {f for f in base.__dataclass_fields__}
        filtered_params = {k: v for k, v in params.items() if k in valid_fields}
        return replace(base, **filtered_params)

    def _create_backtest_result(
        self, broker: SimulatedBroker, risk: RiskMetrics, stats: TradeStatistics
    ) -> BacktestResult:
        """Create BacktestResult summary."""
        return BacktestResult(
            start_date=self.start_date,
            end_date=self.end_date,
            duration_days=(self.end_date - self.start_date).days,
            initial_equity=broker._initial_equity,
            final_equity=broker.equity,
            total_return=risk.total_return_pct,
            total_return_usd=broker.equity - broker._initial_equity,
            realized_pnl=stats.total_pnl,
            unrealized_pnl=sum((p.unrealized_pnl for p in broker.positions.values()), Decimal("0")),
            funding_pnl=broker._funding_tracker.get_total_funding_pnl(),
            fees_paid=stats.total_fees_paid,
            total_trades=stats.total_trades,
            winning_trades=stats.winning_trades,
            losing_trades=stats.losing_trades,
            win_rate=stats.win_rate,
            max_drawdown=risk.max_drawdown_pct,
            max_drawdown_usd=risk.max_drawdown_usd,
            sharpe_ratio=risk.sharpe_ratio,
            sortino_ratio=risk.sortino_ratio,
            avg_position_size_usd=stats.avg_position_size_usd,
            max_position_size_usd=stats.max_position_size_usd,
            avg_leverage=stats.avg_leverage,
            max_leverage=stats.max_leverage,
            avg_slippage_bps=stats.avg_slippage_bps,
            limit_fill_rate=stats.limit_fill_rate,
        )
