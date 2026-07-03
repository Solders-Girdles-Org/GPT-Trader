# Market Data Flow Map

Generated: 1970-01-01T00:00:00+00:00

## REST Polling
| ID | Label | Path |
|----|-------|------|
| engine_cycle | TradingEngine._cycle | `src/gpt_trader/features/live_trade/engines/strategy.py` |
| fetch_batch_tickers | TradingEngine._fetch_batch_tickers | `src/gpt_trader/features/live_trade/engines/strategy.py` |
| broker_get_tickers | Broker.get_tickers (optional) | `src/gpt_trader/features/brokerages/coinbase/rest/product_service.py` |
| broker_get_ticker | BrokerProtocol.get_ticker | `src/gpt_trader/features/brokerages/core/protocols.py` |
| broker_get_candles | BrokerProtocol.get_candles | `src/gpt_trader/features/brokerages/core/protocols.py` |
| process_symbol | TradingEngine._process_symbol | `src/gpt_trader/features/live_trade/engines/strategy.py` |

## WebSocket Streaming
| ID | Label | Path |
|----|-------|------|
| start_streaming | start_streaming_background | `src/gpt_trader/features/live_trade/engines/telemetry_streaming.py` |
| run_stream_loop | telemetry_streaming._run_stream_loop | `src/gpt_trader/features/live_trade/engines/telemetry_streaming.py` |
| stream_orderbook | WebSocketClientMixin.stream_orderbook | `src/gpt_trader/features/brokerages/coinbase/client/websocket_mixin.py` |
| stream_trades | WebSocketClientMixin.stream_trades | `src/gpt_trader/features/brokerages/coinbase/client/websocket_mixin.py` |
| ws_events | ws_events (OrderbookUpdate/TradeEvent) | `src/gpt_trader/features/brokerages/coinbase/ws_events.py` |
| update_mark_metrics | update_mark_and_metrics | `src/gpt_trader/features/live_trade/engines/telemetry_health.py` |
| update_orderbook_snapshot | update_orderbook_snapshot | `src/gpt_trader/features/live_trade/engines/telemetry_health.py` |
| update_trade_aggregator | update_trade_aggregator | `src/gpt_trader/features/live_trade/engines/telemetry_health.py` |

## Runtime State
| ID | Label | Path |
|----|-------|------|
| price_tick_store | PriceTickStore | `src/gpt_trader/features/recorder/price_tick_store.py` |
| runtime_mark_windows | RuntimeStateProtocol.mark_windows | `src/gpt_trader/app/protocols.py` |
| runtime_orderbook_snapshots | RuntimeStateProtocol.orderbook_snapshots | `src/gpt_trader/app/protocols.py` |
| runtime_trade_aggregators | RuntimeStateProtocol.trade_aggregators | `src/gpt_trader/app/protocols.py` |

## Risk + Staleness
| ID | Label | Path |
|----|-------|------|
| risk_last_mark_update | LiveRiskManager.last_mark_update | `src/gpt_trader/features/live_trade/risk/manager/__init__.py` |
| risk_check_mark_staleness | LiveRiskManager.check_mark_staleness | `src/gpt_trader/features/live_trade/risk/manager/__init__.py` |

## Event Store
| ID | Label | Path |
|----|-------|------|
| emit_metric | emit_metric | `src/gpt_trader/utilities/telemetry.py` |
| emit_orderbook_snapshot | emit_orderbook_snapshot | `src/gpt_trader/features/live_trade/engines/telemetry_health.py` |
| emit_trade_flow_summary | emit_trade_flow_summary | `src/gpt_trader/features/live_trade/engines/telemetry_health.py` |
| event_store_append_metric | EventStoreProtocol.append_metric | `src/gpt_trader/app/protocols.py` |
| event_store_append | EventStoreProtocol.append | `src/gpt_trader/app/protocols.py` |

## Strategy Inputs
| ID | Label | Path |
|----|-------|------|
| strategy_decide | Strategy.decide | `src/gpt_trader/features/live_trade/strategies/base.py` |

## Edges
| From | To | Description |
|------|----|-------------|
| engine_cycle | fetch_batch_tickers | poll tickers |
| fetch_batch_tickers | broker_get_tickers | batch request |
| fetch_batch_tickers | broker_get_ticker | fallback per symbol |
| broker_get_tickers | process_symbol | ticker map |
| broker_get_ticker | process_symbol | single ticker |
| process_symbol | broker_get_candles | candles request |
| broker_get_candles | process_symbol | candles data |
| process_symbol | price_tick_store | record mark |
| process_symbol | risk_last_mark_update | mark timestamp |
| price_tick_store | strategy_decide | recent_marks |
| process_symbol | strategy_decide | current_mark + candles |
| start_streaming | run_stream_loop | start WS loop |
| run_stream_loop | stream_orderbook | primary stream |
| run_stream_loop | stream_trades | fallback stream |
| stream_orderbook | ws_events | messages |
| stream_trades | ws_events | messages |
| ws_events | update_mark_metrics | mark updates |
| ws_events | update_orderbook_snapshot | orderbook updates |
| ws_events | update_trade_aggregator | trade updates |
| update_mark_metrics | runtime_mark_windows | mark windows |
| update_mark_metrics | risk_last_mark_update | mark timestamp |
| update_mark_metrics | emit_metric | ws_mark_update |
| update_orderbook_snapshot | runtime_orderbook_snapshots | depth snapshot |
| update_orderbook_snapshot | emit_orderbook_snapshot | persist snapshot |
| update_trade_aggregator | runtime_trade_aggregators | trade stats |
| update_trade_aggregator | emit_trade_flow_summary | persist trade flow |
| run_stream_loop | emit_metric | stream health |
| emit_metric | event_store_append_metric | append_metric |
| emit_orderbook_snapshot | event_store_append | append orderbook |
| emit_trade_flow_summary | event_store_append | append trade flow |
| risk_last_mark_update | risk_check_mark_staleness | staleness input |

## Notes
- TradingEngine uses REST tickers/candles and records marks via PriceTickStore.
- WebSocket streaming updates runtime_state for orderbook + trade flow context.
- Risk mark-staleness checks read LiveRiskManager.last_mark_update timestamps.
- EventStore persistence captures mark updates and orderbook/trade summaries.
- TradingEngine._process_symbol calls Strategy.decide with recent marks and candle context.
