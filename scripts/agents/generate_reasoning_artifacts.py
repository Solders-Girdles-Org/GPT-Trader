#!/usr/bin/env python3
"""Generate reasoning artifacts for CLI flow and config linkage.

Outputs JSON, Markdown, and DOT artifacts under var/agents/reasoning/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, TextIO

PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
OUTPUT_DIR = PROJECT_ROOT / "var" / "agents" / "reasoning"
FLOW_CONFIG_DIR = PROJECT_ROOT / "config" / "agents" / "flows"

try:
    import yaml
except ImportError:  # pragma: no cover - optional for generators
    yaml = None

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_ROOT))


CLI_FLOW_NODES = [
    {
        "id": "cli_entrypoint",
        "label": "CLI entrypoint (gpt_trader.cli:main)",
        "type": "entrypoint",
        "path": "src/gpt_trader/cli/__init__.py",
    },
    {
        "id": "cli_run_command",
        "label": "CLI run command",
        "type": "command",
        "path": "src/gpt_trader/cli/commands/run.py",
    },
    {
        "id": "cli_services",
        "label": "CLI config/services",
        "type": "services",
        "path": "src/gpt_trader/cli/services.py",
    },
    {
        "id": "profile_loader",
        "label": "ProfileLoader",
        "type": "config",
        "path": "src/gpt_trader/app/config/profile_loader.py",
    },
    {
        "id": "bot_config",
        "label": "BotConfig",
        "type": "config",
        "path": "src/gpt_trader/app/config/bot_config.py",
    },
    {
        "id": "bootstrap",
        "label": "build_bot / bot_from_profile",
        "type": "bootstrap",
        "path": "src/gpt_trader/app/bootstrap.py",
    },
    {
        "id": "container",
        "label": "ApplicationContainer",
        "type": "container",
        "path": "src/gpt_trader/app/container.py",
    },
    {
        "id": "trading_bot",
        "label": "TradingBot",
        "type": "runtime",
        "path": "src/gpt_trader/features/live_trade/bot.py",
    },
    {
        "id": "trading_engine",
        "label": "TradingEngine",
        "type": "runtime",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
    },
    {
        "id": "strategy_factory",
        "label": "create_strategy",
        "type": "strategy",
        "path": "src/gpt_trader/features/live_trade/factory.py",
    },
]

CLI_FLOW_EDGES = [
    {
        "from": "cli_entrypoint",
        "to": "cli_run_command",
        "label": "dispatch to run command",
    },
    {
        "from": "cli_run_command",
        "to": "cli_services",
        "label": "build config + instantiate bot",
    },
    {
        "from": "cli_services",
        "to": "profile_loader",
        "label": "load profile schema",
    },
    {
        "from": "profile_loader",
        "to": "bot_config",
        "label": "construct BotConfig",
    },
    {
        "from": "cli_services",
        "to": "container",
        "label": "create ApplicationContainer",
    },
    {
        "from": "bootstrap",
        "to": "container",
        "label": "optional bootstrap path",
    },
    {
        "from": "container",
        "to": "trading_bot",
        "label": "create bot",
    },
    {
        "from": "trading_bot",
        "to": "trading_engine",
        "label": "instantiate engine",
    },
    {
        "from": "trading_engine",
        "to": "strategy_factory",
        "label": "select strategy",
    },
]

GUARD_STACK_CLUSTERS = [
    {"id": "preflight", "label": "Preflight"},
    {"id": "runtime", "label": "Runtime Guards + Monitoring"},
]

GUARD_STACK_NODES = [
    {
        "id": "preflight_entry",
        "label": "Preflight entrypoint",
        "type": "entrypoint",
        "path": "scripts/production_preflight.py",
        "cluster": "preflight",
    },
    {
        "id": "preflight_cli",
        "label": "Preflight CLI",
        "type": "cli",
        "path": "src/gpt_trader/preflight/cli.py",
        "cluster": "preflight",
    },
    {
        "id": "preflight_core",
        "label": "PreflightCheck",
        "type": "core",
        "path": "src/gpt_trader/preflight/core.py",
        "cluster": "preflight",
    },
    {
        "id": "preflight_checks",
        "label": "Preflight checks",
        "type": "checks",
        "path": "src/gpt_trader/preflight/checks/",
        "cluster": "preflight",
    },
    {
        "id": "preflight_report",
        "label": "Preflight report",
        "type": "report",
        "path": "src/gpt_trader/preflight/report.py",
        "cluster": "preflight",
    },
    {
        "id": "trading_engine",
        "label": "TradingEngine",
        "type": "runtime",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "runtime",
    },
    {
        "id": "execution_guard_manager",
        "label": "GuardManager (execution)",
        "type": "runtime_guard",
        "path": "src/gpt_trader/features/live_trade/execution/guard_manager.py",
        "cluster": "runtime",
    },
    {
        "id": "execution_guards",
        "label": "Execution guards",
        "type": "guards",
        "path": "src/gpt_trader/features/live_trade/execution/guards/",
        "cluster": "runtime",
    },
    {
        "id": "health_signals",
        "label": "Health signals",
        "type": "monitoring",
        "path": "src/gpt_trader/monitoring/health_signals.py",
        "cluster": "runtime",
    },
    {
        "id": "health_checks",
        "label": "Health checks",
        "type": "monitoring",
        "path": "src/gpt_trader/monitoring/health_checks.py",
        "cluster": "runtime",
    },
    {
        "id": "status_reporter",
        "label": "Status reporter",
        "type": "monitoring",
        "path": "src/gpt_trader/monitoring/status_reporter.py",
        "cluster": "runtime",
    },
]

GUARD_STACK_EDGES = [
    {
        "from": "preflight_entry",
        "to": "preflight_cli",
        "label": "delegate CLI",
    },
    {
        "from": "preflight_cli",
        "to": "preflight_core",
        "label": "create PreflightCheck",
    },
    {
        "from": "preflight_core",
        "to": "preflight_checks",
        "label": "run checks",
    },
    {
        "from": "preflight_core",
        "to": "preflight_report",
        "label": "generate report",
    },
    {
        "from": "trading_engine",
        "to": "execution_guard_manager",
        "label": "runtime guard sweep",
    },
    {
        "from": "execution_guard_manager",
        "to": "execution_guards",
        "label": "execute runtime guards",
    },
    {
        "from": "trading_engine",
        "to": "execution_guards",
        "label": "pre-trade guard stack",
    },
    {
        "from": "health_signals",
        "to": "health_checks",
        "label": "evaluate thresholds",
    },
    {
        "from": "health_checks",
        "to": "status_reporter",
        "label": "report status",
    },
]

EXECUTION_FLOW_CLUSTERS = [
    {"id": "entry", "label": "Entry + Decision"},
    {"id": "guards", "label": "Guard + Validation"},
    {"id": "submission", "label": "Submission + Telemetry"},
    {"id": "event_store", "label": "Event Store"},
    {"id": "outcomes", "label": "Outcomes"},
]

EXECUTION_FLOW_NODES = [
    {
        "id": "trading_engine_cycle",
        "label": "TradingEngine._cycle",
        "type": "entrypoint",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "entry",
    },
    {
        "id": "strategy_decide",
        "label": "Strategy.decide",
        "type": "decision",
        "path": "src/gpt_trader/features/live_trade/strategies/base.py",
        "cluster": "entry",
    },
    {
        "id": "engine_submit",
        "label": "TradingEngine.submit_order",
        "type": "entrypoint",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "entry",
    },
    {
        "id": "engine_validate",
        "label": "TradingEngine._validate_and_place_order",
        "type": "guard_stack",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "guards",
    },
    {
        "id": "degradation_gate",
        "label": "Degradation gate",
        "type": "guard",
        "path": "src/gpt_trader/features/live_trade/degradation.py",
        "cluster": "guards",
    },
    {
        "id": "security_validator",
        "label": "Security validator",
        "type": "guard",
        "path": "src/gpt_trader/security/security_validator.py",
        "cluster": "guards",
    },
    {
        "id": "risk_manager",
        "label": "LiveRiskManager.pre_trade_validate",
        "type": "guard",
        "path": "src/gpt_trader/features/live_trade/risk/manager/__init__.py",
        "cluster": "guards",
    },
    {
        "id": "order_validator",
        "label": "OrderValidator (exchange/slippage/preview)",
        "type": "guard",
        "path": "src/gpt_trader/features/live_trade/execution/validation.py",
        "cluster": "guards",
    },
    {
        "id": "engine_mark_staleness",
        "label": "TradingEngine._check_mark_staleness",
        "type": "guard",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "guards",
    },
    {
        "id": "risk_check_mark_staleness",
        "label": "LiveRiskManager.check_mark_staleness",
        "type": "guard",
        "path": "src/gpt_trader/features/live_trade/risk/manager/__init__.py",
        "cluster": "guards",
    },
    {
        "id": "order_submitter",
        "label": "OrderSubmitter.submit_order",
        "type": "submission",
        "path": "src/gpt_trader/features/live_trade/execution/order_submission.py",
        "cluster": "submission",
    },
    {
        "id": "order_rejection",
        "label": "OrderSubmitter.record_rejection",
        "type": "telemetry",
        "path": "src/gpt_trader/features/live_trade/execution/order_submission.py",
        "cluster": "submission",
    },
    {
        "id": "broker_executor",
        "label": "BrokerExecutor.execute_order",
        "type": "submission",
        "path": "src/gpt_trader/features/live_trade/execution/broker_executor.py",
        "cluster": "submission",
    },
    {
        "id": "broker_adapter",
        "label": "BrokerProtocol.place_order",
        "type": "submission",
        "path": "src/gpt_trader/core/protocols.py",
        "cluster": "submission",
    },
    {
        "id": "order_event_recorder",
        "label": "OrderEventRecorder",
        "type": "telemetry",
        "path": "src/gpt_trader/features/live_trade/execution/order_event_recorder.py",
        "cluster": "submission",
    },
    {
        "id": "orders_store",
        "label": "OrdersStore",
        "type": "telemetry",
        "path": "src/gpt_trader/persistence/orders_store.py",
        "cluster": "submission",
    },
    {
        "id": "engine_append_event",
        "label": "TradingEngine._append_event",
        "type": "telemetry",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "event_store",
    },
    {
        "id": "emit_metric",
        "label": "emit_metric",
        "type": "telemetry",
        "path": "src/gpt_trader/utilities/telemetry.py",
        "cluster": "event_store",
    },
    {
        "id": "event_store_append_metric",
        "label": "EventStoreProtocol.append_metric",
        "type": "event_store",
        "path": "src/gpt_trader/app/protocols.py",
        "cluster": "event_store",
    },
    {
        "id": "event_store_append",
        "label": "EventStoreProtocol.append",
        "type": "event_store",
        "path": "src/gpt_trader/app/protocols.py",
        "cluster": "event_store",
    },
    {
        "id": "decision_trace",
        "label": "OrderDecisionTrace",
        "type": "outcome",
        "path": "src/gpt_trader/features/live_trade/execution/decision_trace.py",
        "cluster": "outcomes",
    },
    {
        "id": "submission_result",
        "label": "OrderSubmissionResult",
        "type": "outcome",
        "path": "src/gpt_trader/features/live_trade/execution/submission_result.py",
        "cluster": "outcomes",
    },
]

EXECUTION_FLOW_EDGES = [
    {
        "from": "trading_engine_cycle",
        "to": "strategy_decide",
        "label": "produce decision",
    },
    {
        "from": "strategy_decide",
        "to": "engine_validate",
        "label": "submit decision",
    },
    {
        "from": "engine_submit",
        "to": "engine_validate",
        "label": "delegate to guard stack",
    },
    {
        "from": "engine_validate",
        "to": "decision_trace",
        "label": "record outcomes",
    },
    {
        "from": "engine_validate",
        "to": "degradation_gate",
        "label": "pause/allow",
    },
    {
        "from": "degradation_gate",
        "to": "security_validator",
        "label": "validate request",
    },
    {
        "from": "security_validator",
        "to": "engine_mark_staleness",
        "label": "staleness gate",
    },
    {
        "from": "engine_mark_staleness",
        "to": "risk_check_mark_staleness",
        "label": "risk check",
    },
    {
        "from": "engine_mark_staleness",
        "to": "engine_append_event",
        "label": "stale_mark_detected",
    },
    {
        "from": "engine_append_event",
        "to": "event_store_append",
        "label": "append event",
    },
    {
        "from": "engine_mark_staleness",
        "to": "order_validator",
        "label": "continue guards",
    },
    {
        "from": "order_validator",
        "to": "risk_manager",
        "label": "pre-trade validate",
    },
    {
        "from": "engine_validate",
        "to": "order_rejection",
        "label": "guard rejection",
    },
    {
        "from": "order_rejection",
        "to": "order_event_recorder",
        "label": "record rejection",
    },
    {
        "from": "order_validator",
        "to": "order_submitter",
        "label": "submit order",
    },
    {
        "from": "order_submitter",
        "to": "broker_executor",
        "label": "execute broker call",
    },
    {
        "from": "broker_executor",
        "to": "broker_adapter",
        "label": "place order",
    },
    {
        "from": "order_submitter",
        "to": "order_event_recorder",
        "label": "record events",
    },
    {
        "from": "order_event_recorder",
        "to": "emit_metric",
        "label": "emit metrics",
    },
    {
        "from": "emit_metric",
        "to": "event_store_append_metric",
        "label": "append_metric",
    },
    {
        "from": "order_event_recorder",
        "to": "event_store_append",
        "label": "decision trace",
    },
    {
        "from": "order_submitter",
        "to": "orders_store",
        "label": "persist order",
    },
    {
        "from": "engine_validate",
        "to": "submission_result",
        "label": "return status",
    },
]

MARKET_DATA_FLOW_CLUSTERS = [
    {"id": "polling", "label": "REST Polling"},
    {"id": "streaming", "label": "WebSocket Streaming"},
    {"id": "state", "label": "Runtime State"},
    {"id": "risk", "label": "Risk + Staleness"},
    {"id": "event_store", "label": "Event Store"},
    {"id": "strategy", "label": "Strategy Inputs"},
]

MARKET_DATA_FLOW_NODES = [
    {
        "id": "engine_cycle",
        "label": "TradingEngine._cycle",
        "type": "entrypoint",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "polling",
    },
    {
        "id": "fetch_batch_tickers",
        "label": "TradingEngine._fetch_batch_tickers",
        "type": "fetch",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "polling",
    },
    {
        "id": "broker_get_tickers",
        "label": "Broker.get_tickers (optional)",
        "type": "broker",
        "path": "src/gpt_trader/features/brokerages/coinbase/rest/product_service.py",
        "cluster": "polling",
    },
    {
        "id": "broker_get_ticker",
        "label": "BrokerProtocol.get_ticker",
        "type": "broker",
        "path": "src/gpt_trader/core/protocols.py",
        "cluster": "polling",
    },
    {
        "id": "broker_get_candles",
        "label": "BrokerProtocol.get_candles",
        "type": "broker",
        "path": "src/gpt_trader/core/protocols.py",
        "cluster": "polling",
    },
    {
        "id": "process_symbol",
        "label": "TradingEngine._process_symbol",
        "type": "processing",
        "path": "src/gpt_trader/features/live_trade/engines/strategy.py",
        "cluster": "polling",
    },
    {
        "id": "start_streaming",
        "label": "start_streaming_background",
        "type": "stream",
        "path": "src/gpt_trader/features/live_trade/engines/telemetry_streaming.py",
        "cluster": "streaming",
    },
    {
        "id": "run_stream_loop",
        "label": "telemetry_streaming._run_stream_loop",
        "type": "stream",
        "path": "src/gpt_trader/features/live_trade/engines/telemetry_streaming.py",
        "cluster": "streaming",
    },
    {
        "id": "stream_orderbook",
        "label": "WebSocketClientMixin.stream_orderbook",
        "type": "stream",
        "path": "src/gpt_trader/features/brokerages/coinbase/client/websocket_mixin.py",
        "cluster": "streaming",
    },
    {
        "id": "stream_trades",
        "label": "WebSocketClientMixin.stream_trades",
        "type": "stream",
        "path": "src/gpt_trader/features/brokerages/coinbase/client/websocket_mixin.py",
        "cluster": "streaming",
    },
    {
        "id": "ws_events",
        "label": "ws_events (OrderbookUpdate/TradeEvent)",
        "type": "parser",
        "path": "src/gpt_trader/features/brokerages/coinbase/ws_events.py",
        "cluster": "streaming",
    },
    {
        "id": "update_mark_metrics",
        "label": "update_mark_and_metrics",
        "type": "stream_update",
        "path": "src/gpt_trader/features/live_trade/engines/telemetry_health.py",
        "cluster": "streaming",
    },
    {
        "id": "update_orderbook_snapshot",
        "label": "update_orderbook_snapshot",
        "type": "stream_update",
        "path": "src/gpt_trader/features/live_trade/engines/telemetry_health.py",
        "cluster": "streaming",
    },
    {
        "id": "update_trade_aggregator",
        "label": "update_trade_aggregator",
        "type": "stream_update",
        "path": "src/gpt_trader/features/live_trade/engines/telemetry_health.py",
        "cluster": "streaming",
    },
    {
        "id": "price_tick_store",
        "label": "PriceTickStore",
        "type": "state",
        "path": "src/gpt_trader/features/recorder/price_tick_store.py",
        "cluster": "state",
    },
    {
        "id": "runtime_mark_windows",
        "label": "RuntimeStateProtocol.mark_windows",
        "type": "state",
        "path": "src/gpt_trader/app/protocols.py",
        "cluster": "state",
    },
    {
        "id": "runtime_orderbook_snapshots",
        "label": "RuntimeStateProtocol.orderbook_snapshots",
        "type": "state",
        "path": "src/gpt_trader/app/protocols.py",
        "cluster": "state",
    },
    {
        "id": "runtime_trade_aggregators",
        "label": "RuntimeStateProtocol.trade_aggregators",
        "type": "state",
        "path": "src/gpt_trader/app/protocols.py",
        "cluster": "state",
    },
    {
        "id": "risk_last_mark_update",
        "label": "LiveRiskManager.last_mark_update",
        "type": "risk_state",
        "path": "src/gpt_trader/features/live_trade/risk/manager/__init__.py",
        "cluster": "risk",
    },
    {
        "id": "risk_check_mark_staleness",
        "label": "LiveRiskManager.check_mark_staleness",
        "type": "risk_check",
        "path": "src/gpt_trader/features/live_trade/risk/manager/__init__.py",
        "cluster": "risk",
    },
    {
        "id": "emit_metric",
        "label": "emit_metric",
        "type": "telemetry",
        "path": "src/gpt_trader/utilities/telemetry.py",
        "cluster": "event_store",
    },
    {
        "id": "emit_orderbook_snapshot",
        "label": "emit_orderbook_snapshot",
        "type": "telemetry",
        "path": "src/gpt_trader/features/live_trade/engines/telemetry_health.py",
        "cluster": "event_store",
    },
    {
        "id": "emit_trade_flow_summary",
        "label": "emit_trade_flow_summary",
        "type": "telemetry",
        "path": "src/gpt_trader/features/live_trade/engines/telemetry_health.py",
        "cluster": "event_store",
    },
    {
        "id": "event_store_append_metric",
        "label": "EventStoreProtocol.append_metric",
        "type": "event_store",
        "path": "src/gpt_trader/app/protocols.py",
        "cluster": "event_store",
    },
    {
        "id": "event_store_append",
        "label": "EventStoreProtocol.append",
        "type": "event_store",
        "path": "src/gpt_trader/app/protocols.py",
        "cluster": "event_store",
    },
    {
        "id": "strategy_decide",
        "label": "Strategy.decide",
        "type": "decision",
        "path": "src/gpt_trader/features/live_trade/strategies/base.py",
        "cluster": "strategy",
    },
]

MARKET_DATA_FLOW_EDGES = [
    {
        "from": "engine_cycle",
        "to": "fetch_batch_tickers",
        "label": "poll tickers",
    },
    {
        "from": "fetch_batch_tickers",
        "to": "broker_get_tickers",
        "label": "batch request",
    },
    {
        "from": "fetch_batch_tickers",
        "to": "broker_get_ticker",
        "label": "fallback per symbol",
    },
    {
        "from": "broker_get_tickers",
        "to": "process_symbol",
        "label": "ticker map",
    },
    {
        "from": "broker_get_ticker",
        "to": "process_symbol",
        "label": "single ticker",
    },
    {
        "from": "process_symbol",
        "to": "broker_get_candles",
        "label": "candles request",
    },
    {
        "from": "broker_get_candles",
        "to": "process_symbol",
        "label": "candles data",
    },
    {
        "from": "process_symbol",
        "to": "price_tick_store",
        "label": "record mark",
    },
    {
        "from": "process_symbol",
        "to": "risk_last_mark_update",
        "label": "mark timestamp",
    },
    {
        "from": "price_tick_store",
        "to": "strategy_decide",
        "label": "recent_marks",
    },
    {
        "from": "process_symbol",
        "to": "strategy_decide",
        "label": "current_mark + candles",
    },
    {
        "from": "start_streaming",
        "to": "run_stream_loop",
        "label": "start WS loop",
    },
    {
        "from": "run_stream_loop",
        "to": "stream_orderbook",
        "label": "primary stream",
    },
    {
        "from": "run_stream_loop",
        "to": "stream_trades",
        "label": "fallback stream",
    },
    {
        "from": "stream_orderbook",
        "to": "ws_events",
        "label": "messages",
    },
    {
        "from": "stream_trades",
        "to": "ws_events",
        "label": "messages",
    },
    {
        "from": "ws_events",
        "to": "update_mark_metrics",
        "label": "mark updates",
    },
    {
        "from": "ws_events",
        "to": "update_orderbook_snapshot",
        "label": "orderbook updates",
    },
    {
        "from": "ws_events",
        "to": "update_trade_aggregator",
        "label": "trade updates",
    },
    {
        "from": "update_mark_metrics",
        "to": "runtime_mark_windows",
        "label": "mark windows",
    },
    {
        "from": "update_mark_metrics",
        "to": "risk_last_mark_update",
        "label": "mark timestamp",
    },
    {
        "from": "update_mark_metrics",
        "to": "emit_metric",
        "label": "ws_mark_update",
    },
    {
        "from": "update_orderbook_snapshot",
        "to": "runtime_orderbook_snapshots",
        "label": "depth snapshot",
    },
    {
        "from": "update_orderbook_snapshot",
        "to": "emit_orderbook_snapshot",
        "label": "persist snapshot",
    },
    {
        "from": "update_trade_aggregator",
        "to": "runtime_trade_aggregators",
        "label": "trade stats",
    },
    {
        "from": "update_trade_aggregator",
        "to": "emit_trade_flow_summary",
        "label": "persist trade flow",
    },
    {
        "from": "run_stream_loop",
        "to": "emit_metric",
        "label": "stream health",
    },
    {
        "from": "emit_metric",
        "to": "event_store_append_metric",
        "label": "append_metric",
    },
    {
        "from": "emit_orderbook_snapshot",
        "to": "event_store_append",
        "label": "append orderbook",
    },
    {
        "from": "emit_trade_flow_summary",
        "to": "event_store_append",
        "label": "append trade flow",
    },
    {
        "from": "risk_last_mark_update",
        "to": "risk_check_mark_staleness",
        "label": "staleness input",
    },
]

BACKTEST_REPORTING_FLOW_CLUSTERS = [
    {"id": "broker", "label": "Simulation Broker"},
    {"id": "metrics", "label": "Metrics"},
    {"id": "report", "label": "Reporter"},
    {"id": "output", "label": "Outputs"},
]

BACKTEST_REPORTING_FLOW_NODES = [
    {
        "id": "simulated_broker",
        "label": "SimulatedBroker",
        "type": "source",
        "path": "src/gpt_trader/backtesting/simulation/broker.py",
        "cluster": "broker",
    },
    {
        "id": "broker_stats",
        "label": "SimulatedBroker.get_statistics",
        "type": "source",
        "path": "src/gpt_trader/backtesting/simulation/broker.py",
        "cluster": "broker",
    },
    {
        "id": "trade_stats_compute",
        "label": "calculate_trade_statistics",
        "type": "metrics",
        "path": "src/gpt_trader/backtesting/metrics/statistics.py",
        "cluster": "metrics",
    },
    {
        "id": "trade_stats",
        "label": "TradeStatistics",
        "type": "data",
        "path": "src/gpt_trader/backtesting/metrics/statistics.py",
        "cluster": "metrics",
    },
    {
        "id": "risk_metrics_compute",
        "label": "calculate_risk_metrics",
        "type": "metrics",
        "path": "src/gpt_trader/backtesting/metrics/risk.py",
        "cluster": "metrics",
    },
    {
        "id": "risk_metrics",
        "label": "RiskMetrics",
        "type": "data",
        "path": "src/gpt_trader/backtesting/metrics/risk.py",
        "cluster": "metrics",
    },
    {
        "id": "backtest_reporter",
        "label": "BacktestReporter.generate_result",
        "type": "reporter",
        "path": "src/gpt_trader/backtesting/metrics/report.py",
        "cluster": "report",
    },
    {
        "id": "generate_backtest_report",
        "label": "generate_backtest_report",
        "type": "reporter",
        "path": "src/gpt_trader/backtesting/metrics/report.py",
        "cluster": "report",
    },
    {
        "id": "backtest_result_report",
        "label": "BacktestResult",
        "type": "output",
        "path": "src/gpt_trader/backtesting/types.py",
        "cluster": "output",
    },
    {
        "id": "report_summary",
        "label": "BacktestReporter.generate_summary",
        "type": "output",
        "path": "src/gpt_trader/backtesting/metrics/report.py",
        "cluster": "output",
    },
    {
        "id": "report_csv",
        "label": "BacktestReporter.generate_csv_row",
        "type": "output",
        "path": "src/gpt_trader/backtesting/metrics/report.py",
        "cluster": "output",
    },
]

BACKTEST_REPORTING_FLOW_EDGES = [
    {
        "from": "simulated_broker",
        "to": "trade_stats_compute",
        "label": "trade history",
    },
    {
        "from": "trade_stats_compute",
        "to": "trade_stats",
        "label": "build stats",
    },
    {
        "from": "simulated_broker",
        "to": "risk_metrics_compute",
        "label": "equity curve",
    },
    {
        "from": "risk_metrics_compute",
        "to": "risk_metrics",
        "label": "build metrics",
    },
    {
        "from": "simulated_broker",
        "to": "broker_stats",
        "label": "broker stats",
    },
    {
        "from": "trade_stats",
        "to": "backtest_reporter",
        "label": "trade statistics",
    },
    {
        "from": "risk_metrics",
        "to": "backtest_reporter",
        "label": "risk metrics",
    },
    {
        "from": "broker_stats",
        "to": "backtest_reporter",
        "label": "summary stats",
    },
    {
        "from": "backtest_reporter",
        "to": "backtest_result_report",
        "label": "BacktestResult",
    },
    {
        "from": "backtest_reporter",
        "to": "report_summary",
        "label": "summary text",
    },
    {
        "from": "backtest_reporter",
        "to": "report_csv",
        "label": "csv row",
    },
    {
        "from": "simulated_broker",
        "to": "generate_backtest_report",
        "label": "input broker",
    },
    {
        "from": "generate_backtest_report",
        "to": "backtest_reporter",
        "label": "construct reporter",
    },
    {
        "from": "generate_backtest_report",
        "to": "backtest_result_report",
        "label": "return result",
    },
]

BACKTEST_ENTRYPOINTS_CLUSTERS = [
    {"id": "cli", "label": "CLI Entrypoints"},
    {"id": "scripts", "label": "Script Entrypoints"},
    {"id": "library", "label": "Library Entrypoints"},
    {"id": "engine", "label": "Backtesting Engine"},
    {"id": "validation", "label": "Validation + Chaos"},
    {"id": "output", "label": "Outputs"},
]

BACKTEST_ENTRYPOINTS_NODES = [
    {
        "id": "cli_optimize_run",
        "label": "optimize run",
        "type": "entrypoint",
        "path": "src/gpt_trader/cli/commands/optimize/run.py",
        "cluster": "cli",
    },
    {
        "id": "paper_trade_stress_test",
        "label": "run_stress_test",
        "type": "entrypoint",
        "path": "scripts/analysis/paper_trade_stress_test.py",
        "cluster": "scripts",
    },
    {
        "id": "golden_path_demo",
        "label": "golden_path_validation_demo",
        "type": "entrypoint",
        "path": "scripts/analysis/golden_path_validation_demo.py",
        "cluster": "scripts",
    },
    {
        "id": "walk_forward_run",
        "label": "WalkForwardOptimizer.run",
        "type": "entrypoint",
        "path": "src/gpt_trader/features/optimize/walk_forward.py",
        "cluster": "library",
    },
    {
        "id": "data_provider_factory",
        "label": "create_coinbase_data_provider",
        "type": "data",
        "path": "src/gpt_trader/backtesting/data/manager.py",
        "cluster": "engine",
    },
    {
        "id": "batch_runner",
        "label": "BatchBacktestRunner.run_trial",
        "type": "engine",
        "path": "src/gpt_trader/features/optimize/runner/batch_runner.py",
        "cluster": "engine",
    },
    {
        "id": "clocked_bar_runner",
        "label": "ClockedBarRunner.run",
        "type": "engine",
        "path": "src/gpt_trader/backtesting/engine/bar_runner.py",
        "cluster": "engine",
    },
    {
        "id": "simulated_broker_engine",
        "label": "SimulatedBroker",
        "type": "engine",
        "path": "src/gpt_trader/backtesting/simulation/broker.py",
        "cluster": "engine",
    },
    {
        "id": "backtest_guarded_executor",
        "label": "BacktestGuardedExecutor.submit_order",
        "type": "engine",
        "path": "src/gpt_trader/backtesting/engine/guarded_execution.py",
        "cluster": "engine",
    },
    {
        "id": "engine_backtest_result",
        "label": "BacktestResult",
        "type": "output",
        "path": "src/gpt_trader/backtesting/types.py",
        "cluster": "output",
    },
    {
        "id": "decision_logger_log",
        "label": "DecisionLogger.log_decision",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/validation/decision_logger.py",
        "cluster": "validation",
    },
    {
        "id": "decision_logger_export",
        "label": "DecisionLogger.export_to_json",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/validation/decision_logger.py",
        "cluster": "validation",
    },
    {
        "id": "golden_path_validate",
        "label": "GoldenPathValidator.validate_decision",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/validation/validator.py",
        "cluster": "validation",
    },
    {
        "id": "golden_path_report",
        "label": "GoldenPathValidator.generate_report",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/validation/validator.py",
        "cluster": "validation",
    },
    {
        "id": "replay_decisions",
        "label": "replay_decisions_through_simulator",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/validation/validator.py",
        "cluster": "validation",
    },
    {
        "id": "chaos_engine_add",
        "label": "ChaosEngine.add_scenario",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "validation",
    },
    {
        "id": "chaos_engine_process_candle",
        "label": "ChaosEngine.process_candle",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "validation",
    },
    {
        "id": "chaos_engine_process_order",
        "label": "ChaosEngine.process_order",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "validation",
    },
    {
        "id": "chaos_engine_apply_latency",
        "label": "ChaosEngine.apply_latency",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "validation",
    },
    {
        "id": "chaos_scenario_factories",
        "label": "create_*_scenario",
        "type": "entrypoint",
        "path": "src/gpt_trader/backtesting/chaos/scenarios.py",
        "cluster": "validation",
    },
    {
        "id": "validation_report",
        "label": "ValidationReport",
        "type": "output",
        "path": "src/gpt_trader/backtesting/types.py",
        "cluster": "output",
    },
    {
        "id": "decision_log_json",
        "label": "Decision log JSON",
        "type": "output",
        "path": "src/gpt_trader/backtesting/validation/decision_logger.py",
        "cluster": "output",
    },
    {
        "id": "chaos_event",
        "label": "ChaosEvent",
        "type": "output",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "output",
    },
]

BACKTEST_ENTRYPOINTS_EDGES = [
    {
        "from": "cli_optimize_run",
        "to": "data_provider_factory",
        "label": "build data provider",
    },
    {
        "from": "data_provider_factory",
        "to": "batch_runner",
        "label": "IHistoricalDataProvider",
    },
    {
        "from": "cli_optimize_run",
        "to": "batch_runner",
        "label": "start trials",
    },
    {
        "from": "batch_runner",
        "to": "clocked_bar_runner",
        "label": "run loop",
    },
    {
        "from": "clocked_bar_runner",
        "to": "simulated_broker_engine",
        "label": "bars/quotes",
    },
    {
        "from": "simulated_broker_engine",
        "to": "backtest_guarded_executor",
        "label": "broker context",
    },
    {
        "from": "backtest_guarded_executor",
        "to": "decision_logger_log",
        "label": "log decisions",
    },
    {
        "from": "clocked_bar_runner",
        "to": "chaos_engine_process_candle",
        "label": "optional hook",
    },
    {
        "from": "simulated_broker_engine",
        "to": "chaos_engine_process_order",
        "label": "optional hook",
    },
    {
        "from": "simulated_broker_engine",
        "to": "chaos_engine_apply_latency",
        "label": "optional hook",
    },
    {
        "from": "simulated_broker_engine",
        "to": "engine_backtest_result",
        "label": "summary result",
    },
    {
        "from": "walk_forward_run",
        "to": "clocked_bar_runner",
        "label": "backtest window",
    },
    {
        "from": "walk_forward_run",
        "to": "simulated_broker_engine",
        "label": "simulate trades",
    },
    {
        "from": "walk_forward_run",
        "to": "engine_backtest_result",
        "label": "window result",
    },
    {
        "from": "paper_trade_stress_test",
        "to": "clocked_bar_runner",
        "label": "stress loop",
    },
    {
        "from": "paper_trade_stress_test",
        "to": "simulated_broker_engine",
        "label": "simulate broker",
    },
    {
        "from": "golden_path_demo",
        "to": "decision_logger_log",
        "label": "log decisions",
    },
    {
        "from": "golden_path_demo",
        "to": "golden_path_validate",
        "label": "validate decisions",
    },
    {
        "from": "golden_path_demo",
        "to": "golden_path_report",
        "label": "generate report",
    },
    {
        "from": "decision_logger_log",
        "to": "decision_logger_export",
        "label": "export decisions",
    },
    {
        "from": "decision_logger_export",
        "to": "decision_log_json",
        "label": "write JSON",
    },
    {
        "from": "decision_logger_log",
        "to": "replay_decisions",
        "label": "recorded decisions",
    },
    {
        "from": "replay_decisions",
        "to": "golden_path_validate",
        "label": "validate decisions",
    },
    {
        "from": "golden_path_validate",
        "to": "golden_path_report",
        "label": "collect divergences",
    },
    {
        "from": "golden_path_report",
        "to": "validation_report",
        "label": "report",
    },
    {
        "from": "chaos_scenario_factories",
        "to": "chaos_engine_add",
        "label": "scenario config",
    },
    {
        "from": "chaos_engine_add",
        "to": "chaos_engine_process_candle",
        "label": "inject candles",
    },
    {
        "from": "chaos_engine_add",
        "to": "chaos_engine_process_order",
        "label": "inject orders",
    },
    {
        "from": "chaos_engine_add",
        "to": "chaos_engine_apply_latency",
        "label": "inject latency",
    },
    {
        "from": "chaos_engine_process_candle",
        "to": "chaos_event",
        "label": "record events",
    },
    {
        "from": "chaos_engine_process_order",
        "to": "chaos_event",
        "label": "record events",
    },
]

BACKTEST_VALIDATION_CHAOS_CLUSTERS = [
    {"id": "simulation", "label": "Simulation Loop"},
    {"id": "guarded", "label": "Guarded Execution"},
    {"id": "logging", "label": "Decision Logging"},
    {"id": "validation", "label": "Golden-Path Validation"},
    {"id": "chaos", "label": "Chaos Injection"},
    {"id": "output", "label": "Outputs"},
]

BACKTEST_VALIDATION_CHAOS_NODES = [
    {
        "id": "clocked_bar_runner",
        "label": "ClockedBarRunner.run",
        "type": "simulation",
        "path": "src/gpt_trader/backtesting/engine/bar_runner.py",
        "cluster": "simulation",
    },
    {
        "id": "simulated_broker_place_order",
        "label": "SimulatedBroker.place_order",
        "type": "simulation",
        "path": "src/gpt_trader/backtesting/simulation/broker.py",
        "cluster": "simulation",
    },
    {
        "id": "backtest_execution_context",
        "label": "BacktestExecutionContext",
        "type": "guarded",
        "path": "src/gpt_trader/backtesting/engine/guarded_execution.py",
        "cluster": "guarded",
    },
    {
        "id": "backtest_decision_context",
        "label": "BacktestDecisionContext",
        "type": "guarded",
        "path": "src/gpt_trader/backtesting/engine/guarded_execution.py",
        "cluster": "guarded",
    },
    {
        "id": "backtest_guarded_submit",
        "label": "BacktestGuardedExecutor.submit_order",
        "type": "guarded",
        "path": "src/gpt_trader/backtesting/engine/guarded_execution.py",
        "cluster": "guarded",
    },
    {
        "id": "state_collector",
        "label": "StateCollector.collect_account_state",
        "type": "guarded",
        "path": "src/gpt_trader/features/live_trade/execution/state_collection.py",
        "cluster": "guarded",
    },
    {
        "id": "order_validator",
        "label": "OrderValidator.run_pre_trade_validation",
        "type": "guarded",
        "path": "src/gpt_trader/features/live_trade/execution/validation.py",
        "cluster": "guarded",
    },
    {
        "id": "order_submitter",
        "label": "OrderSubmitter.submit_order",
        "type": "guarded",
        "path": "src/gpt_trader/features/live_trade/execution/order_submission.py",
        "cluster": "guarded",
    },
    {
        "id": "strategy_decision",
        "label": "StrategyDecision",
        "type": "logging",
        "path": "src/gpt_trader/backtesting/validation/decision_logger.py",
        "cluster": "logging",
    },
    {
        "id": "decision_logger_log",
        "label": "DecisionLogger.log_decision",
        "type": "logging",
        "path": "src/gpt_trader/backtesting/validation/decision_logger.py",
        "cluster": "logging",
    },
    {
        "id": "decision_logger_export",
        "label": "DecisionLogger.export_to_json",
        "type": "logging",
        "path": "src/gpt_trader/backtesting/validation/decision_logger.py",
        "cluster": "logging",
    },
    {
        "id": "replay_decisions",
        "label": "replay_decisions_through_simulator",
        "type": "validation",
        "path": "src/gpt_trader/backtesting/validation/validator.py",
        "cluster": "validation",
    },
    {
        "id": "golden_validate",
        "label": "GoldenPathValidator.validate_decision",
        "type": "validation",
        "path": "src/gpt_trader/backtesting/validation/validator.py",
        "cluster": "validation",
    },
    {
        "id": "golden_report",
        "label": "GoldenPathValidator.generate_report",
        "type": "validation",
        "path": "src/gpt_trader/backtesting/validation/validator.py",
        "cluster": "validation",
    },
    {
        "id": "chaos_scenario",
        "label": "ChaosScenario",
        "type": "chaos",
        "path": "src/gpt_trader/backtesting/types.py",
        "cluster": "chaos",
    },
    {
        "id": "chaos_scenario_factories",
        "label": "create_*_scenario",
        "type": "chaos",
        "path": "src/gpt_trader/backtesting/chaos/scenarios.py",
        "cluster": "chaos",
    },
    {
        "id": "chaos_engine_add",
        "label": "ChaosEngine.add_scenario",
        "type": "chaos",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "chaos",
    },
    {
        "id": "chaos_engine_process_candle",
        "label": "ChaosEngine.process_candle",
        "type": "chaos",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "chaos",
    },
    {
        "id": "chaos_engine_process_order",
        "label": "ChaosEngine.process_order",
        "type": "chaos",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "chaos",
    },
    {
        "id": "chaos_engine_apply_latency",
        "label": "ChaosEngine.apply_latency",
        "type": "chaos",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "chaos",
    },
    {
        "id": "decision_log_json",
        "label": "Decision log JSON",
        "type": "output",
        "path": "src/gpt_trader/backtesting/validation/decision_logger.py",
        "cluster": "output",
    },
    {
        "id": "validation_report",
        "label": "ValidationReport",
        "type": "output",
        "path": "src/gpt_trader/backtesting/types.py",
        "cluster": "output",
    },
    {
        "id": "validation_divergence",
        "label": "ValidationDivergence",
        "type": "output",
        "path": "src/gpt_trader/backtesting/types.py",
        "cluster": "output",
    },
    {
        "id": "chaos_event",
        "label": "ChaosEvent",
        "type": "output",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "output",
    },
    {
        "id": "chaos_stats",
        "label": "ChaosEngine.get_statistics",
        "type": "output",
        "path": "src/gpt_trader/backtesting/chaos/engine.py",
        "cluster": "output",
    },
]

BACKTEST_VALIDATION_CHAOS_EDGES = [
    {
        "from": "backtest_execution_context",
        "to": "backtest_guarded_submit",
        "label": "configure executor",
    },
    {
        "from": "backtest_decision_context",
        "to": "backtest_guarded_submit",
        "label": "decision metadata",
    },
    {
        "from": "backtest_guarded_submit",
        "to": "state_collector",
        "label": "collect state",
    },
    {
        "from": "state_collector",
        "to": "order_validator",
        "label": "equity/positions",
    },
    {
        "from": "order_validator",
        "to": "order_submitter",
        "label": "validated order",
    },
    {
        "from": "order_submitter",
        "to": "simulated_broker_place_order",
        "label": "broker execution",
    },
    {
        "from": "backtest_guarded_submit",
        "to": "strategy_decision",
        "label": "build decision",
    },
    {
        "from": "strategy_decision",
        "to": "decision_logger_log",
        "label": "log decision",
    },
    {
        "from": "decision_logger_log",
        "to": "decision_logger_export",
        "label": "export JSON",
    },
    {
        "from": "decision_logger_export",
        "to": "decision_log_json",
        "label": "write file",
    },
    {
        "from": "decision_logger_log",
        "to": "replay_decisions",
        "label": "recorded decisions",
    },
    {
        "from": "replay_decisions",
        "to": "golden_validate",
        "label": "validate decisions",
    },
    {
        "from": "golden_validate",
        "to": "validation_divergence",
        "label": "divergence",
    },
    {
        "from": "golden_validate",
        "to": "golden_report",
        "label": "collect divergences",
    },
    {
        "from": "golden_report",
        "to": "validation_report",
        "label": "report",
    },
    {
        "from": "chaos_scenario",
        "to": "chaos_engine_add",
        "label": "scenario config",
    },
    {
        "from": "chaos_scenario_factories",
        "to": "chaos_engine_add",
        "label": "factory scenario",
    },
    {
        "from": "chaos_engine_add",
        "to": "chaos_engine_process_candle",
        "label": "inject candles",
    },
    {
        "from": "chaos_engine_add",
        "to": "chaos_engine_process_order",
        "label": "inject orders",
    },
    {
        "from": "chaos_engine_add",
        "to": "chaos_engine_apply_latency",
        "label": "inject latency",
    },
    {
        "from": "clocked_bar_runner",
        "to": "chaos_engine_process_candle",
        "label": "optional hook",
    },
    {
        "from": "simulated_broker_place_order",
        "to": "chaos_engine_process_order",
        "label": "optional hook",
    },
    {
        "from": "clocked_bar_runner",
        "to": "chaos_engine_apply_latency",
        "label": "optional hook",
    },
    {
        "from": "chaos_engine_process_candle",
        "to": "chaos_event",
        "label": "record event",
    },
    {
        "from": "chaos_engine_process_order",
        "to": "chaos_event",
        "label": "record event",
    },
    {
        "from": "chaos_event",
        "to": "chaos_stats",
        "label": "aggregate",
    },
]


def _timestamp() -> str:
    # Must be deterministic so `uv run agent-regenerate --verify` is stable.
    return "1970-01-01T00:00:00+00:00"


def _ensure_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2))


def _write_markdown(path: Path, content: str) -> None:
    path.write_text(content)


def _write_dot(path: Path, lines: Iterable[str]) -> None:
    path.write_text("\n".join(lines))


@dataclass(frozen=True)
class ValidationIssue:
    artifact: str
    node_id: str
    path: str
    suggestion: str | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load flow configs (pip install pyyaml).")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Flow config {path} must be a mapping.")
    return data


def load_flow_config(
    filename: str,
    *,
    artifact: str,
    required_keys: Sequence[str],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    path = FLOW_CONFIG_DIR / filename
    if not path.exists():
        return dict(fallback)

    data = _load_yaml(path)
    missing = [key for key in required_keys if key not in data]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(f"Flow config {path} missing required keys: {missing_list}")
    if data.get("artifact") != artifact:
        raise ValueError(
            f"Flow config {path} has artifact '{data.get('artifact')}', expected '{artifact}'."
        )
    return data


def _build_path_index(roots: Sequence[str]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = defaultdict(list)
    for root in roots:
        base = PROJECT_ROOT / root
        if not base.exists():
            continue
        for candidate in base.rglob("*"):
            if candidate.is_file():
                index[candidate.name].append(candidate.relative_to(PROJECT_ROOT))
    return index


def validate_flow_map(
    flow: dict[str, Any],
    *,
    artifact: str,
    path_index: dict[str, list[Path]] | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in flow.get("nodes", []):
        path_value = node.get("path")
        if not path_value:
            continue
        path = Path(path_value)
        resolved = path if path.is_absolute() else PROJECT_ROOT / path
        if resolved.exists():
            continue
        suggestion = None
        if path_index is not None:
            matches = path_index.get(path.name)
            if matches:
                suggestion = str(matches[0])
        issues.append(
            ValidationIssue(
                artifact=artifact,
                node_id=str(node.get("id", "<unknown>")),
                path=str(path_value),
                suggestion=suggestion,
            )
        )
    return issues


def _report_validation(
    flow_payloads: Sequence[tuple[str, dict[str, Any]]],
    issues: Sequence[ValidationIssue],
    *,
    out: TextIO = sys.stderr,
) -> None:
    issues_by_artifact: dict[str, list[ValidationIssue]] = defaultdict(list)
    for issue in issues:
        issues_by_artifact[issue.artifact].append(issue)

    print("Validating flow maps...", file=out)
    for artifact, flow in flow_payloads:
        artifact_issues = issues_by_artifact.get(artifact, [])
        node_count = len(flow.get("nodes", []))
        if artifact_issues:
            print(
                f"  {artifact}: {node_count} nodes, {len(artifact_issues)} missing paths",
                file=out,
            )
            for issue in artifact_issues:
                suggestion = f" (did you mean {issue.suggestion}?)" if issue.suggestion else ""
                print(f"    - {issue.node_id}: {issue.path}{suggestion}", file=out)
        else:
            print(f"  {artifact}: {node_count} nodes, 0 missing paths OK", file=out)


def build_cli_flow_map() -> dict[str, Any]:
    return {
        "artifact": "cli_flow_map",
        "description": "CLI → config → container → engine flow map.",
        "entrypoints": [
            "uv run gpt-trader run --profile dev --dev-fast",
        ],
        "nodes": CLI_FLOW_NODES,
        "edges": CLI_FLOW_EDGES,
    }


def _build_flow_payload(flow_def: dict[str, Any]) -> dict[str, Any]:
    payload = dict(flow_def)
    payload["generated_at"] = _timestamp()
    return payload


def build_cli_flow_markdown(flow: dict[str, Any]) -> str:
    lines = [
        "# CLI → Config → Container → Engine Flow",
        "",
        f"Generated: {flow['generated_at']}",
        "",
        "## Entrypoints",
        *[f"- `{entry}`" for entry in flow.get("entrypoints", [])],
        "",
        "## Nodes",
        "| ID | Label | Path |",
        "|----|-------|------|",
    ]
    for node in flow["nodes"]:
        lines.append(f"| {node['id']} | {node['label']} | `{node['path']}` |")

    lines.extend(["", "## Edges", "| From | To | Description |", "|------|----|-------------|"])
    for edge in flow["edges"]:
        lines.append(f"| {edge['from']} | {edge['to']} | {edge['label']} |")

    return "\n".join(lines) + "\n"


def build_cli_flow_dot(flow: dict[str, Any]) -> list[str]:
    lines = ["digraph CliFlow {", "  rankdir=LR;"]
    for node in flow["nodes"]:
        label = f"{node['label']}\\n{node['path']}"
        lines.append(f'  "{node["id"]}" [shape=box, label="{label}"]; ')
    for edge in flow["edges"]:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}" [label="{edge["label"]}"];')
    lines.append("}")
    return lines


def build_guard_stack_map() -> dict[str, Any]:
    return {
        "artifact": "guard_stack_map",
        "description": "Guard stack map (preflight checks vs runtime guards + monitoring).",
        "clusters": GUARD_STACK_CLUSTERS,
        "nodes": GUARD_STACK_NODES,
        "edges": GUARD_STACK_EDGES,
        "notes": [
            "Preflight checks run via the preflight CLI and PreflightCheck facade.",
            "Runtime guard sweep is owned by TradingEngine and GuardManager.",
        ],
    }


def build_guard_stack_markdown(guard_map: dict[str, Any]) -> str:
    lines = [
        "# Guard Stack Map",
        "",
        f"Generated: {guard_map['generated_at']}",
        "",
    ]

    cluster_index = {cluster["id"]: cluster for cluster in guard_map["clusters"]}
    for cluster_id in [cluster["id"] for cluster in guard_map["clusters"]]:
        cluster = cluster_index[cluster_id]
        lines.append(f"## {cluster['label']}")
        lines.append("| ID | Label | Path |")
        lines.append("|----|-------|------|")
        for node in guard_map["nodes"]:
            if node["cluster"] == cluster_id:
                lines.append(f"| {node['id']} | {node['label']} | `{node['path']}` |")
        lines.append("")

    lines.extend(["## Edges", "| From | To | Description |", "|------|----|-------------|"])
    for edge in guard_map["edges"]:
        lines.append(f"| {edge['from']} | {edge['to']} | {edge['label']} |")

    lines.append("")
    lines.append("## Notes")
    for note in guard_map.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def build_guard_stack_dot(guard_map: dict[str, Any]) -> list[str]:
    lines = ["digraph GuardStack {", "  rankdir=LR;"]
    for cluster in guard_map["clusters"]:
        lines.append(f"  subgraph cluster_{cluster['id']} {{")
        lines.append(f'    label="{cluster["label"]}";')
        lines.append("    style=rounded;")
        for node in guard_map["nodes"]:
            if node["cluster"] != cluster["id"]:
                continue
            label = f"{node['label']}\\n{node['path']}"
            lines.append(f'    "{node["id"]}" [shape=box, label="{label}"]; ')
        lines.append("  }")

    for edge in guard_map["edges"]:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}" [label="{edge["label"]}"];')
    lines.append("}")
    return lines


def build_execution_flow_map() -> dict[str, Any]:
    return {
        "artifact": "execution_flow_map",
        "description": "Execution flow map (decision → guard stack → submission → telemetry/event store).",
        "clusters": EXECUTION_FLOW_CLUSTERS,
        "nodes": EXECUTION_FLOW_NODES,
        "edges": EXECUTION_FLOW_EDGES,
        "notes": [
            "TradingEngine._cycle submits strategy decisions directly to the guard stack.",
            "OrderSubmitter handles broker IO, telemetry, and persistence.",
            "Guard rejections emit metrics; stale marks append EventStore events.",
        ],
    }


def build_execution_flow_markdown(flow_map: dict[str, Any]) -> str:
    lines = [
        "# Execution Flow Map",
        "",
        f"Generated: {flow_map['generated_at']}",
        "",
    ]

    cluster_index = {cluster["id"]: cluster for cluster in flow_map["clusters"]}
    for cluster_id in [cluster["id"] for cluster in flow_map["clusters"]]:
        cluster = cluster_index[cluster_id]
        lines.append(f"## {cluster['label']}")
        lines.append("| ID | Label | Path |")
        lines.append("|----|-------|------|")
        for node in flow_map["nodes"]:
            if node["cluster"] == cluster_id:
                lines.append(f"| {node['id']} | {node['label']} | `{node['path']}` |")
        lines.append("")

    lines.extend(["## Edges", "| From | To | Description |", "|------|----|-------------|"])
    for edge in flow_map["edges"]:
        lines.append(f"| {edge['from']} | {edge['to']} | {edge['label']} |")

    lines.append("")
    lines.append("## Notes")
    for note in flow_map.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def build_execution_flow_dot(flow_map: dict[str, Any]) -> list[str]:
    lines = ["digraph ExecutionFlow {", "  rankdir=LR;"]
    for cluster in flow_map["clusters"]:
        lines.append(f"  subgraph cluster_{cluster['id']} {{")
        lines.append(f'    label="{cluster["label"]}";')
        lines.append("    style=rounded;")
        for node in flow_map["nodes"]:
            if node["cluster"] != cluster["id"]:
                continue
            label = f"{node['label']}\\n{node['path']}"
            lines.append(f'    "{node["id"]}" [shape=box, label="{label}"]; ')
        lines.append("  }")

    for edge in flow_map["edges"]:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}" [label="{edge["label"]}"];')
    lines.append("}")
    return lines


def build_market_data_flow_map() -> dict[str, Any]:
    return {
        "artifact": "market_data_flow_map",
        "description": "Market data flow map (REST polling + WS streaming).",
        "clusters": MARKET_DATA_FLOW_CLUSTERS,
        "nodes": MARKET_DATA_FLOW_NODES,
        "edges": MARKET_DATA_FLOW_EDGES,
        "notes": [
            "TradingEngine uses REST tickers/candles and records marks via PriceTickStore.",
            "WebSocket streaming updates runtime_state for orderbook + trade flow context.",
            "Risk mark-staleness checks read LiveRiskManager.last_mark_update timestamps.",
            "EventStore persistence captures mark updates and orderbook/trade summaries.",
            "TradingEngine._process_symbol calls Strategy.decide with recent marks and candle context.",
        ],
    }


def build_market_data_flow_markdown(flow_map: dict[str, Any]) -> str:
    lines = [
        "# Market Data Flow Map",
        "",
        f"Generated: {flow_map['generated_at']}",
        "",
    ]

    cluster_index = {cluster["id"]: cluster for cluster in flow_map["clusters"]}
    for cluster_id in [cluster["id"] for cluster in flow_map["clusters"]]:
        cluster = cluster_index[cluster_id]
        lines.append(f"## {cluster['label']}")
        lines.append("| ID | Label | Path |")
        lines.append("|----|-------|------|")
        for node in flow_map["nodes"]:
            if node["cluster"] == cluster_id:
                lines.append(f"| {node['id']} | {node['label']} | `{node['path']}` |")
        lines.append("")

    lines.extend(["## Edges", "| From | To | Description |", "|------|----|-------------|"])
    for edge in flow_map["edges"]:
        lines.append(f"| {edge['from']} | {edge['to']} | {edge['label']} |")

    lines.append("")
    lines.append("## Notes")
    for note in flow_map.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def build_market_data_flow_dot(flow_map: dict[str, Any]) -> list[str]:
    lines = ["digraph MarketDataFlow {", "  rankdir=LR;"]
    for cluster in flow_map["clusters"]:
        lines.append(f"  subgraph cluster_{cluster['id']} {{")
        lines.append(f'    label="{cluster["label"]}";')
        lines.append("    style=rounded;")
        for node in flow_map["nodes"]:
            if node["cluster"] != cluster["id"]:
                continue
            label = f"{node['label']}\\n{node['path']}"
            lines.append(f'    "{node["id"]}" [shape=box, label="{label}"]; ')
        lines.append("  }")

    for edge in flow_map["edges"]:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}" [label="{edge["label"]}"];')
    lines.append("}")
    return lines


def build_backtest_reporting_flow_map() -> dict[str, Any]:
    return {
        "artifact": "backtest_reporting_flow_map",
        "description": "Backtest reporting flow (broker → metrics → report outputs).",
        "clusters": BACKTEST_REPORTING_FLOW_CLUSTERS,
        "nodes": BACKTEST_REPORTING_FLOW_NODES,
        "edges": BACKTEST_REPORTING_FLOW_EDGES,
        "notes": [
            "BacktestReporter lazily computes TradeStatistics and RiskMetrics from the broker.",
            "generate_backtest_report is a convenience wrapper returning BacktestResult.",
        ],
    }


def build_backtest_reporting_flow_markdown(flow_map: dict[str, Any]) -> str:
    lines = [
        "# Backtest Reporting Flow Map",
        "",
        f"Generated: {flow_map['generated_at']}",
        "",
    ]

    cluster_index = {cluster["id"]: cluster for cluster in flow_map["clusters"]}
    for cluster_id in [cluster["id"] for cluster in flow_map["clusters"]]:
        cluster = cluster_index[cluster_id]
        lines.append(f"## {cluster['label']}")
        lines.append("| ID | Label | Path |")
        lines.append("|----|-------|------|")
        for node in flow_map["nodes"]:
            if node["cluster"] == cluster_id:
                lines.append(f"| {node['id']} | {node['label']} | `{node['path']}` |")
        lines.append("")

    lines.extend(["## Edges", "| From | To | Description |", "|------|----|-------------|"])
    for edge in flow_map["edges"]:
        lines.append(f"| {edge['from']} | {edge['to']} | {edge['label']} |")

    lines.append("")
    lines.append("## Notes")
    for note in flow_map.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def build_backtest_reporting_flow_dot(flow_map: dict[str, Any]) -> list[str]:
    lines = ["digraph BacktestReportingFlow {", "  rankdir=LR;"]
    for cluster in flow_map["clusters"]:
        lines.append(f"  subgraph cluster_{cluster['id']} {{")
        lines.append(f'    label="{cluster["label"]}";')
        lines.append("    style=rounded;")
        for node in flow_map["nodes"]:
            if node["cluster"] != cluster["id"]:
                continue
            label = f"{node['label']}\\n{node['path']}"
            lines.append(f'    "{node["id"]}" [shape=box, label="{label}"]; ')
        lines.append("  }")

    for edge in flow_map["edges"]:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}" [label="{edge["label"]}"];')
    lines.append("}")
    return lines


def build_backtest_entrypoints_map() -> dict[str, Any]:
    return {
        "artifact": "backtest_entrypoints_map",
        "description": "Backtest entrypoints (CLI, scripts, library) and their core engines.",
        "clusters": BACKTEST_ENTRYPOINTS_CLUSTERS,
        "nodes": BACKTEST_ENTRYPOINTS_NODES,
        "edges": BACKTEST_ENTRYPOINTS_EDGES,
        "notes": [
            "CLI optimize run drives BatchBacktestRunner for optimization trials.",
            "WalkForwardOptimizer and paper_trade_stress_test use ClockedBarRunner + SimulatedBroker.",
            "Research backtests rely on EventStore → HistoricalDataLoader → BacktestSimulator.",
            "Golden-path validation and chaos scenarios provide robustness checks and reports.",
            "ChaosEngine hooks are optional; integrate via ClockedBarRunner/SimulatedBroker if enabled.",
        ],
    }


def build_backtest_entrypoints_markdown(flow_map: dict[str, Any]) -> str:
    lines = [
        "# Backtest Entrypoints Map",
        "",
        f"Generated: {flow_map['generated_at']}",
        "",
    ]

    cluster_index = {cluster["id"]: cluster for cluster in flow_map["clusters"]}
    for cluster_id in [cluster["id"] for cluster in flow_map["clusters"]]:
        cluster = cluster_index[cluster_id]
        lines.append(f"## {cluster['label']}")
        lines.append("| ID | Label | Path |")
        lines.append("|----|-------|------|")
        for node in flow_map["nodes"]:
            if node["cluster"] == cluster_id:
                lines.append(f"| {node['id']} | {node['label']} | `{node['path']}` |")
        lines.append("")

    lines.extend(["## Edges", "| From | To | Description |", "|------|----|-------------|"])
    for edge in flow_map["edges"]:
        lines.append(f"| {edge['from']} | {edge['to']} | {edge['label']} |")

    lines.append("")
    lines.append("## Notes")
    for note in flow_map.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def build_backtest_entrypoints_dot(flow_map: dict[str, Any]) -> list[str]:
    lines = ["digraph BacktestEntrypoints {", "  rankdir=LR;"]
    for cluster in flow_map["clusters"]:
        lines.append(f"  subgraph cluster_{cluster['id']} {{")
        lines.append(f'    label="{cluster["label"]}";')
        lines.append("    style=rounded;")
        for node in flow_map["nodes"]:
            if node["cluster"] != cluster["id"]:
                continue
            label = f"{node['label']}\\n{node['path']}"
            lines.append(f'    "{node["id"]}" [shape=box, label="{label}"]; ')
        lines.append("  }")

    for edge in flow_map["edges"]:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}" [label="{edge["label"]}"];')
    lines.append("}")
    return lines


def build_backtest_validation_chaos_map() -> dict[str, Any]:
    return {
        "artifact": "backtest_validation_chaos_map",
        "description": "Backtest validation + chaos flow (guarded execution, decision logs, "
        "golden-path validation, chaos injection).",
        "clusters": BACKTEST_VALIDATION_CHAOS_CLUSTERS,
        "nodes": BACKTEST_VALIDATION_CHAOS_NODES,
        "edges": BACKTEST_VALIDATION_CHAOS_EDGES,
        "notes": [
            "BacktestGuardedExecutor reuses live guard stack components for parity.",
            "DecisionLogger captures StrategyDecision entries for replay and reporting.",
            "GoldenPathValidator compares live vs simulated decisions and emits ValidationReport.",
            "ChaosEngine hooks are optional; inject via simulation loops when enabled.",
        ],
    }


def build_backtest_validation_chaos_markdown(flow_map: dict[str, Any]) -> str:
    lines = [
        "# Backtest Validation + Chaos Flow Map",
        "",
        f"Generated: {flow_map['generated_at']}",
        "",
    ]

    cluster_index = {cluster["id"]: cluster for cluster in flow_map["clusters"]}
    for cluster_id in [cluster["id"] for cluster in flow_map["clusters"]]:
        cluster = cluster_index[cluster_id]
        lines.append(f"## {cluster['label']}")
        lines.append("| ID | Label | Path |")
        lines.append("|----|-------|------|")
        for node in flow_map["nodes"]:
            if node["cluster"] == cluster_id:
                lines.append(f"| {node['id']} | {node['label']} | `{node['path']}` |")
        lines.append("")

    lines.extend(["## Edges", "| From | To | Description |", "|------|----|-------------|"])
    for edge in flow_map["edges"]:
        lines.append(f"| {edge['from']} | {edge['to']} | {edge['label']} |")

    lines.append("")
    lines.append("## Notes")
    for note in flow_map.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def build_backtest_validation_chaos_dot(flow_map: dict[str, Any]) -> list[str]:
    lines = ["digraph BacktestValidationChaos {", "  rankdir=LR;"]
    for cluster in flow_map["clusters"]:
        lines.append(f"  subgraph cluster_{cluster['id']} {{")
        lines.append(f'    label="{cluster["label"]}";')
        lines.append("    style=rounded;")
        for node in flow_map["nodes"]:
            if node["cluster"] != cluster["id"]:
                continue
            label = f"{node['label']}\\n{node['path']}"
            lines.append(f'    "{node["id"]}" [shape=box, label="{label}"]; ')
        lines.append("  }")

    for edge in flow_map["edges"]:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}" [label="{edge["label"]}"];')
    lines.append("}")
    return lines


def _iter_python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if path.parts and path.parts[0] == "tests":
            continue
        yield path


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _find_usage(patterns: list[re.Pattern[str]], files: list[Path]) -> list[str]:
    hits: list[str] = []
    for file_path in files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue
        if any(pattern.search(content) for pattern in patterns):
            hits.append(_relative_path(file_path))
    return sorted(hits)


def _compile_patterns(prefix: str, field_name: str) -> list[re.Pattern[str]]:
    escaped = re.escape(field_name)
    prefix_escaped = re.escape(prefix)
    base_patterns = [
        rf"\\b{prefix_escaped}\\.{escaped}\\b",
        rf"\\bcontext\\.{prefix_escaped}\\.{escaped}\\b",
        rf"\\bself\\.config\\.{escaped}\\b",
        rf"\\bbot\\.config\\.{escaped}\\b",
        rf"\\bbot_config\\.{escaped}\\b",
    ]
    return [re.compile(pattern) for pattern in base_patterns]


def build_config_code_map() -> dict[str, Any]:
    from gpt_trader.app.config.bot_config import (
        BotConfig,
        BotRiskConfig,
        HealthThresholdsConfig,
        MeanReversionConfig,
    )
    from gpt_trader.features.live_trade.strategies.baseline import PerpsStrategyConfig

    files = list(_iter_python_files(SRC_ROOT))

    skip_fields = {
        "strategy",
        "risk",
        "mean_reversion",
        "health_thresholds",
        "regime_config",
        "ensemble_config",
    }

    sections = [
        {
            "name": "bot_config",
            "label": "BotConfig (top-level)",
            "prefix": "config",
            "fields": [f.name for f in fields(BotConfig) if f.name not in skip_fields],
        },
        {
            "name": "risk",
            "label": "BotRiskConfig",
            "prefix": "config.risk",
            "fields": [f.name for f in fields(BotRiskConfig)],
        },
        {
            "name": "strategy",
            "label": "PerpsStrategyConfig",
            "prefix": "config.strategy",
            "fields": [f.name for f in fields(PerpsStrategyConfig)],
        },
        {
            "name": "mean_reversion",
            "label": "MeanReversionConfig",
            "prefix": "config.mean_reversion",
            "fields": [f.name for f in fields(MeanReversionConfig)],
        },
        {
            "name": "health_thresholds",
            "label": "HealthThresholdsConfig",
            "prefix": "config.health_thresholds",
            "fields": [f.name for f in fields(HealthThresholdsConfig)],
        },
    ]

    alias_fields = {
        "short_ma": "strategy.short_ma_period",
        "long_ma": "strategy.long_ma_period",
        "target_leverage": "risk.target_leverage",
        "max_leverage": "risk.max_leverage",
        "trailing_stop_pct": "strategy.trailing_stop_pct or risk.trailing_stop_pct",
        "active_enable_shorts": "strategy.enable_shorts / mean_reversion.enable_shorts",
        "is_hybrid_mode": "trading_modes contains spot+cfm",
        "is_cfm_only": "trading_modes contains only cfm",
        "is_spot_only": "trading_modes contains only spot",
    }

    section_entries: list[dict[str, Any]] = []
    for section in sections:
        entries = []
        for field_name in section["fields"]:
            patterns = _compile_patterns(section["prefix"], field_name)
            usage = _find_usage(patterns, files)
            entries.append(
                {
                    "field": field_name,
                    "usage_count": len(usage),
                    "files": usage,
                    "notes": "No direct usage found" if not usage else "",
                }
            )
        entries.sort(key=lambda item: item["field"])
        section_entries.append(
            {
                "section": section["name"],
                "label": section["label"],
                "prefix": section["prefix"],
                "fields": entries,
            }
        )

    alias_entries = []
    for alias, target in alias_fields.items():
        patterns = _compile_patterns("config", alias)
        usage = _find_usage(patterns, files)
        alias_entries.append(
            {
                "alias": alias,
                "target": target,
                "usage_count": len(usage),
                "files": usage,
                "notes": "No direct usage found" if not usage else "",
            }
        )
    alias_entries.sort(key=lambda item: item["alias"])

    return {
        "artifact": "config_code_map",
        "generated_at": _timestamp(),
        "description": "Config field → code linkage map based on static scan.",
        "scan_root": "src/gpt_trader",
        "sections": section_entries,
        "aliases": alias_entries,
        "notes": [
            "Scan uses simple regex matching (config.<field>) across src/gpt_trader.",
            "Dynamic config access or indirect usage may not appear in results.",
        ],
    }


def build_config_code_markdown(config_map: dict[str, Any]) -> str:
    lines = [
        "# Config → Code Linkage Map",
        "",
        f"Generated: {config_map['generated_at']}",
        "",
        f"Scan root: `{config_map['scan_root']}`",
        "",
    ]

    for section in config_map["sections"]:
        lines.extend(
            [
                f"## {section['label']}",
                "| Field | Usage count | Example files |",
                "|-------|-------------|---------------|",
            ]
        )
        for field_entry in section["fields"]:
            examples = field_entry["files"][:3]
            example_text = ", ".join(f"`{item}`" for item in examples) if examples else "—"
            lines.append(
                f"| {field_entry['field']} | {field_entry['usage_count']} | {example_text} |"
            )
        lines.append("")

    lines.append("## Alias Fields")
    lines.append("| Alias | Canonical target | Usage count | Example files |")
    lines.append("|-------|------------------|-------------|---------------|")
    for alias_entry in config_map["aliases"]:
        examples = alias_entry["files"][:3]
        example_text = ", ".join(f"`{item}`" for item in examples) if examples else "—"
        lines.append(
            "| {alias} | {target} | {count} | {examples} |".format(
                alias=alias_entry["alias"],
                target=alias_entry["target"],
                count=alias_entry["usage_count"],
                examples=example_text,
            )
        )

    lines.append("")
    lines.append("## Notes")
    for note in config_map.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def build_config_code_dot(config_map: dict[str, Any]) -> list[str]:
    lines = ["digraph ConfigCode {", "  rankdir=LR;"]
    seen_nodes: set[str] = set()

    for section in config_map["sections"]:
        section_label = section["label"]
        if section_label not in seen_nodes:
            lines.append(f'  "{section_label}" [shape=box];')
            seen_nodes.add(section_label)
        file_counts: dict[str, int] = {}
        for field_entry in section["fields"]:
            for file_path in field_entry["files"]:
                file_counts[file_path] = file_counts.get(file_path, 0) + 1
        top_files = sorted(file_counts.items(), key=lambda item: item[1], reverse=True)[:12]
        for file_path, count in top_files:
            if file_path not in seen_nodes:
                lines.append(f'  "{file_path}" [shape=ellipse];')
                seen_nodes.add(file_path)
            lines.append(f'  "{section_label}" -> "{file_path}" [label="{count}"];')

    lines.append("}")
    return lines


class FlowValidationError(RuntimeError):
    """Raised when flow map validation fails in strict mode."""


def generate(
    output_dir: Path,
    *,
    validate: bool = False,
    strict: bool = False,
) -> dict[str, Path]:
    _ensure_output_dir(output_dir)
    flow_payloads: list[tuple[str, dict[str, Any]]] = []

    def _load_flow(
        filename: str,
        *,
        artifact: str,
        required_keys: Sequence[str],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        flow_def = load_flow_config(
            filename,
            artifact=artifact,
            required_keys=required_keys,
            fallback=fallback,
        )
        flow_payload = _build_flow_payload(flow_def)
        flow_payloads.append((artifact, flow_payload))
        return flow_payload

    cli_flow = _load_flow(
        "cli_flow.yaml",
        artifact="cli_flow_map",
        required_keys=("artifact", "description", "entrypoints", "nodes", "edges"),
        fallback=build_cli_flow_map(),
    )
    cli_flow_json = output_dir / "cli_flow_map.json"
    cli_flow_md = output_dir / "cli_flow_map.md"
    cli_flow_dot = output_dir / "cli_flow_map.dot"

    _write_json(cli_flow_json, cli_flow)
    _write_markdown(cli_flow_md, build_cli_flow_markdown(cli_flow))
    _write_dot(cli_flow_dot, build_cli_flow_dot(cli_flow))

    guard_map = _load_flow(
        "guard_stack.yaml",
        artifact="guard_stack_map",
        required_keys=("artifact", "description", "clusters", "nodes", "edges"),
        fallback=build_guard_stack_map(),
    )
    guard_json = output_dir / "guard_stack_map.json"
    guard_md = output_dir / "guard_stack_map.md"
    guard_dot = output_dir / "guard_stack_map.dot"

    _write_json(guard_json, guard_map)
    _write_markdown(guard_md, build_guard_stack_markdown(guard_map))
    _write_dot(guard_dot, build_guard_stack_dot(guard_map))

    execution_map = _load_flow(
        "execution_flow.yaml",
        artifact="execution_flow_map",
        required_keys=("artifact", "description", "clusters", "nodes", "edges"),
        fallback=build_execution_flow_map(),
    )
    execution_json = output_dir / "execution_flow_map.json"
    execution_md = output_dir / "execution_flow_map.md"
    execution_dot = output_dir / "execution_flow_map.dot"

    _write_json(execution_json, execution_map)
    _write_markdown(execution_md, build_execution_flow_markdown(execution_map))
    _write_dot(execution_dot, build_execution_flow_dot(execution_map))

    market_data_map = _load_flow(
        "market_data_flow.yaml",
        artifact="market_data_flow_map",
        required_keys=("artifact", "description", "clusters", "nodes", "edges"),
        fallback=build_market_data_flow_map(),
    )
    market_data_json = output_dir / "market_data_flow_map.json"
    market_data_md = output_dir / "market_data_flow_map.md"
    market_data_dot = output_dir / "market_data_flow_map.dot"

    _write_json(market_data_json, market_data_map)
    _write_markdown(market_data_md, build_market_data_flow_markdown(market_data_map))
    _write_dot(market_data_dot, build_market_data_flow_dot(market_data_map))

    backtest_reporting_map = _load_flow(
        "backtest_reporting_flow.yaml",
        artifact="backtest_reporting_flow_map",
        required_keys=("artifact", "description", "clusters", "nodes", "edges"),
        fallback=build_backtest_reporting_flow_map(),
    )
    backtest_reporting_json = output_dir / "backtest_reporting_flow_map.json"
    backtest_reporting_md = output_dir / "backtest_reporting_flow_map.md"
    backtest_reporting_dot = output_dir / "backtest_reporting_flow_map.dot"

    _write_json(backtest_reporting_json, backtest_reporting_map)
    _write_markdown(
        backtest_reporting_md,
        build_backtest_reporting_flow_markdown(backtest_reporting_map),
    )
    _write_dot(
        backtest_reporting_dot,
        build_backtest_reporting_flow_dot(backtest_reporting_map),
    )

    backtest_entrypoints_map = _load_flow(
        "backtest_entrypoints.yaml",
        artifact="backtest_entrypoints_map",
        required_keys=("artifact", "description", "clusters", "nodes", "edges"),
        fallback=build_backtest_entrypoints_map(),
    )
    backtest_entrypoints_json = output_dir / "backtest_entrypoints_map.json"
    backtest_entrypoints_md = output_dir / "backtest_entrypoints_map.md"
    backtest_entrypoints_dot = output_dir / "backtest_entrypoints_map.dot"

    _write_json(backtest_entrypoints_json, backtest_entrypoints_map)
    _write_markdown(
        backtest_entrypoints_md,
        build_backtest_entrypoints_markdown(backtest_entrypoints_map),
    )
    _write_dot(
        backtest_entrypoints_dot,
        build_backtest_entrypoints_dot(backtest_entrypoints_map),
    )

    backtest_validation_map = _load_flow(
        "backtest_validation_chaos.yaml",
        artifact="backtest_validation_chaos_map",
        required_keys=("artifact", "description", "clusters", "nodes", "edges"),
        fallback=build_backtest_validation_chaos_map(),
    )
    backtest_validation_json = output_dir / "backtest_validation_chaos_map.json"
    backtest_validation_md = output_dir / "backtest_validation_chaos_map.md"
    backtest_validation_dot = output_dir / "backtest_validation_chaos_map.dot"

    _write_json(backtest_validation_json, backtest_validation_map)
    _write_markdown(
        backtest_validation_md,
        build_backtest_validation_chaos_markdown(backtest_validation_map),
    )
    _write_dot(
        backtest_validation_dot,
        build_backtest_validation_chaos_dot(backtest_validation_map),
    )

    config_map = build_config_code_map()
    config_json = output_dir / "config_code_map.json"
    config_md = output_dir / "config_code_map.md"
    config_dot = output_dir / "config_code_map.dot"

    _write_json(config_json, config_map)
    _write_markdown(config_md, build_config_code_markdown(config_map))
    _write_dot(config_dot, build_config_code_dot(config_map))

    if validate:
        path_index = _build_path_index(("src", "scripts", "config", "tests"))
        issues: list[ValidationIssue] = []
        for artifact, flow in flow_payloads:
            issues.extend(validate_flow_map(flow, artifact=artifact, path_index=path_index))
        _report_validation(flow_payloads, issues)
        if issues and strict:
            raise FlowValidationError("Flow map validation failed.")

    return {
        "cli_flow_map.json": cli_flow_json,
        "cli_flow_map.md": cli_flow_md,
        "cli_flow_map.dot": cli_flow_dot,
        "guard_stack_map.json": guard_json,
        "guard_stack_map.md": guard_md,
        "guard_stack_map.dot": guard_dot,
        "execution_flow_map.json": execution_json,
        "execution_flow_map.md": execution_md,
        "execution_flow_map.dot": execution_dot,
        "market_data_flow_map.json": market_data_json,
        "market_data_flow_map.md": market_data_md,
        "market_data_flow_map.dot": market_data_dot,
        "backtest_reporting_flow_map.json": backtest_reporting_json,
        "backtest_reporting_flow_map.md": backtest_reporting_md,
        "backtest_reporting_flow_map.dot": backtest_reporting_dot,
        "backtest_entrypoints_map.json": backtest_entrypoints_json,
        "backtest_entrypoints_map.md": backtest_entrypoints_md,
        "backtest_entrypoints_map.dot": backtest_entrypoints_dot,
        "backtest_validation_chaos_map.json": backtest_validation_json,
        "backtest_validation_chaos_map.md": backtest_validation_md,
        "backtest_validation_chaos_map.dot": backtest_validation_dot,
        "config_code_map.json": config_json,
        "config_code_map.md": config_md,
        "config_code_map.dot": config_dot,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate reasoning artifacts (CLI flow + guard/execution + market data + "
            "backtesting + reporting + entrypoints + validation/chaos + config linkage)"
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Output directory (default: var/agents/reasoning)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate flow map node paths against the repository",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if validation finds missing paths (implies --validate)",
    )
    args = parser.parse_args()

    if args.strict:
        args.validate = True

    try:
        outputs = generate(
            args.output_dir,
            validate=args.validate,
            strict=args.strict,
        )
    except FlowValidationError:
        return 1

    print("Generated reasoning artifacts:")
    for name, path in outputs.items():
        print(f"- {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
