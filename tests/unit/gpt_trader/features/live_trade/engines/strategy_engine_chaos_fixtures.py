"""Shared fixtures for TradingEngine chaos tests."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from strategy_engine_chaos_helpers import make_position

from gpt_trader.app.config import BotConfig, BotRiskConfig
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
from gpt_trader.core import Balance
from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.engines.strategy import TradingEngine
from gpt_trader.features.live_trade.execution.order_submission import (
    OrderSubmissionOutcome,
    OrderSubmissionOutcomeStatus,
)
from gpt_trader.features.live_trade.strategies.baseline import Action, Decision


@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.get_ticker.return_value = {"price": "50000"}
    broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("10000"), available=Decimal("10000"))
    ]
    broker.list_positions.return_value = [make_position(qty="0.5")]
    broker.get_resilience_status.return_value = None
    broker.get_market_snapshot.return_value = {"spread_bps": 10, "depth_l1": 10000}
    broker.place_order.return_value = "order-123"
    return broker


@pytest.fixture
def mock_risk_config():
    config = MagicMock()
    config.broker_outage_max_failures, config.broker_outage_cooldown_seconds = 3, 120
    config.mark_staleness_cooldown_seconds, config.mark_staleness_allow_reduce_only = 60, True
    config.slippage_failure_pause_after, config.slippage_pause_seconds = 3, 60
    config.validation_failure_cooldown_seconds, config.preview_failure_disable_after = 180, 3
    (
        config.api_health_cooldown_seconds,
        config.api_error_rate_threshold,
        config.api_rate_limit_usage_threshold,
    ) = (
        300,
        0.2,
        0.9,
    )
    # WS health config
    config.ws_health_interval_seconds = 1  # Fast for tests
    config.ws_message_stale_seconds = 15
    config.ws_heartbeat_stale_seconds = 30
    config.ws_reconnect_pause_seconds = 10
    config.kill_switch_enabled = False
    return config


@pytest.fixture
def context(mock_broker, mock_risk_config):
    risk = BotRiskConfig(position_fraction=Decimal("0.1"))
    config = BotConfig(symbols=["BTC-USD"], interval=1, risk=risk)
    rm = MagicMock()
    rm._start_of_day_equity, rm.check_mark_staleness.return_value = Decimal("10000.0"), False
    rm.is_reduce_only_mode.return_value, rm.config = False, mock_risk_config
    return CoordinatorContext(config=config, broker=mock_broker, risk_manager=rm)


@pytest.fixture
def application_container(context):
    """Set up application container for TradingEngine chaos tests."""
    container = ApplicationContainer(context.config)
    set_application_container(container)
    yield container
    clear_application_container()


@pytest.fixture
def mock_security_validator(monkeypatch):
    validator = MagicMock()
    result = MagicMock()
    result.is_valid, result.errors = True, []
    validator.validate_order_request.return_value = result

    import gpt_trader.security.validate as security_validate_module

    monkeypatch.setattr(
        security_validate_module,
        "get_validator",
        MagicMock(return_value=validator),
    )
    return validator


@pytest.fixture
def engine(context, mock_security_validator, application_container, monkeypatch):
    strategy = MagicMock()
    strategy.decide.return_value, strategy.config.position_fraction = (
        Decision(Action.HOLD, "test"),
        Decimal("0.1"),
    )
    import gpt_trader.features.live_trade.engines.strategy as strategy_module

    monkeypatch.setattr(
        strategy_module,
        "create_strategy",
        MagicMock(return_value=strategy),
    )

    eng = TradingEngine(context)
    eng._state_collector = MagicMock()
    eng._state_collector.require_product.return_value = MagicMock()
    eng._state_collector.resolve_effective_price.return_value = Decimal("50000")
    eng._state_collector.build_positions_dict.return_value = {}
    eng._order_validator = MagicMock()
    eng._order_validator.validate_exchange_rules.return_value = (Decimal("0.02"), None)
    for attr in [
        "enforce_slippage_guard",
        "ensure_mark_is_fresh",
        "run_pre_trade_validation",
        "maybe_preview_order",
    ]:
        setattr(eng._order_validator, attr, MagicMock(return_value=None))
    from unittest.mock import AsyncMock

    eng._order_validator.maybe_preview_order_async = AsyncMock(return_value=None)
    eng._order_validator.finalize_reduce_only_flag.return_value = False
    eng._order_validator.enable_order_preview = True
    eng._order_submitter = MagicMock()
    eng._order_submitter.submit_order_with_result.return_value = OrderSubmissionOutcome(
        status=OrderSubmissionOutcomeStatus.SUCCESS,
        order_id="order-123",
    )
    yield eng
