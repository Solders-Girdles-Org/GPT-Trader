from __future__ import annotations

from datetime import UTC, datetime

import pytest


# Deterministic Message Generators
@pytest.fixture
def ticker_message_factory():
    """Factory for creating ticker messages with deterministic content."""

    def create_ticker(
        symbol: str = "BTC-USD",
        price: str = "50000.00",
        bid: str = "49900.00",
        ask: str = "50100.00",
    ):
        return {
            "type": "ticker",
            "product_id": symbol,
            "price": price,
            "best_bid": bid,
            "best_ask": ask,
            "timestamp": datetime.now(UTC).isoformat(),
            "sequence": 12345,
        }

    return create_ticker


@pytest.fixture
def trade_message_factory():
    """Factory for creating trade/match messages."""

    def create_trade(
        symbol: str = "BTC-USD", price: str = "50050.00", size: str = "0.1", side: str = "buy"
    ):
        return {
            "type": "match",
            "product_id": symbol,
            "price": price,
            "size": size,
            "side": side,
            "timestamp": datetime.now(UTC).isoformat(),
            "trade_id": "123456",
            "sequence": 12346,
        }

    return create_trade


@pytest.fixture
def orderbook_message_factory():
    """Factory for creating orderbook update messages."""

    def create_orderbook(symbol: str = "BTC-USD", changes: list | None = None):
        if changes is None:
            changes = [
                ["buy", "49950.00", "0.5"],
                ["sell", "50100.00", "0.3"],
            ]
        return {
            "type": "l2update",
            "product_id": symbol,
            "changes": changes,
            "timestamp": datetime.now(UTC).isoformat(),
            "sequence": 12347,
        }

    return create_orderbook


@pytest.fixture
def heartbeat_message_factory():
    """Factory for creating heartbeat/status messages."""

    def create_heartbeat(status: str = "ok", message: str = "healthy"):
        return {
            "type": "heartbeat",
            "status": status,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    return create_heartbeat


# Error Scenario Helpers
@pytest.fixture
def message_parsing_error_scenarios():
    """Dictionary of message parsing error scenarios."""
    return {
        "invalid_json": '{"invalid": json structure}',
        "missing_type": {"price": "50000.00", "product_id": "BTC-USD"},
        "invalid_price": {"type": "ticker", "product_id": "BTC-USD", "price": "invalid_price"},
        "missing_symbol": {"type": "ticker", "price": "50000.00"},
        "null_message": None,
        "empty_dict": {},
        "wrong_type": 12345,
    }


# Subscription Management Helpers
@pytest.fixture
def subscription_factory():
    """Factory for creating subscription messages."""

    def create_subscription(channels: list[str], product_ids: list[str]):
        return {
            "type": "subscribe",
            "channels": channels,
            "product_ids": product_ids,
        }

    return create_subscription


@pytest.fixture
def sample_subscriptions():
    """Sample subscription configurations."""
    return {
        "ticker_only": ["ticker"],
        "full_market": ["ticker", "matches", "level2"],
        "user_events": ["user"],
        "minimal": ["heartbeat"],
    }
