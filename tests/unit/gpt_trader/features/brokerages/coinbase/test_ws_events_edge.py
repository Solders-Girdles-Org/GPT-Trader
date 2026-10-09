"""Edge coverage for Coinbase WebSocket event parsing."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gpt_trader.features.brokerages.coinbase.ws_events import (
    FillEvent,
    OrderbookUpdate,
    OrderUpdateEvent,
    TickerEvent,
    TradeEvent,
)


@pytest.mark.parametrize(
    "message",
    [
        {"product_id": "BTC-USD"},
        {"product_id": "BTC-USD", "events": []},
    ],
)
def test_ticker_event_defaults_when_events_missing(message: dict) -> None:
    event = TickerEvent.from_message(message)

    assert event.product_id == "BTC-USD"
    assert event.price == Decimal("0")
    assert event.bid is None
    assert event.ask is None
    assert event.volume_24h is None
    assert event.timestamp is None


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"events": []},
    ],
)
def test_trade_event_missing_events_returns_empty(message: dict) -> None:
    events = TradeEvent.from_message(message)

    assert events == []


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"events": []},
        {"events": [{}]},
    ],
)
def test_orderbook_update_missing_updates_returns_empty(message: dict) -> None:
    update = OrderbookUpdate.from_message(message)

    assert update.product_id == ""
    assert update.bids == []
    assert update.asks == []


def test_fill_event_none_without_filled_or_avg_price() -> None:
    message = {
        "events": [
            {
                "type": "update",
                "orders": [
                    {
                        "order_id": "o1",
                        "status": "OPEN",
                    }
                ],
            }
        ]
    }

    event = FillEvent.from_message(message)

    assert event is None


def test_fill_event_created_from_avg_price_even_without_filled() -> None:
    message = {
        "sequence_num": 42,
        "events": [
            {
                "type": "update",
                "orders": [
                    {
                        "order_id": "o1",
                        "client_order_id": "c1",
                        "product_id": "BTC-USD",
                        "order_side": "BUY",
                        "status": "OPEN",
                        "avg_price": "50000",
                        "filled_size": "0.1",
                        "fee": "1.0",
                        "total_fees": "1.2",
                        "creation_time": "2024-01-01T00:00:00Z",
                    }
                ],
            }
        ],
    }

    event = FillEvent.from_message(message)

    assert event is not None
    assert event.sequence == 42
    assert event.fill_price == Decimal("50000")
    assert event.timestamp == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_order_update_defaults_without_order_configuration() -> None:
    message = {
        "events": [
            {
                "type": "update",
                "orders": [
                    {
                        "order_id": "o1",
                        "client_order_id": "c1",
                        "product_id": "ETH-USD",
                        "status": "OPEN",
                        "order_side": "SELL",
                        "order_type": "LIMIT",
                    }
                ],
            }
        ]
    }

    events = OrderUpdateEvent.from_message(message)

    assert len(events) == 1
    assert events[0].size == Decimal("0")
    assert events[0].filled_size == Decimal("0")
    assert events[0].price is None
