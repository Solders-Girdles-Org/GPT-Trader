"""Tests for ReadOnlyBroker, the dry-run safety wrapper.

ReadOnlyBroker is the only thing standing between dry_run=True and real
order submission, and the factory wraps *any* broker in it — including
DeterministicBroker, an interaction that silently rotted the integration
suite for five months (see PR #1097). These tests pin down the contract:
writes are suppressed and recorded as order_suppressed events, reads pass
through untouched, and the factory wrapping rule holds.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

import gpt_trader.features.brokerages.factory as brokerage_factory
from gpt_trader.app.config import BotConfig
from gpt_trader.core import OrderSide, OrderStatus, OrderType
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.brokerages.read_only import ReadOnlyBroker, ReadOnlyViolation


class RecordingEventStore:
    """Minimal EventStoreProtocol stand-in that records append() calls."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def append(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, data))


class DescriptorBroker:
    def __init__(self) -> None:
        self.client_reads = 0

    @property
    def client(self) -> object:
        self.client_reads += 1
        return object()


class RaisingEventStore:
    """Event store whose append() always fails."""

    def append(self, event_type: str, data: dict[str, Any]) -> None:
        raise RuntimeError("event store unavailable")


class StubBroker:
    """Records read calls so passthrough can be asserted; writes must never run."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        self.calls.append(("get_ticker", (product_id,)))
        return {"product_id": product_id, "price": "50000"}

    def list_products(self) -> list[str]:
        self.calls.append(("list_products", ()))
        return ["BTC-USD", "ETH-USD"]

    def preview_order(self, symbol: str) -> dict[str, str]:
        self.calls.append(("preview_order", (symbol,)))
        return {"symbol": symbol, "status": "previewed"}

    def edit_order_preview(self, symbol: str) -> dict[str, str]:
        self.calls.append(("edit_order_preview", (symbol,)))
        return {"symbol": symbol, "status": "previewed"}

    def get_resilience_status(self) -> dict[str, str]:
        self.calls.append(("get_resilience_status", ()))
        return {"status": "healthy"}

    def get_ws_health(self) -> dict[str, str]:
        self.calls.append(("get_ws_health", ()))
        return {"status": "healthy"}

    def stream_orderbook(self, symbol: str) -> str:
        self.calls.append(("stream_orderbook", (symbol,)))
        return "orderbook-stream"

    def stream_trades(self, symbol: str) -> str:
        self.calls.append(("stream_trades", (symbol,)))
        return "trade-stream"

    def set_mark(self, symbol: str, price: Decimal) -> None:
        raise AssertionError("set_mark is local-only for DeterministicBroker")

    def move_funds(self, amount: Decimal) -> None:
        raise AssertionError("move_funds must never reach the wrapped broker")

    def future_mutation(self) -> None:
        raise AssertionError("future_mutation must never reach the wrapped broker")

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("place_order must never reach the wrapped broker")

    def cancel_order(self, order_id: str) -> bool:
        raise AssertionError("cancel_order must never reach the wrapped broker")


def _make_wrapper(
    event_store: Any | None = None,
    broker: Any | None = None,
    bot_id: str | None = "test-bot",
) -> ReadOnlyBroker:
    return ReadOnlyBroker(
        broker=broker if broker is not None else StubBroker(),
        event_store=event_store,
        bot_id=bot_id,
    )


class TestPlaceOrderSuppression:
    def test_returns_rejected_order_with_dry_run_metadata(self) -> None:
        wrapper = _make_wrapper(event_store=RecordingEventStore())

        order = wrapper.place_order(
            "BTC-USD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.5"),
            price=Decimal("50000"),
        )

        assert order.status == OrderStatus.REJECTED
        assert order.dry_run_suppressed is True
        assert order.suppressed_reason == "dry_run"
        assert order.symbol == "BTC-USD"
        assert order.side == OrderSide.BUY
        assert order.type == OrderType.LIMIT
        assert order.quantity == Decimal("0.5")
        assert order.filled_quantity == Decimal("0")

    def test_records_order_suppressed_event(self) -> None:
        event_store = RecordingEventStore()
        wrapper = _make_wrapper(event_store=event_store)

        wrapper.place_order(
            "BTC-USD",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("1.25"),
        )

        assert len(event_store.events) == 1
        event_type, data = event_store.events[0]
        assert event_type == "order_suppressed"
        assert data["action"] == "place_order"
        assert data["reason"] == "dry_run"
        assert data["dry_run"] is True
        assert data["bot_id"] == "test-bot"
        assert data["symbol"] == "BTC-USD"
        assert data["side"] == "SELL"  # enums serialized to their values
        assert data["quantity"] == "1.25"  # Decimals serialized to strings

    def test_never_calls_wrapped_broker(self) -> None:
        broker = StubBroker()
        wrapper = _make_wrapper(event_store=RecordingEventStore(), broker=broker)

        wrapper.place_order("BTC-USD", side="buy", quantity=Decimal("1"))

        assert broker.calls == []

    def test_dict_payload_extracts_symbol_and_side(self) -> None:
        event_store = RecordingEventStore()
        wrapper = _make_wrapper(event_store=event_store)

        order = wrapper.place_order(
            {"product_id": "ETH-USD", "side": "sell", "quantity": Decimal("2")}
        )

        assert order.status == OrderStatus.REJECTED
        assert order.symbol == "ETH-USD"
        assert order.side == OrderSide.SELL
        _, data = event_store.events[0]
        assert data["symbol"] == "ETH-USD"
        assert data["request"] == {"product_id": "ETH-USD", "side": "sell", "quantity": "2"}

    def test_client_id_flows_into_dry_run_order_id(self) -> None:
        wrapper = _make_wrapper(event_store=RecordingEventStore())

        order = wrapper.place_order(
            "BTC-USD", side="buy", quantity=Decimal("1"), client_id="abc-123"
        )

        assert order.id == "DRYRUN_abc-123"
        assert order.client_id == "abc-123"

    def test_suppression_survives_missing_event_store(self) -> None:
        wrapper = _make_wrapper(event_store=None)

        order = wrapper.place_order("BTC-USD", side="buy", quantity=Decimal("1"))

        assert order.status == OrderStatus.REJECTED

    def test_suppression_survives_event_store_failure(self) -> None:
        wrapper = _make_wrapper(event_store=RaisingEventStore())

        order = wrapper.place_order("BTC-USD", side="buy", quantity=Decimal("1"))

        assert order.status == OrderStatus.REJECTED
        assert order.dry_run_suppressed is True


class TestReadPathPassthrough:
    def test_get_ticker_delegates_to_wrapped_broker(self) -> None:
        broker = StubBroker()
        wrapper = _make_wrapper(event_store=RecordingEventStore(), broker=broker)

        ticker = wrapper.get_ticker("BTC-USD")

        assert ticker == {"product_id": "BTC-USD", "price": "50000"}
        assert broker.calls == [("get_ticker", ("BTC-USD",))]

    def test_list_products_delegates_through_explicit_read_allowlist(self) -> None:
        broker = StubBroker()
        wrapper = _make_wrapper(event_store=RecordingEventStore(), broker=broker)

        products = wrapper.list_products()

        assert products == ["BTC-USD", "ETH-USD"]
        assert broker.calls == [("list_products", ())]

    @pytest.mark.parametrize("method_name", ["preview_order", "edit_order_preview"])
    def test_provider_previews_stay_outside_read_only_broker(self, method_name: str) -> None:
        broker = StubBroker()
        wrapper = _make_wrapper(broker=broker)

        with pytest.raises(ReadOnlyViolation, match=method_name):
            getattr(wrapper, method_name)("BTC-USD")

        assert broker.calls == []

    @pytest.mark.parametrize(
        ("method_name", "args"),
        [
            ("get_resilience_status", ()),
            ("get_ws_health", ()),
            ("stream_orderbook", ("BTC-USD",)),
            ("stream_trades", ("BTC-USD",)),
        ],
    )
    def test_existing_health_and_stream_reads_remain_available(
        self,
        method_name: str,
        args: tuple[Any, ...],
    ) -> None:
        broker = StubBroker()
        wrapper = _make_wrapper(broker=broker)

        assert hasattr(wrapper, method_name) is True
        getattr(wrapper, method_name)(*args)

        assert broker.calls == [(method_name, args)]

    def test_deterministic_broker_can_receive_local_mark(self) -> None:
        broker = DeterministicBroker()
        wrapper = _make_wrapper(broker=broker)

        wrapper.set_mark("BTC-USD", Decimal("61000"))

        assert wrapper.get_ticker("BTC-USD")["price"] == "61000"

    def test_reads_record_no_suppression_events(self) -> None:
        event_store = RecordingEventStore()
        wrapper = _make_wrapper(event_store=event_store)

        wrapper.get_ticker("BTC-USD")
        wrapper.list_products()

        assert event_store.events == []


class TestFailClosedDelegation:
    @pytest.mark.parametrize("method_name", ["move_funds", "future_mutation"])
    def test_unknown_callable_is_blocked_without_touching_broker(self, method_name: str) -> None:
        event_store = RecordingEventStore()
        wrapper = _make_wrapper(event_store=event_store)

        with pytest.raises(ReadOnlyViolation, match=method_name):
            getattr(wrapper, method_name)()

        assert event_store.events == []

    def test_hasattr_treats_blocked_capability_as_unavailable(self) -> None:
        event_store = RecordingEventStore()
        wrapper = _make_wrapper(event_store=event_store)

        assert hasattr(wrapper, "future_mutation") is False
        assert event_store.events == []

    def test_blocked_descriptor_is_not_evaluated(self) -> None:
        broker = DescriptorBroker()
        wrapper = _make_wrapper(broker=broker)

        with pytest.raises(ReadOnlyViolation, match="client"):
            _ = wrapper.client

        assert broker.client_reads == 0

    def test_raw_client_escape_hatch_is_blocked(self) -> None:
        broker = StubBroker()
        broker.client = object()
        wrapper = _make_wrapper(broker=broker)

        with pytest.raises(ReadOnlyViolation, match="client"):
            _ = wrapper.client

    def test_remote_shaped_broker_cannot_receive_local_mark(self) -> None:
        wrapper = _make_wrapper()

        with pytest.raises(ReadOnlyViolation, match="set_mark"):
            wrapper.set_mark("BTC-USD", Decimal("61000"))


class TestCancelOrderSuppression:
    def test_cancel_reports_not_cancelled_without_touching_broker(self) -> None:
        broker = StubBroker()
        wrapper = _make_wrapper(event_store=RecordingEventStore(), broker=broker)

        assert wrapper.cancel_order("order-1") is False
        assert broker.calls == []

    def test_cancel_records_order_suppressed_event(self) -> None:
        event_store = RecordingEventStore()
        wrapper = _make_wrapper(event_store=event_store)

        wrapper.cancel_order("order-1")

        assert len(event_store.events) == 1
        event_type, data = event_store.events[0]
        assert event_type == "order_suppressed"
        assert data["action"] == "cancel_order"
        assert data["order_id"] == "order-1"
        assert data["dry_run"] is True


class TestFactoryWrappingRule:
    def _make_config(self, *, dry_run: bool) -> BotConfig:
        config = BotConfig()
        config.mock_broker = True
        config.paper_fills = False
        config.dry_run = dry_run
        config.profile = "dev"
        return config

    def test_dry_run_wraps_even_deterministic_broker(self) -> None:
        # This exact interaction (mock_broker=True + dry_run=True) silently
        # rejected every order in the integration suite for five months; the
        # wrapping rule is load-bearing and must stay pinned. See PR #1097.
        event_store = RecordingEventStore()

        broker, *_ = brokerage_factory.create_brokerage(
            event_store=event_store,
            market_data=MagicMock(),
            product_catalog=MagicMock(),
            config=self._make_config(dry_run=True),
        )

        assert isinstance(broker, ReadOnlyBroker)

        order = broker.place_order("BTC-USD", side="buy", quantity=Decimal("1"))
        assert order.status == OrderStatus.REJECTED
        assert order.dry_run_suppressed is True

        assert len(event_store.events) == 1
        event_type, data = event_store.events[0]
        assert event_type == "order_suppressed"
        assert data["bot_id"] == "dev"  # bot_id wired from config.profile

    def test_without_dry_run_mock_broker_is_not_wrapped(self) -> None:
        broker, *_ = brokerage_factory.create_brokerage(
            event_store=RecordingEventStore(),
            market_data=MagicMock(),
            product_catalog=MagicMock(),
            config=self._make_config(dry_run=False),
        )

        assert isinstance(broker, DeterministicBroker)
        assert not isinstance(broker, ReadOnlyBroker)

        order = broker.place_order("BTC-USD", side="buy", quantity=Decimal("1"))
        assert order.status == OrderStatus.FILLED
