"""
Deterministic broker for development, testing, and paper trading.

Used when MOCK_BROKER=1 to bypass real credentials and API calls.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from gpt_trader.core import (
    Balance,
    Candle,
    MarketType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Product,
    Quote,
    TimeInForce,
)
from gpt_trader.utilities.datetime_helpers import utc_now
from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="deterministic_broker")


class DeterministicBroker:
    """Mock broker with deterministic behavior for testing and development."""

    def __init__(self, equity: Decimal = Decimal("100000")) -> None:
        self._equity = equity
        self._marks: dict[str, Decimal] = {}
        self._positions: dict[str, Position] = {}
        self._balances: dict[str, Balance] = {
            "USDC": Balance(asset="USDC", total=equity, available=equity),
        }
        self._order_counter = 0
        logger.info(f"DeterministicBroker initialized with equity={equity}")

    def set_mark(self, symbol: str, price: Decimal) -> None:
        """Set mark price for a symbol (for test control)."""
        self._marks[symbol] = price

    def get_product(self, symbol: str) -> Product | None:
        """Return a synthetic product definition."""
        base, _, quote = symbol.partition("-")
        quote = quote or "USD"

        # Handle PERP suffix
        if base.endswith("PERP"):
            base = base.replace("-PERP", "").replace("PERP", "")
            market_type = MarketType.PERPETUAL
        else:
            market_type = MarketType.SPOT

        return Product(
            symbol=symbol,
            base_asset=base,
            quote_asset=quote,
            market_type=market_type,
            min_size=Decimal("0.001"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("10"),
            price_increment=Decimal("0.01"),
            leverage_max=3,
        )

    def get_quote(self, symbol: str) -> Quote:
        """Return a deterministic quote."""
        # Default prices based on symbol
        if symbol.startswith("BTC"):
            default_price = Decimal("50000")
        elif symbol.startswith("ETH"):
            default_price = Decimal("3000")
        else:
            default_price = Decimal("100")

        price = self._marks.get(symbol, default_price)
        spread = price * Decimal("0.0001")  # 1 bps spread

        return Quote(
            symbol=symbol,
            bid=price - spread / 2,
            ask=price + spread / 2,
            last=price,
            ts=utc_now(),
        )

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        """Return ticker data for a product (used by strategy engine)."""
        quote = self.get_quote(product_id)
        return {
            "price": str(quote.last),
            "bid": str(quote.bid),
            "ask": str(quote.ask),
            "time": quote.ts.isoformat(),
            "product_id": product_id,
        }

    def list_positions(self) -> list[Position]:
        """Return current positions."""
        return list(self._positions.values())

    def get_positions(self) -> list[Position]:
        """Alias for list_positions."""
        return self.list_positions()

    def list_balances(self) -> list[Balance]:
        """Return current balances."""
        return list(self._balances.values())

    def get_balances(self) -> list[Balance]:
        """Alias for list_balances."""
        return self.list_balances()

    def list_orders(self, **kwargs: Any) -> dict[str, Any]:
        """Return list of orders (always empty for deterministic broker as orders fill immediately)."""
        return {"orders": []}

    def place_order(
        self,
        symbol_or_payload: str | dict[str, Any] | None = None,
        side: str | OrderSide | None = None,
        order_type: str | OrderType = "market",
        quantity: Decimal | None = None,
        limit_price: Decimal | None = None,
        **kwargs: Any,
    ) -> Order:
        """Place order with deterministic immediate fill.

        Accepts either:
        - Positional args: symbol, side, order_type, quantity, limit_price
        - Keyword args: symbol=..., side=..., order_type=..., quantity=...
        - Dict payload: {"product_id": ..., "side": ..., "order_configuration": ...}
        """
        self._order_counter += 1
        client_id = kwargs.get("client_id")
        # A persisted client identity must survive mock-process restarts without
        # reusing a broker ID already attributed to a different request.
        order_id = (
            "MOCK_" + hashlib.sha256(str(client_id).encode()).hexdigest()
            if client_id
            else f"MOCK_{self._order_counter:06d}"
        )

        # Handle 'symbol' keyword arg (from BrokerExecutor)
        if symbol_or_payload is None:
            symbol_or_payload = kwargs.pop("symbol", "")

        # Handle 'price' keyword arg (alias for limit_price)
        if limit_price is None and "price" in kwargs:
            limit_price = kwargs.pop("price")

        # Handle dict payload from strategy engine
        if isinstance(symbol_or_payload, dict):
            payload = symbol_or_payload
            symbol = payload.get("product_id", "")
            side = payload.get("side", "BUY")
            # Extract quantity from order_configuration if present
            order_config = payload.get("order_configuration", {})
            if "market_market_ioc" in order_config:
                quote_size = order_config["market_market_ioc"].get("quote_size")
                if quote_size:
                    quantity = Decimal(quote_size) / Decimal("50000")  # Estimate
        else:
            symbol = symbol_or_payload or ""

        # Convert strings to enums
        if isinstance(side, str):
            side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        else:
            side_enum = side or OrderSide.BUY

        if isinstance(order_type, str):
            type_enum = OrderType.MARKET if order_type.lower() == "market" else OrderType.LIMIT
        else:
            type_enum = order_type

        now = utc_now()
        fill_price = self._marks.get(symbol, limit_price or Decimal("50000"))

        logger.info(
            f"DeterministicBroker: Placed {type_enum.value} {side_enum.value} "
            f"order for {symbol}, quantity={quantity}"
        )

        return Order(
            id=order_id,
            client_id=kwargs.get("client_id", order_id),
            symbol=symbol,
            side=side_enum,
            type=type_enum,
            quantity=quantity or Decimal("0.01"),
            price=fill_price,
            stop_price=kwargs.get("stop_price"),
            tif=kwargs.get("tif", TimeInForce.GTC),
            status=OrderStatus.FILLED,
            filled_quantity=quantity or Decimal("0.01"),
            avg_fill_price=fill_price,
            submitted_at=now,
            updated_at=now,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Mock order cancellation."""
        logger.info(f"DeterministicBroker: Cancelled order {order_id}")
        return True

    def get_candles(self, symbol: str, granularity: str, limit: int = 200) -> list[Candle]:
        """Return empty candles list."""
        return []

    def is_connected(self) -> bool:
        """Always returns True for mock broker."""
        return True

    def start_market_data(self, symbols: list[str]) -> None:
        """No-op for mock broker."""
        logger.info(f"DeterministicBroker: Mock market data started for {symbols}")

    def stop_market_data(self) -> None:
        """No-op for mock broker."""
        pass

    def is_stale(self, symbol: str, threshold_seconds: int = 10) -> bool:
        """Never stale for mock broker."""
        return False


__all__ = ["DeterministicBroker"]
