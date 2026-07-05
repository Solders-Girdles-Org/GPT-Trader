# Config → Code Linkage Map

Generated: 1970-01-01T00:00:00+00:00

Scan root: `src/gpt_trader`

## BotConfig (top-level)
| Field | Usage count | Example files |
|-------|-------------|---------------|
| broker_calls_use_dedicated_executor | 0 | — |
| broker_hint | 0 | — |
| cfm_enabled | 0 | — |
| cfm_margin_window | 0 | — |
| cfm_max_leverage | 0 | — |
| cfm_symbols | 0 | — |
| coinbase_api_mode | 0 | — |
| coinbase_default_quote | 0 | — |
| coinbase_derivatives_type | 0 | — |
| coinbase_sandbox_enabled | 0 | — |
| coinbase_us_futures_enabled | 0 | — |
| derivatives_enabled | 0 | — |
| dry_run | 0 | — |
| enable_order_preview | 0 | — |
| environment | 0 | — |
| event_driven_paper_lane_enabled | 0 | — |
| event_store_root_override | 0 | — |
| interval | 0 | — |
| log_level | 0 | — |
| market_order_fallback | 0 | — |
| max_cycles | 0 | — |
| metadata | 0 | — |
| mock_broker | 0 | — |
| order_submission_retries_enabled | 0 | — |
| paper_fills | 0 | — |
| perps_enable_streaming | 0 | — |
| perps_paper_trading | 0 | — |
| perps_position_fraction | 0 | — |
| perps_skip_startup_reconcile | 0 | — |
| perps_stream_level | 0 | — |
| profile | 0 | — |
| reduce_only_mode | 0 | — |
| regime_switcher_trend_mode | 0 | — |
| risk_budget_runtime_seed_enabled | 0 | — |
| risk_config_path | 0 | — |
| runtime_root | 0 | — |
| spot_force_live | 0 | — |
| status_enabled | 0 | — |
| status_file | 0 | — |
| status_interval | 0 | — |
| strategy_signal_proposals_enabled | 0 | — |
| strategy_type | 0 | — |
| symbols | 0 | — |
| time_in_force | 0 | — |
| trading_modes | 0 | — |
| use_limit_orders | 0 | — |
| webhook_url | 0 | — |

## BotRiskConfig
| Field | Usage count | Example files |
|-------|-------------|---------------|
| dry_run_equity_usd | 0 | — |
| max_drawdown_pct | 0 | — |
| max_position_size | 0 | — |
| position_fraction | 0 | — |
| reduce_only_threshold | 0 | — |
| stop_loss_pct | 0 | — |
| take_profit_pct | 0 | — |
| target_leverage | 0 | — |
| trailing_stop_pct | 0 | — |

## PerpsStrategyConfig
| Field | Usage count | Example files |
|-------|-------------|---------------|
| crossover_weight | 0 | — |
| enable_shorts | 0 | — |
| force_entry_on_trend | 0 | — |
| kill_switch_enabled | 0 | — |
| long_ma_period | 0 | — |
| max_leverage | 0 | — |
| min_confidence | 0 | — |
| position_fraction | 0 | — |
| rsi_overbought | 0 | — |
| rsi_oversold | 0 | — |
| rsi_period | 0 | — |
| rsi_weight | 0 | — |
| short_ma_period | 0 | — |
| stop_loss_pct | 0 | — |
| take_profit_pct | 0 | — |
| target_leverage | 0 | — |
| trailing_stop_pct | 0 | — |
| trend_weight | 0 | — |

## MeanReversionConfig
| Field | Usage count | Example files |
|-------|-------------|---------------|
| cooldown_bars | 0 | — |
| enable_shorts | 0 | — |
| kill_switch_enabled | 0 | — |
| lookback_window | 0 | — |
| max_position_pct | 0 | — |
| stop_loss_pct | 0 | — |
| take_profit_pct | 0 | — |
| target_daily_volatility | 0 | — |
| trend_filter_enabled | 0 | — |
| trend_override_z_score | 0 | — |
| trend_threshold_pct | 0 | — |
| trend_window | 0 | — |
| z_score_entry_threshold | 0 | — |
| z_score_exit_threshold | 0 | — |

## HealthThresholdsConfig
| Field | Usage count | Example files |
|-------|-------------|---------------|
| broker_latency_ms_crit | 0 | — |
| broker_latency_ms_warn | 0 | — |
| guard_trip_count_crit | 0 | — |
| guard_trip_count_warn | 0 | — |
| market_data_staleness_seconds_crit | 0 | — |
| market_data_staleness_seconds_warn | 0 | — |
| missing_decision_id_count_crit | 0 | — |
| missing_decision_id_count_warn | 0 | — |
| order_error_rate_crit | 0 | — |
| order_error_rate_warn | 0 | — |
| order_retry_rate_crit | 0 | — |
| order_retry_rate_warn | 0 | — |
| ws_staleness_seconds_crit | 0 | — |
| ws_staleness_seconds_warn | 0 | — |

## Alias Fields
| Alias | Canonical target | Usage count | Example files |
|-------|------------------|-------------|---------------|
| active_enable_shorts | strategy.enable_shorts / mean_reversion.enable_shorts | 0 | — |
| is_cfm_only | trading_modes contains only cfm | 0 | — |
| is_hybrid_mode | trading_modes contains spot+cfm | 0 | — |
| is_spot_only | trading_modes contains only spot | 0 | — |
| long_ma | strategy.long_ma_period | 0 | — |
| short_ma | strategy.short_ma_period | 0 | — |
| target_leverage | risk.target_leverage | 0 | — |
| trailing_stop_pct | strategy.trailing_stop_pct or risk.trailing_stop_pct | 0 | — |

## Notes
- Scan uses simple regex matching (config.<field>) across src/gpt_trader.
- Dynamic config access or indirect usage may not appear in results.
