"""
State collection utilities for live trading execution.

This module handles collecting and transforming account state including
balances, positions, equity calculations, and collateral asset resolution.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from gpt_trader.app.config import BotConfig
from gpt_trader.config.constants import (
    DEFAULT_QUOTE_INCREMENT,
    MIN_COLLATERAL_CHANGE_TO_LOG,
)
from gpt_trader.core import Balance, MarketType, Product
from gpt_trader.core.protocols import ExtendedBrokerProtocol
from gpt_trader.utilities.logging_patterns import get_logger
from gpt_trader.utilities.quantities import quantity_from

logger = get_logger(__name__, component="state_collection")


class StateCollector:
    """Collects and transforms account state for execution and risk management."""

    def __init__(
        self,
        broker: ExtendedBrokerProtocol,
        config: BotConfig,
        *,
        integration_mode: bool = False,
    ) -> None:
        """
        Initialize state collector.

        Args:
            broker: Brokerage adapter implementing ExtendedBrokerProtocol
            config: Bot configuration
            integration_mode: Enable integration test mode with synthetic fallbacks.
        """
        self.broker = broker
        self._config = config
        self._integration_mode = integration_mode

        self.collateral_assets = self._resolve_collateral_assets()
        self._last_collateral_available: Decimal | None = None

        # Initialize production logger for balance updates
        from gpt_trader.monitoring.system import get_logger as get_prod_logger

        self._production_logger = get_prod_logger(settings=None)

    def log_collateral_update(
        self,
        collateral_balances: list[Balance],
        equity: Decimal,
        collateral_total: Decimal,
        all_balances: list[Balance],
    ) -> None:
        """Log collateral balance changes."""
        if not collateral_balances:
            return

        total_available = sum((b.available for b in collateral_balances), Decimal("0"))

        change_value: Decimal | None = None
        if self._last_collateral_available is not None:
            diff = total_available - self._last_collateral_available
            change_value = diff
            if abs(diff) > Decimal(str(MIN_COLLATERAL_CHANGE_TO_LOG)):
                logger.info(
                    "Collateral available changed",
                    previous=float(self._last_collateral_available),
                    current=float(total_available),
                    delta=float(diff),
                    operation="collateral_update",
                )

        self._last_collateral_available = total_available

        # Log to telemetry
        try:
            currency = collateral_balances[0].asset if collateral_balances else "USD"
            self._production_logger.log_balance_update(
                currency=currency,
                available=float(total_available),
                total=float(collateral_total),
                equity=float(equity),
                change=float(change_value) if change_value is not None else None,
            )
        except Exception as exc:
            logger.error(
                "Failed to log balance update to telemetry",
                error_type=type(exc).__name__,
                error_message=str(exc),
                operation="log_collateral_update",
                equity=float(equity),
            )

    def _resolve_collateral_assets(self) -> set[str]:
        """Resolve collateral assets from environment or use defaults."""
        env_value = os.getenv("PERPS_COLLATERAL_ASSETS", "")
        default_assets = {"USD", "USDC"}
        parsed = {token.strip().upper() for token in env_value.split(",") if token.strip()}
        return parsed or set(default_assets)

    def calculate_equity_from_balances(
        self,
        balances: list[Balance],
    ) -> tuple[Decimal, list[Balance], Decimal]:
        """
        Calculate total equity from balance list.

        Args:
            balances: List of balance objects

        Returns:
            Tuple of (total_available, collateral_balances, total_balance)
        """
        total_available = Decimal("0")
        total_balance = Decimal("0")
        collateral_balances: list[Balance] = []

        for bal in balances:
            asset = (bal.asset or "").upper()
            if asset in self.collateral_assets:
                collateral_balances.append(bal)
                total_available += bal.available
                total_balance += bal.total

        if collateral_balances:
            return total_available, collateral_balances, total_balance

        usd_balance = next((bal for bal in balances if (bal.asset or "").upper() == "USD"), None)
        if usd_balance:
            return usd_balance.available, [usd_balance], usd_balance.total

        return Decimal("0"), [], Decimal("0")

    def collect_account_state(
        self,
    ) -> tuple[list[Balance], Decimal, list[Balance], Decimal, list[Any]]:
        """
        Collect complete account state from broker.

        Returns:
            Tuple of (balances, equity, collateral_balances, total_balance, positions)
        """
        balances_data: Iterable[Balance] | None = None
        if hasattr(self.broker, "list_balances"):
            try:
                balances_data = self.broker.list_balances()
            except Exception:
                if not self._integration_mode:
                    raise
        balances: list[Balance] = list(balances_data) if balances_data is not None else []
        if not balances and self._integration_mode:
            balances = [
                Balance(
                    asset="USD",
                    total=Decimal("100000"),
                    available=Decimal("100000"),
                    hold=Decimal("0"),
                )
            ]
        equity, collateral_balances, total_balance = self.calculate_equity_from_balances(balances)
        positions_data: Iterable[Any] | None = None
        if hasattr(self.broker, "list_positions"):
            try:
                positions_data = self.broker.list_positions()
            except Exception:
                if not self._integration_mode:
                    raise
        positions: list[Any] = list(positions_data) if positions_data is not None else []
        if self._integration_mode and not positions:
            positions = []

        return balances, equity, collateral_balances, total_balance, positions

    def build_positions_dict(self, positions: list[Any]) -> dict[str, dict[str, Any]]:
        """
        Build simplified position dictionary for validation.

        Args:
            positions: List of position objects

        Returns:
            Dictionary mapping symbol to position details
        """
        positions_dict: dict[str, dict[str, Any]] = {}
        for pos in positions:
            qty = quantity_from(pos)  # naming: allow
            if qty is None or qty == Decimal("0"):  # naming: allow
                continue
            try:
                positions_dict[pos.symbol] = {
                    "quantity": qty,  # naming: allow
                    "side": getattr(pos, "side", "long").lower(),
                    "entry_price": Decimal(str(getattr(pos, "entry_price", "0"))),
                    "mark_price": Decimal(str(getattr(pos, "mark_price", "0"))),
                }
            except Exception as exc:
                logger.warning(
                    "Failed to parse position",
                    symbol=getattr(pos, "symbol", ""),
                    error=str(exc),
                    operation="state_collection",
                    stage="positions",
                )
                continue
        return positions_dict

    def resolve_effective_price(
        self,
        symbol: str,
        side: str,
        price: Decimal | None,
        product: Product,
    ) -> Decimal:
        """
        Resolve effective price for order validation.

        Args:
            symbol: Trading symbol
            side: Order side ("buy" or "sell")
            price: User-provided price (None for market orders)
            product: Product specifications

        Returns:
            Effective price to use for validation
        """
        if price is not None and price > Decimal("0"):
            return price

        # For market orders, use mark price or bid/ask
        if hasattr(self.broker, "get_mark_price"):
            try:
                mark = self.broker.get_mark_price(symbol)
                if mark and mark > Decimal("0"):
                    return Decimal(str(mark))
            except Exception as exc:
                logger.error(
                    "Failed to get mark price from broker",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="resolve_effective_price",
                    symbol=symbol,
                )

        # Fallback to mid-price
        if hasattr(product, "bid_price") and hasattr(product, "ask_price"):
            if product.bid_price and product.ask_price:
                bid = Decimal(str(product.bid_price))
                ask = Decimal(str(product.ask_price))
                if bid > Decimal("0") and ask > Decimal("0"):
                    return (bid + ask) / Decimal("2")

        # Broker quote fallback
        if hasattr(self.broker, "get_quote"):
            try:
                quote = self.broker.get_quote(symbol)
                last = getattr(quote, "last", None)
                if last is not None:
                    last_decimal = Decimal(str(last))
                    if last_decimal > Decimal("0"):
                        return last_decimal
            except Exception as exc:
                logger.error(
                    "Failed to get quote from broker",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    operation="resolve_effective_price",
                    symbol=symbol,
                )

        # Last resort: use last price or quote_increment
        if hasattr(product, "price") and product.price:
            return Decimal(str(product.price))

        # If all else fails, use a default based on quote_increment
        if hasattr(product, "quote_increment") and product.quote_increment:
            quote_inc = Decimal(str(product.quote_increment))
        else:
            quote_inc = Decimal(str(DEFAULT_QUOTE_INCREMENT))
        return quote_inc * Decimal("100")

    def require_product(self, symbol: str, product: Product | None) -> Product:
        """
        Ensure product specification is available.

        Args:
            symbol: Trading symbol
            product: Product object or None

        Returns:
            Valid product object

        Raises:
            ValidationError: If product cannot be resolved
        """
        if product is not None:
            return product

        # Try to fetch from broker
        fetched = self.broker.get_product(symbol)
        if fetched is None:
            # Import here to avoid circular dependency
            from gpt_trader.features.live_trade.risk import ValidationError

            if self._integration_mode:
                # Provide a permissive synthetic product for integration scenarios.
                base_asset, quote_asset = symbol.split("-", 1) if "-" in symbol else (symbol, "USD")
                return cast(
                    Product,
                    SimpleNamespace(
                        symbol=symbol,
                        base_asset=base_asset,
                        quote_asset=quote_asset,
                        market_type=MarketType.PERPETUAL,
                        min_size=Decimal("0.001"),
                        step_size=Decimal("0.001"),
                        price_increment=Decimal("0.01"),
                        min_notional=Decimal("1"),
                        leverage_max=None,
                        contract_size=None,
                        funding_rate=None,
                        next_funding_time=None,
                        bid_price=None,
                        ask_price=None,
                        price=None,
                        quote_increment=Decimal("0.01"),
                    ),
                )
            raise ValidationError(f"Product not found: {symbol}")
        return fetched
