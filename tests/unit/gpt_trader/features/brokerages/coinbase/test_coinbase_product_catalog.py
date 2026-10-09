"""Coinbase product catalog tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.core import MarketType, NotFoundError
from gpt_trader.features.brokerages.coinbase.utilities import ProductCatalog
from gpt_trader.utilities.datetime_helpers import utc_now

pytestmark = pytest.mark.endpoints


class TestProductCatalog:
    def make_catalog(self, ttl_seconds: int = 900) -> ProductCatalog:
        return ProductCatalog(ttl_seconds=ttl_seconds)

    def test_catalog_refresh_with_perps(self) -> None:
        catalog = self.make_catalog()
        mock_client = MagicMock()
        mock_client.get_products.return_value = {
            "products": [
                {
                    "product_id": "BTC-USD",
                    "base_currency": "BTC",
                    "quote_currency": "USD",
                    "base_min_size": "0.001",
                    "base_increment": "0.00001",
                    "quote_increment": "0.01",
                },
                {
                    "product_id": "BTC-PERP",
                    "base_currency": "BTC",
                    "quote_currency": "USD",
                    "contract_type": "perpetual",
                    "base_min_size": "0.001",
                    "base_increment": "0.00001",
                    "quote_increment": "0.01",
                    "contract_size": "1",
                    "funding_rate": "0.0001",
                    "next_funding_time": "2024-01-15T16:00:00Z",
                    "max_leverage": 20,
                },
            ]
        }
        catalog.refresh(mock_client)
        assert len(catalog._cache) == 2
        perp = catalog._cache["BTC-PERP"]
        assert perp.market_type == MarketType.PERPETUAL
        assert perp.contract_size == Decimal("1")
        assert perp.funding_rate == Decimal("0.0001")
        assert perp.leverage_max == 20

    def test_catalog_get_with_expiry(self) -> None:
        catalog = self.make_catalog(ttl_seconds=1)
        mock_client = MagicMock()
        mock_client.get_products.return_value = {
            "products": [
                {
                    "product_id": "BTC-PERP",
                    "base_currency": "BTC",
                    "quote_currency": "USD",
                    "contract_type": "perpetual",
                    "base_min_size": "0.001",
                    "base_increment": "0.00001",
                    "quote_increment": "0.01",
                }
            ]
        }
        product = catalog.get(mock_client, "BTC-PERP")
        assert product.symbol == "BTC-PERP"
        assert mock_client.get_products.call_count == 1

        product = catalog.get(mock_client, "BTC-PERP")
        assert mock_client.get_products.call_count == 1

        catalog._last_refresh = utc_now() - timedelta(seconds=2)
        catalog.get(mock_client, "BTC-PERP")
        assert mock_client.get_products.call_count == 2

    def test_catalog_get_not_found(self) -> None:
        catalog = self.make_catalog()
        mock_client = MagicMock()
        mock_client.get_products.return_value = {"products": []}
        with pytest.raises(NotFoundError) as exc_info:
            catalog.get(mock_client, "MISSING-PERP")
        assert "Product not found: MISSING-PERP" in str(exc_info.value)
        assert mock_client.get_products.call_count == 1

    def test_catalog_get_funding_for_perpetual(self) -> None:
        catalog = self.make_catalog()
        mock_client = MagicMock()
        mock_client.get_products.return_value = {
            "products": [
                {
                    "product_id": "BTC-PERP",
                    "base_currency": "BTC",
                    "quote_currency": "USD",
                    "contract_type": "perpetual",
                    "base_min_size": "0.001",
                    "base_increment": "0.00001",
                    "quote_increment": "0.01",
                    "funding_rate": "0.0001",
                    "next_funding_time": "2024-01-15T16:00:00Z",
                }
            ]
        }
        funding_rate, next_funding = catalog.get_funding(mock_client, "BTC-PERP")
        assert funding_rate == Decimal("0.0001")
        assert next_funding == datetime(2024, 1, 15, 16, 0, 0, tzinfo=UTC)

    def test_catalog_get_funding_for_spot(self) -> None:
        catalog = self.make_catalog()
        mock_client = MagicMock()
        mock_client.get_products.return_value = {
            "products": [
                {
                    "product_id": "BTC-USD",
                    "base_currency": "BTC",
                    "quote_currency": "USD",
                    "base_min_size": "0.001",
                    "base_increment": "0.00001",
                    "quote_increment": "0.01",
                }
            ]
        }
        funding_rate, next_funding = catalog.get_funding(mock_client, "BTC-USD")
        assert funding_rate is None
        assert next_funding is None

    def test_catalog_handles_alternative_response_format(self) -> None:
        catalog = self.make_catalog()
        mock_client = MagicMock()
        mock_client.get_products.return_value = {
            "data": [
                {
                    "product_id": "ETH-PERP",
                    "base_currency": "ETH",
                    "quote_currency": "USD",
                    "contract_type": "perpetual",
                    "base_min_size": "0.01",
                    "base_increment": "0.001",
                    "quote_increment": "0.1",
                }
            ]
        }
        catalog.refresh(mock_client)
        assert "ETH-PERP" in catalog._cache
        assert catalog._cache["ETH-PERP"].market_type == MarketType.PERPETUAL
