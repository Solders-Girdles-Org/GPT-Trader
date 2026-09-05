"""Actual durable WS/REST accounting, crash recovery and evidence boundaries."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

from gpt_trader.core.fill_accounting import PositionBaseline
from gpt_trader.features.brokerages.coinbase.rest.pnl_service import PnLService
from gpt_trader.features.brokerages.coinbase.rest.position_state_store import PositionStateStore
from gpt_trader.features.brokerages.coinbase.user_event_handler import CoinbaseUserEventHandler
from gpt_trader.features.brokerages.coinbase.ws_events import FillEvent
from gpt_trader.persistence.orders_store import OrderRecord, OrdersStore, OrderStatus

NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def _setup(tmp_path):
    store = OrdersStore(tmp_path)
    store.initialize()
    store.accounting.record_baseline(
        PositionBaseline(
            "BTC-USD",
            (NOW - timedelta(days=1)).isoformat(),
            "0",
            None,
            "fixture: explicitly flat before first fill",
            actor_id="fixture",
        )
    )
    store.save_order(
        OrderRecord(
            order_id="order",
            client_order_id="client",
            symbol="BTC-USD",
            side="buy",
            order_type="market",
            quantity=Decimal(".1"),
            price=None,
            status=OrderStatus.OPEN,
            filled_quantity=Decimal(0),
            average_fill_price=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    handler = CoinbaseUserEventHandler(
        broker=None,
        orders_store=store,
        event_store=None,
        bot_id="fixture",
        market_data_service=None,
        symbols=["BTC-USD"],
    )
    market = Mock()
    market.get_mark.return_value = Decimal("120")
    pnl = PnLService(position_store=PositionStateStore(), market_data=market, orders_store=store)
    return store, handler, pnl


def _ws(handler, size, price):
    handler.handle_fill(
        FillEvent(
            "order",
            "client",
            "BTC-USD",
            "BUY",
            Decimal(price),
            Decimal(size),
            Decimal(0),
            Decimal(0),
            None,
            NOW,
        )
    )


def _rest(handler, fill_id, size, price, seconds=0):
    return handler._apply_rest_fill(
        {
            "fill_id": fill_id,
            "order_id": "order",
            "client_order_id": "client",
            "product_id": "BTC-USD",
            "side": "BUY",
            "size": size,
            "price": price,
            "trade_time": (NOW + timedelta(seconds=seconds)).isoformat(),
        }
    )
