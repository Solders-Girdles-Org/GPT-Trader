"""
WebSocket event dispatcher for Coinbase.

Routes incoming WebSocket messages to appropriate handlers based on
event type. Provides a clean separation between message parsing,
routing, and domain logic.

Supported Event Types:
- Public channels: ticker, level2, market_trades, heartbeat, status
- Private channels: user, match, done (order events)

Usage:
    dispatcher = EventDispatcher()
    dispatcher.on_fill(handle_fill)
    dispatcher.on_ticker(handle_ticker)
    dispatcher.dispatch(message)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from gpt_trader.utilities.logging_patterns import get_logger

logger = get_logger(__name__, component="ws_events")


class EventType(str, Enum):
    """WebSocket event types from Coinbase."""

    # System events
    SUBSCRIPTIONS = "subscriptions"
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    STATUS = "status"

    # Public market data
    TICKER = "ticker"
    LEVEL2 = "l2_data"  # Level 2 orderbook
    MARKET_TRADES = "market_trades"
    CANDLES = "candles"

    # Private user events
    USER = "user"  # User order events
    MATCH = "match"  # Order fill match


@dataclass(frozen=True)
class TickerEvent:
    """Parsed ticker event."""

    product_id: str
    price: Decimal
    bid: Decimal | None
    ask: Decimal | None
    volume_24h: Decimal | None
    timestamp: datetime | None

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> TickerEvent:
        """Parse from WebSocket message."""
        events = data.get("events", [{}])
        tickers = events[0].get("tickers", [{}]) if events else [{}]
        ticker = tickers[0] if tickers else {}

        return cls(
            product_id=ticker.get("product_id", data.get("product_id", "")),
            price=Decimal(str(ticker.get("price", "0"))),
            bid=Decimal(str(ticker["best_bid"])) if ticker.get("best_bid") else None,
            ask=Decimal(str(ticker["best_ask"])) if ticker.get("best_ask") else None,
            volume_24h=Decimal(str(ticker["volume_24_h"])) if ticker.get("volume_24_h") else None,
            timestamp=(
                datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
                if data.get("timestamp")
                else None
            ),
        )


@dataclass(frozen=True)
class TradeEvent:
    """Parsed market trade event."""

    product_id: str
    trade_id: str
    price: Decimal
    size: Decimal
    side: str  # buy | sell
    timestamp: datetime | None

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> list[TradeEvent]:
        """Parse from WebSocket message - can have multiple trades."""
        events = data.get("events", [{}])
        trades_data = events[0].get("trades", []) if events else []

        trades = []
        for trade in trades_data:
            trades.append(
                cls(
                    product_id=trade.get("product_id", ""),
                    trade_id=trade.get("trade_id", ""),
                    price=Decimal(str(trade.get("price", "0"))),
                    size=Decimal(str(trade.get("size", "0"))),
                    side=trade.get("side", ""),
                    timestamp=(
                        datetime.fromisoformat(trade["time"].replace("Z", "+00:00"))
                        if trade.get("time")
                        else None
                    ),
                )
            )
        return trades


@dataclass(frozen=True)
class OrderbookUpdate:
    """Parsed level2 orderbook update."""

    product_id: str
    bids: list[tuple[Decimal, Decimal]]  # (price, size)
    asks: list[tuple[Decimal, Decimal]]  # (price, size)
    timestamp: datetime | None

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> OrderbookUpdate:
        """Parse from WebSocket message."""
        events = data.get("events", [{}])
        updates = events[0].get("updates", []) if events else []

        bids = []
        asks = []
        product_id = ""

        for update in updates:
            price = Decimal(str(update.get("price_level", "0")))
            size = Decimal(str(update.get("new_quantity", "0")))
            side = update.get("side", "")
            product_id = update.get("product_id", product_id)

            if side == "bid":
                bids.append((price, size))
            elif side == "offer":
                asks.append((price, size))

        return cls(
            product_id=product_id,
            bids=bids,
            asks=asks,
            timestamp=(
                datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
                if data.get("timestamp")
                else None
            ),
        )


@dataclass(frozen=True)
class FillEvent:
    """
    Parsed order fill event.

    Note: Private channel - requires authentication.
    This structure follows Coinbase Advanced Trade WebSocket format.
    """

    order_id: str
    client_order_id: str
    product_id: str
    side: str  # BUY | SELL
    fill_price: Decimal
    fill_size: Decimal
    fee: Decimal
    commission: Decimal
    sequence: int | None
    timestamp: datetime | None

    @classmethod
    def from_messages(cls, data: dict[str, Any]) -> list[FillEvent]:
        """Parse from WebSocket user event message."""
        fills = []
        events = data.get("events", [])
        for event in events:
            if event.get("type") == "snapshot" or event.get("type") == "update":
                orders = event.get("orders", [])
                for order in orders:
                    # Check if this is a fill event
                    if order.get("status") == "FILLED" or order.get("avg_price"):
                        fills.append(
                            cls(
                                order_id=order.get("order_id", ""),
                                client_order_id=order.get("client_order_id", ""),
                                product_id=order.get("product_id", ""),
                                side=order.get("order_side", ""),
                                fill_price=Decimal(str(order.get("avg_price", "0"))),
                                fill_size=Decimal(str(order.get("filled_size", "0"))),
                                fee=Decimal(str(order.get("fee", "0"))),
                                commission=Decimal(str(order.get("total_fees", "0"))),
                                sequence=data.get("sequence_num"),
                                timestamp=(
                                    datetime.fromisoformat(
                                        order["creation_time"].replace("Z", "+00:00")
                                    )
                                    if order.get("creation_time")
                                    else None
                                ),
                            )
                        )
        return fills

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> FillEvent | None:
        """Compatibility reader for callers explicitly asking for one observation."""
        fills = cls.from_messages(data)
        return fills[0] if fills else None


@dataclass(frozen=True)
class OrderUpdateEvent:
    """
    Parsed order status update event.

    Note: Private channel - requires authentication.
    """

    order_id: str
    client_order_id: str
    product_id: str
    status: str  # PENDING, OPEN, FILLED, CANCELLED, EXPIRED, FAILED
    side: str
    order_type: str
    size: Decimal
    filled_size: Decimal
    price: Decimal | None
    avg_price: Decimal | None
    timestamp: datetime | None

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> list[OrderUpdateEvent]:
        """Parse from WebSocket user event message."""
        events = data.get("events", [])
        updates = []

        for event in events:
            orders = event.get("orders", [])
            for order in orders:
                updates.append(
                    cls(
                        order_id=order.get("order_id", ""),
                        client_order_id=order.get("client_order_id", ""),
                        product_id=order.get("product_id", ""),
                        status=order.get("status", ""),
                        side=order.get("order_side", ""),
                        order_type=order.get("order_type", ""),
                        size=Decimal(str(order.get("order_configuration", {}).get("size", "0"))),
                        filled_size=Decimal(str(order.get("filled_size", "0"))),
                        price=(
                            Decimal(
                                str(order.get("order_configuration", {}).get("limit_price", "0"))
                            )
                            if order.get("order_configuration", {}).get("limit_price")
                            else None
                        ),
                        avg_price=(
                            Decimal(str(order.get("avg_price", "0")))
                            if order.get("avg_price")
                            else None
                        ),
                        timestamp=(
                            datetime.fromisoformat(order["creation_time"].replace("Z", "+00:00"))
                            if order.get("creation_time")
                            else None
                        ),
                    )
                )

        return updates


# Type aliases for event handlers
TickerHandler = Callable[[TickerEvent], None]
TradeHandler = Callable[[TradeEvent], None]
OrderbookHandler = Callable[[OrderbookUpdate], None]
FillHandler = Callable[[FillEvent], None]
OrderUpdateHandler = Callable[[OrderUpdateEvent], None]
RawHandler = Callable[[dict[str, Any]], None]


class EventDispatcher:
    """
    Dispatch WebSocket messages to registered handlers.

    Provides typed event routing with automatic message parsing.
    Handlers can be registered for specific event types or raw messages.
    """

    def __init__(self) -> None:
        self._ticker_handlers: list[TickerHandler] = []
        self._trade_handlers: list[TradeHandler] = []
        self._orderbook_handlers: list[OrderbookHandler] = []
        self._fill_handlers: list[FillHandler] = []
        self._order_update_handlers: list[OrderUpdateHandler] = []
        self._raw_handlers: list[RawHandler] = []
        self._error_handlers: list[RawHandler] = []

    def on_ticker(self, handler: TickerHandler) -> None:
        """Register handler for ticker events."""
        self._ticker_handlers.append(handler)

    def on_trade(self, handler: TradeHandler) -> None:
        """Register handler for market trade events."""
        self._trade_handlers.append(handler)

    def on_orderbook(self, handler: OrderbookHandler) -> None:
        """Register handler for orderbook update events."""
        self._orderbook_handlers.append(handler)

    def on_fill(self, handler: FillHandler) -> None:
        """Register handler for order fill events (private channel)."""
        self._fill_handlers.append(handler)

    def on_order_update(self, handler: OrderUpdateHandler) -> None:
        """Register handler for order status updates (private channel)."""
        self._order_update_handlers.append(handler)

    def on_raw(self, handler: RawHandler) -> None:
        """Register handler for all raw messages."""
        self._raw_handlers.append(handler)

    def on_error(self, handler: RawHandler) -> None:
        """Register handler for error messages."""
        self._error_handlers.append(handler)

    def dispatch(self, message: dict[str, Any]) -> None:
        """
        Dispatch a WebSocket message to appropriate handlers.

        Args:
            message: Raw WebSocket message dict
        """
        # Always call raw handlers
        for handler in self._raw_handlers:
            try:
                handler(message)
            except Exception as e:
                logger.error("Raw handler error", error=str(e))

        # Get channel/type from message
        channel = message.get("channel", "")
        msg_type = message.get("type", "")

        # Handle errors
        if msg_type == "error" or channel == "error":
            for handler in self._error_handlers:
                try:
                    handler(message)
                except Exception as e:
                    logger.error("Error handler error", error=str(e))
            return

        # Route based on channel
        try:
            if channel == "ticker":
                self._dispatch_ticker(message)
            elif channel == "market_trades":
                self._dispatch_trades(message)
            elif channel == "l2_data":
                self._dispatch_orderbook(message)
            elif channel == "user":
                self._dispatch_user_events(message)
        except Exception as e:
            logger.error(
                "Event dispatch error",
                channel=channel,
                error=str(e),
            )

    def _dispatch_ticker(self, message: dict[str, Any]) -> None:
        """Dispatch ticker event to handlers."""
        if not self._ticker_handlers:
            return

        event = TickerEvent.from_message(message)
        for handler in self._ticker_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error("Ticker handler error", error=str(e))

    def _dispatch_trades(self, message: dict[str, Any]) -> None:
        """Dispatch trade events to handlers."""
        if not self._trade_handlers:
            return

        events = TradeEvent.from_message(message)
        for event in events:
            for handler in self._trade_handlers:
                try:
                    handler(event)
                except Exception as e:
                    logger.error("Trade handler error", error=str(e))

    def _dispatch_orderbook(self, message: dict[str, Any]) -> None:
        """Dispatch orderbook update to handlers."""
        if not self._orderbook_handlers:
            return

        event = OrderbookUpdate.from_message(message)
        for handler in self._orderbook_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error("Orderbook handler error", error=str(e))

    def _dispatch_user_events(self, message: dict[str, Any]) -> None:
        """Dispatch user/order events to handlers."""
        # Check for fills
        if self._fill_handlers:
            fill = FillEvent.from_message(message)
            if fill:
                for fill_handler in self._fill_handlers:
                    try:
                        fill_handler(fill)
                    except Exception as e:
                        logger.error("Fill handler error", error=str(e))

        # Check for order updates
        if self._order_update_handlers:
            updates = OrderUpdateEvent.from_message(message)
            for update in updates:
                for update_handler in self._order_update_handlers:
                    try:
                        update_handler(update)
                    except Exception as e:
                        logger.error("Order update handler error", error=str(e))


__all__ = [
    "EventDispatcher",
    "EventType",
    "FillEvent",
    "OrderUpdateEvent",
    "OrderbookUpdate",
    "TickerEvent",
    "TradeEvent",
]
