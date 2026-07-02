"""
Utilities for Coinbase Brokerage.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from gpt_trader.core import (
    InvalidRequestError,
    NotFoundError,
    Product,
)
from gpt_trader.features.brokerages.coinbase.models import to_product
from gpt_trader.utilities.datetime_helpers import utc_now
from gpt_trader.utilities.quantization import quantize_to_increment


@dataclass
class PositionState:
    symbol: str
    side: Literal["long", "short"]
    quantity: Decimal
    entry_price: Decimal
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    leverage: int | None = None


class ProductCatalog:
    def __init__(self, ttl_seconds: int = 3600):
        self._cache: dict[str, Product] = {}
        self._funding: dict[str, tuple[Decimal, Any]] = {}
        self.ttl_seconds = ttl_seconds
        self._last_refresh = datetime.min.replace(tzinfo=UTC)

    def update_products(self, products: list[Product]) -> None:
        for p in products:
            self._cache[p.symbol] = p

    def refresh(self, client: Any) -> None:
        try:
            # Fetch products from client
            # Handle both list and dict responses as seen in tests
            response = client.get_products()
            products_data = []
            if isinstance(response, dict):
                products_data = response.get("products", []) or response.get("data", [])
            elif isinstance(response, list):
                products_data = response

            # Convert to Product objects
            products = []
            for p_data in products_data:
                try:
                    products.append(to_product(p_data))
                except Exception:
                    pass

            self.update_products(products)
            self._last_refresh = utc_now()
        except Exception:
            traceback.print_exc()

    def get(self, *args: Any) -> Product:
        client = None
        product_id = None
        if len(args) == 2:
            client, product_id = args
        elif len(args) == 1:
            product_id = args[0]
        else:
            raise TypeError(f"get() takes 1 or 2 arguments, got {len(args)}")

        # Check staleness first
        is_stale = False
        if self._cache:
            diff = (utc_now() - self._last_refresh).total_seconds()
            is_stale = diff > self.ttl_seconds
            # sys.stderr.write(f"DEBUG: Cache stale check: now={utc_now()}, last={self._last_refresh}, diff={diff}, ttl={self.ttl_seconds}, stale={is_stale}\n")
            # sys.stderr.flush()

        if client and (not self._cache or is_stale):
            # sys.stderr.write("DEBUG: Refreshing catalog\n")
            # sys.stderr.flush()
            self.refresh(client)

        product = self._cache.get(product_id)
        if not product:
            raise NotFoundError(f"Product not found: {product_id}")
        return product

    def get_funding(self, *args: Any) -> tuple[Decimal | None, datetime | None]:
        if len(args) == 2:
            client, product_id = args
        elif len(args) == 1:
            product_id = args[0]
        else:
            raise TypeError(f"get_funding() takes 1 or 2 arguments, got {len(args)}")

        if client:
            product = self.get(client, product_id)
        else:
            product = self.get(product_id)
        return (product.funding_rate, product.next_funding_time)


class FundingCalculator:
    def accrue_if_due(
        self, position: PositionState, rate: Decimal, next_funding: datetime | None
    ) -> Decimal:
        # simplified logic for now
        return Decimal("0")


def enforce_perp_rules(
    product: Product, quantity: Decimal, price: Decimal | None = None
) -> tuple[Decimal, Decimal | None]:
    # Quantize quantity
    quantity = quantize_to_increment(quantity, product.step_size)

    if quantity < product.min_size:
        raise InvalidRequestError(
            f"Order quantity {quantity} is below minimum size {product.min_size}"
        )

    quantized_price = price
    if price:
        quantized_price = quantize_to_increment(price, product.price_increment)
        if product.min_notional:
            notional = quantity * quantized_price
            if notional < product.min_notional:
                raise InvalidRequestError(
                    f"Order notional {notional} is below minimum {product.min_notional}"
                )

    return quantity, quantized_price
