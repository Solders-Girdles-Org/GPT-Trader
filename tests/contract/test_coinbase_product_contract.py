"""Contract tests: Coinbase Advanced Trade products/ticker -> Product/Quote.

Recorded ``GET /api/v3/brokerage/products`` and public market ticker payloads
are served at the HTTP boundary and translated by the production
``CoinbaseRestService`` stack (client normalization + ``to_product``/``to_quote``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.core import MarketType

pytestmark = pytest.mark.contract

PRODUCTS_PATH = "/api/v3/brokerage/products"
PUBLIC_TICKER_PATH = "/api/v3/brokerage/market/products/BTC-USD/ticker"


def test_list_products_translates_advanced_trade_products(coinbase_service, transport):
    transport.route_fixture("GET", PRODUCTS_PATH, "products")

    products = coinbase_service.list_products()

    assert [p.symbol for p in products] == ["BTC-USD", "ETH-USD"]

    btc = products[0]
    # Advanced Trade uses base_currency_id/quote_currency_id field names.
    assert btc.base_asset == "BTC"
    assert btc.quote_asset == "USD"
    assert btc.market_type == MarketType.SPOT
    assert btc.min_size == Decimal("0.00000001")
    assert btc.step_size == Decimal("0.00000001")
    assert btc.price_increment == Decimal("0.01")

    eth = products[1]
    assert eth.base_asset == "ETH"
    assert eth.quote_asset == "USD"


def test_get_product_populates_catalog_from_products_endpoint(coinbase_service, transport):
    transport.route_fixture("GET", PRODUCTS_PATH, "products")

    product = coinbase_service.get_product("BTC-USD")

    assert product is not None
    assert product.symbol == "BTC-USD"
    assert product.base_asset == "BTC"
    assert product.step_size == Decimal("0.00000001")
    # The catalog refresh path must go through the real products endpoint.
    assert len(transport.requests_for("GET", PRODUCTS_PATH)) == 1


def test_get_rest_quote_translates_public_market_ticker(coinbase_service, transport):
    transport.route_fixture("GET", PUBLIC_TICKER_PATH, "ticker_btc_usd")

    quote = coinbase_service.get_rest_quote("BTC-USD")

    assert quote is not None
    assert quote.symbol == "BTC-USD"
    assert quote.bid == Decimal("67119.01")
    assert quote.ask == Decimal("67121.57")
    # Last trade price comes from the most recent entry in "trades".
    assert quote.last == Decimal("67120.99")
    assert quote.bid < quote.ask
    assert quote.ts == datetime(2026, 6, 30, 20, 4, 59, 123456, tzinfo=UTC)
    # Advanced mode must serve quotes from the public market ticker endpoint.
    assert len(transport.requests_for("GET", PUBLIC_TICKER_PATH)) == 1
