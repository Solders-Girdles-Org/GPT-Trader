"""Walk-Forward Analysis (WFA) for robust strategy optimization.

Walk-Forward Analysis prevents overfitting by:
1. Splitting data into rolling train/test windows
2. Optimizing parameters on each train (in-sample) window
3. Validating on the subsequent test (out-of-sample) window
4. Aggregating out-of-sample results for unbiased performance estimation

Example:
    For 12 months of data with 3-month train, 1-month test:
    - Window 1: Train Jan-Mar, Test Apr
    - Window 2: Train Feb-Apr, Test May (anchored: Train Jan-Apr, Test May)
    - Window 3: Train Mar-May, Test Jun
    - ... and so on

Usage:
    config = WalkForwardConfig(
        train_months=6,
        test_months=1,
        anchor_start=False,  # Rolling vs expanding window
    )
    optimizer = WalkForwardOptimizer(
        data_provider=data_provider,
        symbols=["BTC-PERP-USDC"],
        granularity="FIVE_MINUTE",
        strategy_factory=strategy_factory,
        objective=sharpe_objective,
        config=config,
    )
    result = await optimizer.run(
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 12, 31),
        optimization_config=opt_config,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from gpt_trader.backtesting.engine.bar_runner import (
    ClockedBarRunner,
    ConstantFundingRates,
    FundingProcessor,
    IHistoricalDataProvider,
)
from gpt_trader.backtesting.metrics.risk import RiskMetrics, calculate_risk_metrics
from gpt_trader.backtesting.metrics.statistics import (
    TradeStatistics,
    calculate_trade_statistics,
)
from gpt_trader.backtesting.simulation.broker import SimulatedBroker
from gpt_trader.backtesting.types import BacktestResult, FeeTier, SimulationConfig
from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.live_trade.strategies.base import StrategyProtocol
from gpt_trader.features.live_trade.strategies.baseline.strategy import Action, Decision
from gpt_trader.features.optimize.objectives.base import ObjectiveFunction
from gpt_trader.features.optimize.study.manager import OptimizationStudyManager
from gpt_trader.features.optimize.types import OptimizationConfig
from gpt_trader.features.optimize.walk_forward_models import (
    WalkForwardConfig,
    WalkForwardResult,
    WalkForwardWindow,
    WindowResult,
)
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="walk_forward")


def generate_windows(
    start_date: datetime,
    end_date: datetime,
    config: WalkForwardConfig,
) -> list[WalkForwardWindow]:
    """Generate walk-forward windows for the given date range.

    Args:
        start_date: Start of the full data range
        end_date: End of the full data range
        config: Walk-forward configuration

    Returns:
        List of WalkForwardWindow objects

    Raises:
        ValueError: If date range is too short for at least one window
    """
    windows: list[WalkForwardWindow] = []

    # Calculate step size (how much to move forward each window)
    step_months = config.test_months

    # For anchored windows, we track the expanding train period
    # For rolling windows, we track the sliding start
    current_train_end_for_anchored = _add_months(start_date, config.train_months)
    current_train_start = start_date
    window_id = 0

    while True:
        # Calculate window boundaries
        if config.anchor_start:
            # Anchored (expanding) window: train always starts from the beginning
            # but extends further with each window
            train_start = start_date
            train_end = current_train_end_for_anchored
        else:
            # Rolling window: train starts at current position
            train_start = current_train_start
            train_end = _add_months(train_start, config.train_months)

        # Test immediately follows train
        test_start = train_end
        test_end = _add_months(test_start, config.test_months)

        # Check if we've exceeded the data range
        if test_end > end_date:
            break

        windows.append(
            WalkForwardWindow(
                window_id=window_id,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )

        window_id += 1

        # Move forward by step size
        if config.anchor_start:
            # For anchored, expand the training window
            current_train_end_for_anchored = _add_months(
                current_train_end_for_anchored, step_months
            )
        else:
            # For rolling, slide the start forward
            current_train_start = _add_months(current_train_start, step_months)

    if len(windows) == 0:
        min_required = config.train_months + config.test_months
        raise ValueError(
            f"Date range too short for walk-forward analysis. "
            f"Need at least {min_required} months, got {_months_between(start_date, end_date):.1f} months."
        )

    return windows


def _add_months(date: datetime, months: int) -> datetime:
    """Add months to a date, handling year boundaries."""
    new_month = date.month + months
    new_year = date.year + (new_month - 1) // 12
    new_month = ((new_month - 1) % 12) + 1

    # Handle day overflow (e.g., Jan 31 + 1 month)
    import calendar

    max_day = calendar.monthrange(new_year, new_month)[1]
    new_day = min(date.day, max_day)

    return datetime(new_year, new_month, new_day, date.hour, date.minute, date.second)


def _months_between(start: datetime, end: datetime) -> float:
    """Calculate approximate months between two dates."""
    days = (end - start).days
    return days / 30.44  # Average days per month


class WalkForwardOptimizer:
    """
    Orchestrates Walk-Forward Analysis for strategy optimization.

    This class:
    1. Generates train/test windows based on configuration
    2. Runs Optuna optimization on each train window
    3. Validates best parameters on the test window
    4. Aggregates out-of-sample results for unbiased performance estimation
    """

    def __init__(
        self,
        data_provider: IHistoricalDataProvider,
        symbols: list[str],
        granularity: str,
        strategy_factory: Callable[[dict[str, Any]], StrategyProtocol],
        objective: ObjectiveFunction,
        config: WalkForwardConfig,
        base_simulation_config: SimulationConfig | None = None,
    ):
        """
        Initialize Walk-Forward Optimizer.

        Args:
            data_provider: Provider for historical candle data
            symbols: List of symbols to trade
            granularity: Candle granularity (e.g., "FIVE_MINUTE")
            strategy_factory: Function creating a strategy from parameters
            objective: Objective function to optimize
            config: Walk-forward configuration
            base_simulation_config: Base simulation settings (optional)
        """
        self.data_provider = data_provider
        self.symbols = symbols
        self.granularity = granularity
        self.strategy_factory = strategy_factory
        self.objective = objective
        self.config = config
        self.base_simulation_config = base_simulation_config

    async def run(
        self,
        start_date: datetime,
        end_date: datetime,
        optimization_config: OptimizationConfig,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> WalkForwardResult:
        """
        Run complete Walk-Forward Analysis.

        Args:
            start_date: Start of the full data range
            end_date: End of the full data range
            optimization_config: Optuna optimization configuration
            progress_callback: Optional callback(window_id, total_windows, status)

        Returns:
            WalkForwardResult with aggregated performance metrics
        """
        import time

        start_time = time.time()

        # Generate windows
        windows = generate_windows(start_date, end_date, self.config)
        total_windows = len(windows)

        logger.info(
            f"Starting Walk-Forward Analysis with {total_windows} windows "
            f"({self.config.train_months}m train, {self.config.test_months}m test)"
        )

        window_results: list[WindowResult] = []

        for window in windows:
            if progress_callback:
                progress_callback(
                    window.window_id,
                    total_windows,
                    f"Optimizing window {window.window_id + 1}/{total_windows}",
                )

            logger.info(
                f"Window {window.window_id + 1}/{total_windows}: "
                f"Train {window.train_start.date()} to {window.train_end.date()}, "
                f"Test {window.test_start.date()} to {window.test_end.date()}"
            )

            # Run optimization on train window
            result = await self._process_window(window, optimization_config)
            window_results.append(result)

        # Aggregate results
        aggregated = self._aggregate_results(window_results)

        duration = time.time() - start_time

        return WalkForwardResult(
            config=self.config,
            total_windows=total_windows,
            valid_windows=sum(1 for r in window_results if r.is_valid),
            aggregate_return_pct=aggregated["return_pct"],
            aggregate_sharpe=aggregated["sharpe"],
            aggregate_max_drawdown_pct=aggregated["max_drawdown_pct"],
            total_trades=aggregated["total_trades"],
            overall_win_rate=aggregated["win_rate"],
            window_results=window_results,
            parameter_stability=aggregated["parameter_stability"],
            performance_consistency=aggregated["performance_consistency"],
            total_duration_seconds=duration,
        )

    async def _process_window(
        self,
        window: WalkForwardWindow,
        optimization_config: OptimizationConfig,
    ) -> WindowResult:
        """Process a single walk-forward window."""
        validation_errors: list[str] = []

        # Create window-specific optimization config
        window_config = OptimizationConfig(
            study_name=f"{optimization_config.study_name}_window_{window.window_id}",
            parameter_space=optimization_config.parameter_space,
            objective_name=optimization_config.objective_name,
            direction=optimization_config.direction,
            number_of_trials=optimization_config.number_of_trials,
            timeout_seconds=optimization_config.timeout_seconds,
            parallel_jobs=optimization_config.parallel_jobs,
            sampler_type=optimization_config.sampler_type,
            pruner_type=optimization_config.pruner_type,
            seed=optimization_config.seed,
        )

        # Run optimization on train window
        study_manager = OptimizationStudyManager(window_config)
        study = study_manager.create_or_load_study()

        async def objective_wrapper(trial: Any) -> float:
            params = study_manager.suggest_parameters(trial)
            return await self._evaluate_parameters(
                params,
                window.train_start,
                window.train_end,
            )

        # Run optimization (synchronous Optuna with async evaluation)
        best_params: dict[str, Any] = {}
        best_objective = float("-inf") if self.objective.direction == "maximize" else float("inf")

        for trial_num in range(window_config.number_of_trials):
            trial = study.ask()
            params = study_manager.suggest_parameters(trial)

            try:
                objective_value = await self._evaluate_parameters(
                    params,
                    window.train_start,
                    window.train_end,
                )
                study.tell(trial, objective_value)

                # Track best
                if self.objective.direction == "maximize":
                    if objective_value > best_objective:
                        best_objective = objective_value
                        best_params = params.copy()
                else:
                    if objective_value < best_objective:
                        best_objective = objective_value
                        best_params = params.copy()

            except Exception as e:
                logger.warning(f"Trial {trial_num} failed: {e}")
                study.tell(
                    trial, float("-inf") if self.objective.direction == "maximize" else float("inf")
                )

        if not best_params:
            validation_errors.append("No valid optimization trials completed")
            return WindowResult(
                window=window,
                best_parameters={},
                optimization_trials=window_config.number_of_trials,
                best_train_objective=best_objective,
                is_valid=False,
                validation_errors=validation_errors,
            )

        # Evaluate best parameters on test (out-of-sample) window
        test_result, test_risk, test_stats = await self._run_backtest(
            best_params,
            window.test_start,
            window.test_end,
        )

        # Validate test results
        if test_stats.total_trades < self.config.min_trades_per_window:
            validation_errors.append(
                f"Insufficient trades in test window: {test_stats.total_trades} < {self.config.min_trades_per_window}"
            )

        # Calculate test objective value
        test_objective = self.objective.calculate(test_result, test_risk, test_stats)

        return WindowResult(
            window=window,
            best_parameters=best_params,
            optimization_trials=len(study.trials),
            best_train_objective=best_objective,
            test_result=test_result,
            test_risk_metrics=test_risk,
            test_trade_stats=test_stats,
            test_objective_value=test_objective,
            is_valid=len(validation_errors) == 0,
            validation_errors=validation_errors,
        )

    async def _evaluate_parameters(
        self,
        parameters: dict[str, Any],
        start_date: datetime,
        end_date: datetime,
    ) -> float:
        """Evaluate parameters on a date range and return objective value."""
        result, risk, stats = await self._run_backtest(parameters, start_date, end_date)

        if not self.objective.is_feasible(result, risk, stats):
            return float("-inf") if self.objective.direction == "maximize" else float("inf")

        return self.objective.calculate(result, risk, stats)

    async def _run_backtest(
        self,
        parameters: dict[str, Any],
        start_date: datetime,
        end_date: datetime,
    ) -> tuple[BacktestResult, RiskMetrics, TradeStatistics]:
        """Run a backtest with given parameters and date range."""
        # Create simulation config
        sim_config = SimulationConfig(
            start_date=start_date,
            end_date=end_date,
            granularity=self.granularity,
            initial_equity_usd=Decimal("100000"),
            fee_tier=FeeTier.TIER_2,
            enable_funding_pnl=True,
        )

        if self.base_simulation_config:
            # Override with base config values
            sim_config = SimulationConfig(
                start_date=start_date,
                end_date=end_date,
                granularity=self.granularity,
                initial_equity_usd=self.base_simulation_config.initial_equity_usd,
                fee_tier=self.base_simulation_config.fee_tier,
                slippage_bps=self.base_simulation_config.slippage_bps,
                spread_impact_pct=self.base_simulation_config.spread_impact_pct,
                enable_funding_pnl=self.base_simulation_config.enable_funding_pnl,
                funding_rates_8h=self.base_simulation_config.funding_rates_8h,
            )

        # Create broker
        broker = SimulatedBroker(
            initial_equity_usd=sim_config.initial_equity_usd,
            fee_tier=sim_config.fee_tier,
            config=sim_config,
        )
        broker.connect()

        # Create strategy from parameters
        strategy = self.strategy_factory(parameters)

        # Create bar runner
        runner = ClockedBarRunner(
            data_provider=self.data_provider,
            symbols=self.symbols,
            granularity=self.granularity,
            start_date=start_date,
            end_date=end_date,
        )

        # Optional: funding processor
        funding_processor = None
        if sim_config.enable_funding_pnl and sim_config.funding_rates_8h:
            rate_provider = ConstantFundingRates(rates_8h=sim_config.funding_rates_8h)
            funding_processor = FundingProcessor(
                rate_provider=rate_provider,
                accrual_interval_hours=sim_config.funding_accrual_hours,
                enabled=True,
            )

        # Run backtest
        async for current_time, bars, quotes in runner.run():
            # Update broker with market data
            for symbol in self.symbols:
                if symbol in bars:
                    broker.update_bar(symbol, bars[symbol])
                if symbol in quotes:
                    broker._current_quote[symbol] = quotes[symbol]

            broker._simulation_time = current_time
            broker.update_equity_curve()

            # Process funding if enabled
            if funding_processor:
                funding_processor.process_funding(
                    broker=broker,
                    current_time=current_time,
                    symbols=self.symbols,
                )

            # Run strategy
            for symbol in self.symbols:
                await self._run_strategy_cycle(symbol, strategy, broker)

        # Calculate metrics
        risk_metrics = calculate_risk_metrics(broker)
        trade_stats = calculate_trade_statistics(broker)
        backtest_result = self._create_backtest_result(
            broker, risk_metrics, trade_stats, start_date, end_date
        )

        return backtest_result, risk_metrics, trade_stats

    async def _run_strategy_cycle(
        self,
        symbol: str,
        strategy: StrategyProtocol,
        broker: SimulatedBroker,
    ) -> None:
        """Execute one strategy cycle for a symbol."""
        mark = broker.get_mark_price(symbol)
        if mark is None:
            return

        product = broker.get_product(symbol)
        position = broker.get_position(symbol)

        pos_state = None
        if position:
            pos_state = {
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "unrealized_pnl": position.unrealized_pnl,
                "side": position.side,
                "leverage": getattr(position, "leverage", 1),
            }

        candles = broker.get_candles(symbol, limit=100)
        recent_marks = [c.close for c in candles]

        decision = strategy.decide(
            symbol=symbol,
            current_mark=mark,
            position_state=pos_state,
            recent_marks=recent_marks,
            equity=broker.equity,
            product=product,
        )

        await self._execute_decision(symbol, decision, broker, strategy)

    async def _execute_decision(
        self,
        symbol: str,
        decision: Decision,
        broker: SimulatedBroker,
        strategy: Any,
    ) -> None:
        """Execute the strategy decision."""
        if decision.action == Action.HOLD:
            return

        if decision.action == Action.CLOSE:
            position = broker.get_position(symbol)
            if position:
                side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
                broker.place_order(
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    quantity=abs(position.quantity),
                    reduce_only=True,
                )
            return

        if decision.action in (Action.BUY, Action.SELL):
            position_fraction = Decimal("0.2")
            leverage = 1

            if hasattr(strategy, "config"):
                if (
                    hasattr(strategy.config, "position_fraction")
                    and strategy.config.position_fraction
                ):
                    position_fraction = Decimal(str(strategy.config.position_fraction))
                if hasattr(strategy.config, "target_leverage"):
                    leverage = strategy.config.target_leverage

            mark = broker.get_mark_price(symbol) or Decimal("0")
            if mark == 0:
                return

            target_notional = broker.equity * position_fraction * Decimal(str(leverage))
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

    def _create_backtest_result(
        self,
        broker: SimulatedBroker,
        risk: RiskMetrics,
        stats: TradeStatistics,
        start_date: datetime,
        end_date: datetime,
    ) -> BacktestResult:
        """Create BacktestResult from broker state."""
        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            duration_days=(end_date - start_date).days,
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

    def _aggregate_results(
        self,
        window_results: list[WindowResult],
    ) -> dict[str, Any]:
        """Aggregate results from all windows."""
        valid_results = [r for r in window_results if r.is_valid and r.test_result]

        if not valid_results:
            return {
                "return_pct": Decimal("0"),
                "sharpe": None,
                "max_drawdown_pct": Decimal("0"),
                "total_trades": 0,
                "win_rate": Decimal("0"),
                "parameter_stability": {},
                "performance_consistency": 0.0,
            }

        # Aggregate returns (chain them)
        cumulative_return = Decimal("1")
        for r in valid_results:
            if r.test_result:
                window_return = Decimal("1") + r.test_result.total_return / Decimal("100")
                cumulative_return *= window_return

        aggregate_return_pct = (cumulative_return - Decimal("1")) * Decimal("100")

        # Aggregate other metrics (average)
        sharpe_values = [
            r.test_risk_metrics.sharpe_ratio
            for r in valid_results
            if r.test_risk_metrics and r.test_risk_metrics.sharpe_ratio is not None
        ]
        aggregate_sharpe = (
            Decimal(str(sum(float(s) for s in sharpe_values) / len(sharpe_values)))
            if sharpe_values
            else None
        )

        max_dd_values = [r.test_result.max_drawdown for r in valid_results if r.test_result]
        aggregate_max_dd = max(max_dd_values) if max_dd_values else Decimal("0")

        total_trades = sum(
            r.test_trade_stats.total_trades for r in valid_results if r.test_trade_stats
        )

        winning_trades = sum(
            r.test_trade_stats.winning_trades for r in valid_results if r.test_trade_stats
        )

        win_rate = (
            Decimal(str(winning_trades / total_trades * 100)) if total_trades > 0 else Decimal("0")
        )

        # Parameter stability (variance across windows)
        parameter_stability = self._calculate_parameter_stability(valid_results)

        # Performance consistency (correlation between train and test)
        performance_consistency = self._calculate_performance_consistency(valid_results)

        return {
            "return_pct": aggregate_return_pct,
            "sharpe": aggregate_sharpe,
            "max_drawdown_pct": aggregate_max_dd,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "parameter_stability": parameter_stability,
            "performance_consistency": performance_consistency,
        }

    def _calculate_parameter_stability(
        self,
        results: list[WindowResult],
    ) -> dict[str, float]:
        """Calculate variance of each parameter across windows."""
        if len(results) < 2:
            return {}

        # Collect parameter values
        param_values: dict[str, list[float]] = {}
        for r in results:
            for name, value in r.best_parameters.items():
                if isinstance(value, (int, float, Decimal)):
                    if name not in param_values:
                        param_values[name] = []
                    param_values[name].append(float(value))

        # Calculate coefficient of variation (normalized variance)
        stability: dict[str, float] = {}
        for name, values in param_values.items():
            if len(values) >= 2:
                import statistics

                mean = statistics.mean(values)
                if mean != 0:
                    cv = statistics.stdev(values) / abs(mean)
                    stability[name] = cv
                else:
                    stability[name] = 0.0

        return stability

    def _calculate_performance_consistency(
        self,
        results: list[WindowResult],
    ) -> float:
        """Calculate correlation between train and test performance."""
        if len(results) < 3:
            return 0.0

        train_values = [r.best_train_objective for r in results]
        test_values = [
            r.test_objective_value for r in results if r.test_objective_value is not None
        ]

        if len(train_values) != len(test_values) or len(train_values) < 3:
            return 0.0

        # Calculate Pearson correlation
        import statistics

        mean_train = statistics.mean(train_values)
        mean_test = statistics.mean(test_values)

        numerator = sum(
            (t - mean_train) * (o - mean_test) for t, o in zip(train_values, test_values)
        )

        std_train = statistics.stdev(train_values)
        std_test = statistics.stdev(test_values)

        if std_train == 0 or std_test == 0:
            return 0.0

        correlation = numerator / ((len(train_values) - 1) * std_train * std_test)
        return correlation
