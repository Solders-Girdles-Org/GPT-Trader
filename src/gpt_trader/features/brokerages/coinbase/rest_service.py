"""Composable REST service facade for Coinbase brokerage operations.

This module provides CoinbaseRestService, a composition-based facade over the
supported Coinbase REST operations.

The facade delegates to specialized service classes:
- ProductService: Product and market data operations
- OrderService: Order management operations
- PortfolioService: Balance and position management
- PnLService: PnL tracking and calculation
- CoinbaseRestServiceCore: Order payload building and execution
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from gpt_trader.app.config import BotConfig
from gpt_trader.core import (
    Balance,
    Candle,
    Order,
    OrderSide,
    OrderType,
    Position,
    Product,
    Quote,
    TimeInForce,
)
from gpt_trader.features.brokerages.coinbase.client import CoinbaseClient
from gpt_trader.features.brokerages.coinbase.endpoints import CoinbaseEndpoints
from gpt_trader.features.brokerages.coinbase.market_data_service import MarketDataService
from gpt_trader.features.brokerages.coinbase.models import APIConfig
from gpt_trader.features.brokerages.coinbase.rest.base import CoinbaseRestServiceCore
from gpt_trader.features.brokerages.coinbase.rest.order_service import OrderService
from gpt_trader.features.brokerages.coinbase.rest.pnl_service import PnLService
from gpt_trader.features.brokerages.coinbase.rest.portfolio_service import PortfolioService
from gpt_trader.features.brokerages.coinbase.rest.position_state_store import PositionStateStore
from gpt_trader.features.brokerages.coinbase.rest.product_service import ProductService
from gpt_trader.features.brokerages.coinbase.utilities import PositionState, ProductCatalog
from gpt_trader.persistence.event_store import EventStore
from gpt_trader.persistence.orders_store import OrdersStore


class CoinbaseRestService:
    """Composable facade for Coinbase REST endpoints.

    This class exposes the supported Coinbase REST facade API using composition
    internally instead of mixin inheritance.

    Each method delegates to the appropriate composed service:
    - ProductService: list_products, get_product, get_candles, etc.
    - OrderService: place_order, cancel_order, list_orders, etc.
    - PortfolioService: list_balances, list_positions, cfm_*
    - PnLService: process_fill_for_pnl, get_position_pnl, get_portfolio_pnl
    - CoinbaseRestServiceCore: update_position_metrics, payload building
    """

    def __init__(
        self,
        *,
        client: CoinbaseClient,
        endpoints: CoinbaseEndpoints,
        config: APIConfig,
        product_catalog: ProductCatalog,
        market_data: MarketDataService,
        event_store: EventStore,
        bot_config: BotConfig | None = None,
        orders_store: OrdersStore | None = None,
    ) -> None:
        # Create shared position state store
        self._position_store = PositionStateStore()

        # Create core service (for payload building/execution)
        self._core = CoinbaseRestServiceCore(
            client=client,
            endpoints=endpoints,
            config=config,
            product_catalog=product_catalog,
            market_data=market_data,
            event_store=event_store,
            position_store=self._position_store,
            bot_config=bot_config,
        )

        # Create composed services
        self._product_service = ProductService(
            client=client,
            product_catalog=product_catalog,
            market_data=market_data,
        )

        self._portfolio_service = PortfolioService(
            client=client,
            endpoints=endpoints,
            event_store=event_store,
        )

        self._order_service = OrderService(
            client=client,
            payload_builder=self._core,
            payload_executor=self._core,
            position_provider=self._portfolio_service,
        )

        self._pnl_service = PnLService(
            position_store=self._position_store,
            market_data=market_data,
            orders_store=orders_store,
        )

        # Expose shared dependencies for legacy access patterns
        self.client = client
        self.endpoints = endpoints
        self.config = config
        self._product_catalog = product_catalog
        self.market_data = market_data
        self._event_store = event_store
        self.bot_config = bot_config
        self._funding_calculator = self._core._funding_calculator

    @property
    def product_catalog(self) -> ProductCatalog:
        """Get product catalog."""
        return self._product_catalog

    @product_catalog.setter
    def product_catalog(self, value: ProductCatalog) -> None:
        """Set product catalog and update composed services."""
        self._product_catalog = value
        # Update composed services that depend on product_catalog
        self._product_service.product_catalog = value
        self._core.product_catalog = value

    # =========================================================================
    # Position State
    # =========================================================================

    @property
    def positions(self) -> dict[str, PositionState]:
        """Get position states (backward compatible)."""
        return self._position_store.all()

    # =========================================================================
    # Delegated Methods - ProductService
    # =========================================================================

    def list_products(self) -> list[Product]:
        """List all available products."""
        return self._product_service.list_products()

    def get_product(self, product_id: str) -> Product | None:
        """Get details of a single product."""
        return self._product_service.get_product(product_id)

    def get_rest_quote(self, symbol: str) -> Quote | None:
        """Get current quote (bid/ask/last) for a symbol via REST."""
        return self._product_service.get_rest_quote(symbol)

    def get_candles(self, symbol: str, **kwargs: Any) -> list[Candle]:
        """Get historical OHLCV candles for a symbol."""
        return self._product_service.get_candles(symbol, **kwargs)

    def get_futures(self) -> list[Product]:
        """List futures products."""
        return self._product_service.get_futures()

    def get_quote(self, symbol: str) -> Quote | None:
        """Get current quote for a symbol (BrokerProtocol)."""
        return self._product_service.get_quote(symbol)

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        """Get ticker data for a product (BrokerProtocol)."""
        return self._product_service.get_ticker(product_id)

    def get_mark_price(self, symbol: str) -> Decimal | None:
        """Get current mark price for a symbol (ExtendedBrokerProtocol)."""
        return self._product_service.get_mark_price(symbol)

    def get_tickers(self, product_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Get ticker data for multiple products in a single batch request.

        In Advanced API mode, uses the batch best_bid_ask endpoint.
        In Exchange API mode, falls back to individual get_ticker calls.
        """
        return self._product_service.get_tickers(product_ids)

    # =========================================================================
    # Delegated Methods - OrderService
    # =========================================================================

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        tif: TimeInForce = TimeInForce.GTC,
        client_id: str | None = None,
        reduce_only: bool = False,
        leverage: int | None = None,
        post_only: bool = False,
    ) -> Order:
        """Place a new order."""
        return self._order_service.place_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            tif=tif,
            client_id=client_id,
            reduce_only=reduce_only,
            leverage=leverage,
            post_only=post_only,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order."""
        return self._order_service.cancel_order(order_id)

    def list_orders(
        self,
        product_id: str | None = None,
        status: list[str] | None = None,
        limit: int = 100,
    ) -> list[Order]:
        """List orders with pagination."""
        return self._order_service.list_orders(product_id, status, limit)

    def get_order(self, order_id: str) -> Order | None:
        """Get details of a single order."""
        return self._order_service.get_order(order_id)

    def list_fills(
        self,
        product_id: str | None = None,
        order_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List fills with pagination."""
        return self._order_service.list_fills(product_id, order_id, limit)

    def close_position(
        self,
        symbol: str,
        client_order_id: str | None = None,
        fallback: Callable[[], Order] | None = None,
    ) -> Order:
        """Close position for a symbol."""
        return self._order_service.close_position(symbol, client_order_id, fallback)

    # =========================================================================
    # Delegated Methods - PortfolioService
    # =========================================================================

    def list_balances(self) -> list[Balance]:
        """List all balances."""
        return self._portfolio_service.list_balances()

    def get_portfolio_balances(self) -> list[Balance]:
        """Get portfolio balances."""
        return self._portfolio_service.get_portfolio_balances()

    def list_positions(self) -> list[Position]:
        """List all open positions."""
        return self._portfolio_service.list_positions()

    def get_position(self, symbol: str) -> Position | None:
        """Get position for a symbol."""
        return self._portfolio_service.get_position(symbol)

    def get_cfm_balance_summary(self) -> dict[str, Any]:
        """Get CFM balance summary."""
        return self._portfolio_service.get_cfm_balance_summary()

    def list_cfm_sweeps(self) -> list[dict[str, Any]]:
        """List CFM sweeps."""
        return self._portfolio_service.list_cfm_sweeps()

    def get_cfm_sweeps_schedule(self) -> dict[str, Any]:
        """Get CFM sweeps schedule."""
        return self._portfolio_service.get_cfm_sweeps_schedule()

    def get_cfm_margin_window(self) -> dict[str, Any]:
        """Get current CFM margin window."""
        return self._portfolio_service.get_cfm_margin_window()

    def update_cfm_margin_window(
        self,
        margin_window: str,
        effective_time: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update CFM margin window."""
        return self._portfolio_service.update_cfm_margin_window(
            margin_window, effective_time, extra_payload
        )

    # =========================================================================
    # Delegated Methods - PnLService
    # =========================================================================

    def process_fill_for_pnl(self, fill: dict[str, Any]) -> None:
        """Update position state and PnL based on a fill."""
        return self._pnl_service.process_fill_for_pnl(fill)

    def get_position_pnl(self, symbol: str) -> dict[str, Any]:
        """Get PnL metrics for a specific position."""
        return self._pnl_service.get_position_pnl(symbol)

    def get_portfolio_pnl(self) -> dict[str, Any]:
        """Get aggregated PnL for the portfolio."""
        return self._pnl_service.get_portfolio_pnl()

    # =========================================================================
    # Delegated Methods - Core Service
    # =========================================================================

    def update_position_metrics(self, symbol: str) -> None:
        """Update position metrics for a symbol."""
        return self._core.update_position_metrics(symbol)

    # =========================================================================
    # Legacy Private Methods for Backward Compatibility
    # =========================================================================

    def _build_order_payload(
        self,
        symbol: str,
        side: OrderSide | str,
        order_type: OrderType | str,
        quantity: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        tif: TimeInForce | str = TimeInForce.GTC,
        client_id: str | None = None,
        reduce_only: bool = False,
        leverage: int | None = None,
        post_only: bool = False,
        include_client_id: bool = True,
    ) -> dict[str, Any]:
        """Legacy method for building order payloads (backward compatibility)."""
        return self._core.build_order_payload(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            tif=tif,
            client_id=client_id,
            reduce_only=reduce_only,
            leverage=leverage,
            post_only=post_only,
            include_client_id=include_client_id,
        )

    def _execute_order_payload(
        self, symbol: str, payload: dict[str, Any], client_id: str | None = None
    ) -> Order:
        """Legacy method for executing order payloads (backward compatibility)."""
        return self._core.execute_order_payload(symbol, payload, client_id)


__all__ = ["CoinbaseRestService"]
