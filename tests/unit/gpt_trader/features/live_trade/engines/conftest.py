from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from gpt_trader.app.config import BotConfig, BotRiskConfig
from gpt_trader.app.container import (
    ApplicationContainer,
    clear_application_container,
    set_application_container,
)
from gpt_trader.core import (
    Balance,
    MarketType,
    Order,
    OrderStatus,
    Position,
    Product,
)
from gpt_trader.features.live_trade.engines.base import CoordinatorContext
from gpt_trader.features.live_trade.engines.strategy import TradingEngine
from gpt_trader.features.live_trade.execution.order_submission import (
    OrderSubmissionOutcome,
    OrderSubmissionOutcomeStatus,
)
from gpt_trader.features.live_trade.strategies.perps_baseline import Action, Decision


@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.get_ticker.return_value = {"price": "50000"}
    broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("1000"), available=Decimal("1000")),
        Balance(asset="BTC", total=Decimal("1"), available=Decimal("1")),
    ]
    broker.list_positions.return_value = [
        Position(
            symbol="BTC-USD",
            quantity=Decimal("0.5"),
            entry_price=Decimal("40000"),
            mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("5000"),
            realized_pnl=Decimal("0"),
            side="long",
        )
    ]
    return broker


@pytest.fixture
def mock_strategy():
    strategy = MagicMock()
    strategy.decide.return_value = Decision(Action.HOLD, "test")
    config = MagicMock()
    config.position_fraction = None
    strategy.config = config
    return strategy


def _make_risk_manager() -> MagicMock:
    risk_manager = MagicMock()
    risk_manager._start_of_day_equity = Decimal("1000.0")
    risk_manager.check_mark_staleness.return_value = False
    risk_manager.config = MagicMock()
    risk_manager.config.broker_outage_max_failures = 3
    risk_manager.config.broker_outage_cooldown_seconds = 120
    risk_manager.config.mark_staleness_cooldown_seconds = 120
    risk_manager.config.mark_staleness_allow_reduce_only = True
    risk_manager.config.slippage_failure_pause_after = 3
    risk_manager.config.slippage_pause_seconds = 60
    risk_manager.config.validation_failure_cooldown_seconds = 180
    risk_manager.config.preview_failure_disable_after = 3
    risk_manager.config.api_health_cooldown_seconds = 300
    return risk_manager


@pytest.fixture
def context(mock_broker, application_container):
    risk = BotRiskConfig(position_fraction=Decimal("0.1"))
    config = BotConfig(symbols=["BTC-USD"], interval=1, risk=risk)
    return CoordinatorContext(
        config=config,
        container=application_container,
        broker=mock_broker,
        risk_manager=_make_risk_manager(),
    )


@pytest.fixture
def application_container():
    """Create container before context - no global needed."""
    risk = BotRiskConfig(position_fraction=Decimal("0.1"))
    config = BotConfig(symbols=["BTC-USD"], interval=1, risk=risk)
    container = ApplicationContainer(config)
    # Also set global for any code paths that still use service locator
    set_application_container(container)
    yield container
    clear_application_container()


@pytest.fixture
def engine(context, mock_strategy, application_container, monkeypatch):
    import gpt_trader.features.live_trade.engines.strategy as strategy_module

    monkeypatch.setattr(
        strategy_module,
        "create_strategy",
        MagicMock(return_value=mock_strategy),
    )

    engine = TradingEngine(context)
    engine.strategy = mock_strategy

    engine._state_collector = MagicMock()
    engine._state_collector.require_product.return_value = MagicMock()
    engine._state_collector.resolve_effective_price.return_value = Decimal("50000")
    engine._state_collector.build_positions_dict.return_value = {}

    engine._order_validator = MagicMock()
    engine._order_validator.validate_exchange_rules.return_value = (
        Decimal("0.02"),
        None,
    )
    engine._order_validator.enforce_slippage_guard.return_value = None
    engine._order_validator.ensure_mark_is_fresh.return_value = None
    engine._order_validator.run_pre_trade_validation.return_value = None
    engine._order_validator.maybe_preview_order_async = AsyncMock(return_value=None)
    engine._order_validator.maybe_preview_order.return_value = None
    engine._order_validator.finalize_reduce_only_flag.return_value = False

    engine._order_submitter = MagicMock()
    engine._order_submitter.submit_order_with_result.return_value = OrderSubmissionOutcome(
        status=OrderSubmissionOutcomeStatus.SUCCESS,
        order_id="order-123",
    )

    return engine


@pytest.fixture
def real_flow_product() -> Product:
    return Product(
        symbol="BTC-USD",
        base_asset="BTC",
        quote_asset="USD",
        market_type=MarketType.SPOT,
        min_size=Decimal("0.0001"),
        step_size=Decimal("0.00001"),
        min_notional=Decimal("1"),
        price_increment=Decimal("0.01"),
        leverage_max=None,
    )


@pytest.fixture
def real_flow_broker(real_flow_product):
    """Boundary-double broker for the real-flow engine.

    Unlike ``mock_broker``, every configured return value is a real domain
    object so the production StateCollector/OrderValidator/OrderSubmitter
    stack can run against it unstubbed.
    """
    broker = MagicMock()
    broker.get_ticker.return_value = {"price": "50000"}
    broker.get_candles.return_value = []
    broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("1000"), available=Decimal("1000")),
    ]
    broker.list_positions.return_value = []
    broker.get_product.return_value = real_flow_product
    broker.get_mark_price.return_value = Decimal("50000")
    # None skips the slippage guard's market-impact math; tests that exercise
    # the guard set a real snapshot dict.
    broker.get_market_snapshot.return_value = None

    order_ids = iter(f"order-{n}" for n in range(1, 1000))

    def _place_order(**kwargs):
        return Order(
            id=next(order_ids),
            symbol=kwargs["symbol"],
            side=kwargs["side"],
            type=kwargs["order_type"],
            quantity=kwargs["quantity"],
            status=OrderStatus.SUBMITTED,
            price=kwargs.get("price"),
            tif=kwargs.get("tif") or "GTC",
            client_id=kwargs.get("client_id"),
            submitted_at=datetime.now(UTC),
        )

    broker.place_order.side_effect = _place_order
    return broker


@pytest.fixture
def real_flow_context(real_flow_broker, tmp_path):
    """CoordinatorContext whose engine keeps its REAL guard stack.

    Only the process boundaries are doubles: the broker (``real_flow_broker``)
    and the risk manager (MagicMock with explicitly passing defaults). The
    orders/event stores are isolated under ``tmp_path`` because the real
    OrderSubmitter persists submissions.
    """
    risk = BotRiskConfig(position_fraction=Decimal("0.1"))
    config = BotConfig(
        symbols=["BTC-USD"],
        interval=1,
        risk=risk,
        runtime_root=str(tmp_path),
    )
    container = ApplicationContainer(config)
    set_application_container(container)

    risk_manager = _make_risk_manager()
    # The real validator/submitter consult these boundaries; MagicMock's
    # truthy auto-attributes would otherwise silently flip trading modes.
    risk_manager.is_reduce_only_mode.return_value = False
    risk_manager.check_order.return_value = True
    risk_manager.pre_trade_validate.return_value = None
    risk_manager._daily_pnl_triggered = False
    risk_manager.last_mark_update = {}
    risk_manager.config.slippage_guard_bps = 50
    risk_manager.config.kill_switch_enabled = False

    yield CoordinatorContext(
        config=config,
        container=container,
        broker=real_flow_broker,
        risk_manager=risk_manager,
    )
    clear_application_container()


@pytest.fixture
def real_flow_engine(real_flow_context, mock_strategy, monkeypatch):
    """TradingEngine wired with its production validator/submitter stack.

    No engine internals are stubbed, so tests written against this fixture
    survive strategy.py decomposition refactors. Steer behavior at the
    broker/risk-manager boundary instead of patching ``_order_validator``,
    ``_order_submitter``, or ``_state_collector``.
    """
    import gpt_trader.features.live_trade.engines.strategy as strategy_module

    monkeypatch.setattr(
        strategy_module,
        "create_strategy",
        MagicMock(return_value=mock_strategy),
    )

    engine = TradingEngine(real_flow_context)
    engine.strategy = mock_strategy
    return engine
