# Backtest Entrypoints Map

Generated: 1970-01-01T00:00:00+00:00

## CLI Entrypoints
| ID | Label | Path |
|----|-------|------|
| cli_optimize_run | optimize run | `src/gpt_trader/cli/commands/optimize/run.py` |

## Script Entrypoints
| ID | Label | Path |
|----|-------|------|
| paper_trade_stress_test | run_stress_test | `scripts/analysis/paper_trade_stress_test.py` |
| golden_path_demo | golden_path_validation_demo | `scripts/analysis/golden_path_validation_demo.py` |

## Library Entrypoints
| ID | Label | Path |
|----|-------|------|
| walk_forward_run | WalkForwardOptimizer.run | `src/gpt_trader/features/optimize/walk_forward.py` |

## Backtesting Engine
| ID | Label | Path |
|----|-------|------|
| data_provider_factory | create_coinbase_data_provider | `src/gpt_trader/backtesting/data/manager.py` |
| batch_runner | BatchBacktestRunner.run_trial | `src/gpt_trader/features/optimize/runner/batch_runner.py` |
| clocked_bar_runner | ClockedBarRunner.run | `src/gpt_trader/backtesting/engine/bar_runner.py` |
| simulated_broker_engine | SimulatedBroker | `src/gpt_trader/backtesting/simulation/broker.py` |
| backtest_guarded_executor | BacktestGuardedExecutor.submit_order | `src/gpt_trader/backtesting/engine/guarded_execution.py` |

## Validation + Chaos
| ID | Label | Path |
|----|-------|------|
| decision_logger_log | DecisionLogger.log_decision | `src/gpt_trader/backtesting/validation/decision_logger.py` |
| decision_logger_export | DecisionLogger.export_to_json | `src/gpt_trader/backtesting/validation/decision_logger.py` |
| golden_path_validate | GoldenPathValidator.validate_decision | `src/gpt_trader/backtesting/validation/validator.py` |
| golden_path_report | GoldenPathValidator.generate_report | `src/gpt_trader/backtesting/validation/validator.py` |
| replay_decisions | replay_decisions_through_simulator | `src/gpt_trader/backtesting/validation/validator.py` |
| chaos_engine_add | ChaosEngine.add_scenario | `src/gpt_trader/backtesting/chaos/engine.py` |
| chaos_engine_process_candle | ChaosEngine.process_candle | `src/gpt_trader/backtesting/chaos/engine.py` |
| chaos_engine_process_order | ChaosEngine.process_order | `src/gpt_trader/backtesting/chaos/engine.py` |
| chaos_engine_apply_latency | ChaosEngine.apply_latency | `src/gpt_trader/backtesting/chaos/engine.py` |
| chaos_scenario_factories | create_*_scenario | `src/gpt_trader/backtesting/chaos/scenarios.py` |

## Outputs
| ID | Label | Path |
|----|-------|------|
| engine_backtest_result | BacktestResult | `src/gpt_trader/backtesting/types.py` |
| validation_report | ValidationReport | `src/gpt_trader/backtesting/types.py` |
| decision_log_json | Decision log JSON | `src/gpt_trader/backtesting/validation/decision_logger.py` |
| chaos_event | ChaosEvent | `src/gpt_trader/backtesting/chaos/engine.py` |

## Edges
| From | To | Description |
|------|----|-------------|
| cli_optimize_run | data_provider_factory | build data provider |
| data_provider_factory | batch_runner | IHistoricalDataProvider |
| cli_optimize_run | batch_runner | start trials |
| batch_runner | clocked_bar_runner | run loop |
| clocked_bar_runner | simulated_broker_engine | bars/quotes |
| simulated_broker_engine | backtest_guarded_executor | broker context |
| backtest_guarded_executor | decision_logger_log | log decisions |
| clocked_bar_runner | chaos_engine_process_candle | optional hook |
| simulated_broker_engine | chaos_engine_process_order | optional hook |
| simulated_broker_engine | chaos_engine_apply_latency | optional hook |
| simulated_broker_engine | engine_backtest_result | summary result |
| walk_forward_run | clocked_bar_runner | backtest window |
| walk_forward_run | simulated_broker_engine | simulate trades |
| walk_forward_run | engine_backtest_result | window result |
| paper_trade_stress_test | clocked_bar_runner | stress loop |
| paper_trade_stress_test | simulated_broker_engine | simulate broker |
| golden_path_demo | decision_logger_log | log decisions |
| golden_path_demo | golden_path_validate | validate decisions |
| golden_path_demo | golden_path_report | generate report |
| decision_logger_log | decision_logger_export | export decisions |
| decision_logger_export | decision_log_json | write JSON |
| decision_logger_log | replay_decisions | recorded decisions |
| replay_decisions | golden_path_validate | validate decisions |
| golden_path_validate | golden_path_report | collect divergences |
| golden_path_report | validation_report | report |
| chaos_scenario_factories | chaos_engine_add | scenario config |
| chaos_engine_add | chaos_engine_process_candle | inject candles |
| chaos_engine_add | chaos_engine_process_order | inject orders |
| chaos_engine_add | chaos_engine_apply_latency | inject latency |
| chaos_engine_process_candle | chaos_event | record events |
| chaos_engine_process_order | chaos_event | record events |

## Notes
- CLI optimize run drives BatchBacktestRunner for optimization trials.
- WalkForwardOptimizer and paper_trade_stress_test use ClockedBarRunner + SimulatedBroker.
- Research backtests rely on EventStore → HistoricalDataLoader → BacktestSimulator.
- Golden-path validation and chaos scenarios provide robustness checks and reports.
- ChaosEngine hooks are optional; integrate via ClockedBarRunner/SimulatedBroker if enabled.
