"""Contract tests: Coinbase Advanced Trade order endpoints -> core Order objects.

Covers the full order lifecycle against recorded Advanced Trade payloads:
request building for order placement (quantization, order_configuration) and
response translation for create/get/list/cancel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.core import OrderSide, OrderStatus, OrderType, TimeInForce
from gpt_trader.features.brokerages.coinbase.errors import OrderCancellationError

pytestmark = pytest.mark.contract

PRODUCTS_PATH = "/api/v3/brokerage/products"
ORDERS_PATH = "/api/v3/brokerage/orders"
ORDERS_HISTORICAL_PATH = "/api/v3/brokerage/orders/historical"
BATCH_CANCEL_PATH = "/api/v3/brokerage/orders/batch_cancel"

ORDER_ID = "a8f7e6d5-c4b3-42a1-9080-7f6e5d4c3b2a"


def test_get_order_translates_historical_order(coinbase_service, transport):
    transport.route_fixture("GET", f"{ORDERS_HISTORICAL_PATH}/{ORDER_ID}", "order_historical")

    order = coinbase_service.get_order(ORDER_ID)

    assert order is not None
    assert order.id == ORDER_ID
    assert order.client_id == "gpt-trader-7c1f9b2e4d"
    assert order.symbol == "BTC-USD"
    assert order.side == OrderSide.BUY
    assert order.type == OrderType.LIMIT
    assert order.status == OrderStatus.SUBMITTED  # Advanced Trade "OPEN"
    assert order.tif == TimeInForce.GTC  # "GOOD_UNTIL_CANCELLED"
    # Size and price live inside order_configuration.limit_limit_gtc.
    assert order.quantity == Decimal("0.005")
    assert order.price == Decimal("65000.00")
    assert order.filled_quantity == Decimal("0.002")
    assert order.avg_fill_price == Decimal("64998.75")
    assert order.submitted_at == datetime(2026, 6, 30, 18, 45, 12, tzinfo=UTC)


def test_get_order_quote_sized_market_uses_filled_base_size(coinbase_service, transport):
    order_id = "e5f6a7b8-c9d0-4122-8033-445566778899"
    transport.route_fixture(
        "GET", f"{ORDERS_HISTORICAL_PATH}/{order_id}", "order_historical_quote_sized"
    )

    order = coinbase_service.get_order(order_id)

    assert order is not None
    assert order.type == OrderType.MARKET
    assert order.tif == TimeInForce.IOC
    assert order.status == OrderStatus.FILLED
    # Quote-sized orders have no base_size; quantity must fall back to the
    # executed base amount instead of reporting a zero-size order.
    assert order.quantity == Decimal("0.00149")
    assert order.filled_quantity == Decimal("0.00149")
    assert order.avg_fill_price == Decimal("67114.09")


def test_get_order_returns_none_for_unknown_order(coinbase_service, transport):
    # No route registered: the transport answers 404, which the client maps
    # to NotFoundError and the service translates to None.
    order = coinbase_service.get_order("does-not-exist")

    assert order is None


def test_list_orders_translates_order_page(coinbase_service, transport):
    transport.route_fixture("GET", ORDERS_HISTORICAL_PATH, "orders_historical")

    orders = coinbase_service.list_orders()

    assert len(orders) == 2

    limit_order, market_order = orders
    assert limit_order.id == ORDER_ID
    assert limit_order.type == OrderType.LIMIT
    assert limit_order.side == OrderSide.BUY
    assert limit_order.status == OrderStatus.SUBMITTED

    assert market_order.symbol == "ETH-USD"
    assert market_order.type == OrderType.MARKET
    assert market_order.side == OrderSide.SELL
    assert market_order.status == OrderStatus.FILLED
    assert market_order.tif == TimeInForce.IOC  # "IMMEDIATE_OR_CANCEL"
    assert market_order.quantity == Decimal("1.5")
    assert market_order.avg_fill_price == Decimal("2531.42")

    (request,) = transport.requests_for("GET", ORDERS_HISTORICAL_PATH)
    assert request.query["limit"] == ["100"]


def test_cancel_order_translates_batch_cancel_result(coinbase_service, transport):
    transport.route_fixture("POST", BATCH_CANCEL_PATH, "orders_batch_cancel")

    assert coinbase_service.cancel_order(ORDER_ID) is True

    (request,) = transport.requests_for("POST", BATCH_CANCEL_PATH)
    assert request.body == {"order_ids": [ORDER_ID]}


def test_cancel_order_rejection_raises(coinbase_service, transport):
    transport.route_fixture("POST", BATCH_CANCEL_PATH, "orders_batch_cancel_rejected")

    with pytest.raises(OrderCancellationError, match="UNKNOWN_CANCEL_ORDER"):
        coinbase_service.cancel_order(ORDER_ID)


def test_place_limit_order_round_trip(coinbase_service, transport):
    transport.route_fixture("GET", PRODUCTS_PATH, "products")
    transport.route_fixture("POST", ORDERS_PATH, "order_create_success")

    # Realistic sequence: product discovery seeds the catalog used to
    # quantize order sizes, then the order is placed.
    assert coinbase_service.get_product("BTC-USD") is not None

    order = coinbase_service.place_order(
        symbol="BTC-USD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.005"),
        price=Decimal("65000"),
        tif=TimeInForce.GTC,
        client_id="gpt-trader-contract-1",
    )

    # Request contract: Advanced Trade create-order payload.
    (request,) = transport.requests_for("POST", ORDERS_PATH)
    assert request.body is not None
    assert request.body["product_id"] == "BTC-USD"
    assert request.body["side"] == "BUY"
    assert request.body["client_order_id"] == "gpt-trader-contract-1"
    limit_config = request.body["order_configuration"]["limit_limit_gtc"]
    assert limit_config["base_size"] == "0.005"
    assert limit_config["limit_price"] == "65000.00"

    # Response contract: success envelope unwrapped into a domain Order.
    assert order.id == "c1d2e3f4-a5b6-4788-99aa-bbccddeeff00"
    assert order.client_id == "gpt-trader-contract-1"
    assert order.symbol == "BTC-USD"
    assert order.side == OrderSide.BUY
    assert order.type == OrderType.LIMIT
    assert order.quantity == Decimal("0.005")
    assert order.price == Decimal("65000.00")
    assert order.status == OrderStatus.SUBMITTED
    assert order.tif == TimeInForce.GTC  # inferred from limit_limit_gtc


def test_place_market_order_reports_ioc_tif(coinbase_service, transport):
    transport.route_fixture("GET", PRODUCTS_PATH, "products")
    transport.route_fixture("POST", ORDERS_PATH, "order_create_market_success")

    assert coinbase_service.get_product("ETH-USD") is not None

    order = coinbase_service.place_order(
        symbol="ETH-USD",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.25"),
        client_id="gpt-trader-contract-2",
    )

    (request,) = transport.requests_for("POST", ORDERS_PATH)
    assert request.body is not None
    assert request.body["order_configuration"]["market_market_ioc"]["base_size"] == "0.25"

    # Create-order responses omit time_in_force; the fulfillment policy must
    # be inferred from the market_market_ioc configuration key.
    assert order.id == "d4e5f6a7-b8c9-4011-8899-aabbccddee11"
    assert order.symbol == "ETH-USD"
    assert order.side == OrderSide.SELL
    assert order.type == OrderType.MARKET
    assert order.tif == TimeInForce.IOC
    assert order.quantity == Decimal("0.25")
    assert order.price is None
