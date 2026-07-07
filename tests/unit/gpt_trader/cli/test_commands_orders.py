"""CLI `orders` command tests: payload building, previews, editing, history.

Consolidated from test_commands_orders_editing.py and
test_commands_orders_history.py (dedupe cluster 65a…81, #1083); all three
shared the orders_command_test_helpers boundary doubles.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import pytest

import gpt_trader.cli.commands.orders as orders_cmd
from gpt_trader.cli.response import CliErrorCode, CliResponse
from gpt_trader.persistence.orders_store import OrderStatus
from tests.unit.gpt_trader.cli.orders_command_test_helpers import (
    make_args,
    make_history_args,
)
from tests.unit.gpt_trader.persistence.orders_store_test_helpers import create_test_order

# -- payload building and order preview --------------------------------------


def test_build_order_payload_includes_optional_fields():
    args = make_args()
    payload = orders_cmd._build_order_payload(args)

    assert payload["symbol"] == "BTC-PERP"
    assert payload["side"].name == "BUY"
    assert payload["order_type"].name == "LIMIT"
    assert str(payload["quantity"]) == "0.5"
    assert payload["tif"].name == "IOC"
    assert str(payload["price"]) == "42000"
    assert str(payload["stop_price"]) == "41000"
    assert payload["reduce_only"] is True
    assert payload["leverage"] == 3
    assert payload["client_id"] == "client-1"


def test_build_order_payload_omits_optional_fields_when_missing():
    args = make_args(
        type="market",
        price=None,
        stop=None,
        tif=None,
    )
    payload = orders_cmd._build_order_payload(args)

    assert "price" not in payload
    assert "stop_price" not in payload
    assert payload["tif"].name == "GTC"


def test_handle_preview_prints_json(monkeypatch, capsys):
    captured: dict[str, object] = {}

    def fake_build_config(args, *, skip):
        captured["skip"] = set(skip)
        return "config"

    class StubBroker:
        def preview_order(self, **payload):
            captured["payload"] = payload
            return {"preview": True}

        def edit_order_preview(self, order_id, **payload):
            raise AssertionError("Not expected in preview test")

        def edit_order(self, order_id, preview_id, **payload):
            raise AssertionError("Not expected in preview test")

    class StubBot:
        def __init__(self):
            self.broker = StubBroker()

        async def shutdown(self):
            captured["shutdown"] = True

    monkeypatch.setattr(orders_cmd.services, "build_config_from_args", fake_build_config)
    monkeypatch.setattr(orders_cmd.services, "instantiate_bot", lambda config: StubBot())

    exit_code = orders_cmd._handle_preview(make_args())

    assert exit_code == 0
    assert captured["payload"]["symbol"] == "BTC-PERP"
    assert "orders_command" in captured["skip"]
    assert captured["shutdown"] is True
    out = capsys.readouterr().out
    assert json.loads(out)["preview"] is True


def test_handle_preview_errors_without_preview_support(monkeypatch):
    class StubBot:
        broker = object()

        async def shutdown(self):
            StubBot.shutdown_called = True

    StubBot.shutdown_called = False

    monkeypatch.setattr(orders_cmd.services, "build_config_from_args", lambda *_, **__: "config")
    monkeypatch.setattr(orders_cmd.services, "instantiate_bot", lambda config: StubBot())

    with pytest.raises(RuntimeError):
        orders_cmd._handle_preview(make_args())

    assert StubBot.shutdown_called is False


# -- order editing (preview + apply) -----------------------------------------


def test_handle_edit_preview_errors_without_support(monkeypatch):
    class StubBot:
        broker = object()

        async def shutdown(self):
            StubBot.shutdown_called = True

    StubBot.shutdown_called = False

    monkeypatch.setattr(orders_cmd.services, "build_config_from_args", lambda *_, **__: "config")
    monkeypatch.setattr(orders_cmd.services, "instantiate_bot", lambda config: StubBot())

    with pytest.raises(RuntimeError):
        orders_cmd._handle_edit_preview(make_args())

    assert StubBot.shutdown_called is False


def test_handle_edit_preview_invokes_broker(monkeypatch, capsys):
    class StubBroker:
        def preview_order(self, **kwargs):
            raise AssertionError("Not expected in edit preview test")

        def edit_order_preview(self, order_id, **payload):
            StubBroker.order_id = order_id
            StubBroker.payload = payload
            return {"edit": True}

        def edit_order(self, order_id, preview_id, **payload):
            raise AssertionError("Not expected in edit preview test")

    class StubBot:
        def __init__(self):
            self.broker = StubBroker()

        async def shutdown(self):
            StubBot.shutdown_called = True

    StubBot.shutdown_called = False

    monkeypatch.setattr(orders_cmd.services, "build_config_from_args", lambda *_, **__: "config")
    monkeypatch.setattr(orders_cmd.services, "instantiate_bot", lambda config: StubBot())

    exit_code = orders_cmd._handle_edit_preview(make_args())

    assert exit_code == 0
    assert StubBot.shutdown_called is True
    assert StubBroker.order_id == "abc"
    assert StubBroker.payload["symbol"] == "BTC-PERP"
    out = capsys.readouterr().out
    assert json.loads(out)["edit"] is True


def test_handle_apply_edit_serializes_dataclass(monkeypatch, capsys):
    @dataclass
    class OrderResult:
        order_id: str
        status: str

    class StubBroker:
        def preview_order(self, **kwargs):
            raise AssertionError("Not expected in apply edit test")

        def edit_order_preview(self, order_id, **payload):
            raise AssertionError("Not expected in apply edit test")

        def edit_order(self, order_id, preview_id):
            return OrderResult(order_id=order_id, status=f"preview:{preview_id}")

    class StubBot:
        def __init__(self):
            self.broker = StubBroker()

        async def shutdown(self):
            StubBot.shutdown_called = True

    StubBot.shutdown_called = False

    monkeypatch.setattr(orders_cmd.services, "build_config_from_args", lambda *_, **__: "config")
    monkeypatch.setattr(orders_cmd.services, "instantiate_bot", lambda config: StubBot())

    exit_code = orders_cmd._handle_apply_edit(make_args())

    assert exit_code == 0
    assert StubBot.shutdown_called is True
    out_data = json.loads(capsys.readouterr().out)
    assert out_data == {"order_id": "abc", "status": "preview:def"}


def test_handle_apply_edit_errors_without_support(monkeypatch):
    class StubBot:
        broker = object()

        async def shutdown(self):
            StubBot.shutdown_called = True

    StubBot.shutdown_called = False

    monkeypatch.setattr(orders_cmd.services, "build_config_from_args", lambda *_, **__: "config")
    monkeypatch.setattr(orders_cmd.services, "instantiate_bot", lambda config: StubBot())

    with pytest.raises(RuntimeError):
        orders_cmd._handle_apply_edit(make_args())

    assert StubBot.shutdown_called is False


def test_handle_apply_edit_propagates_broker_errors(monkeypatch):
    class StubBroker:
        def preview_order(self, **kwargs):
            raise AssertionError("Not expected in failure test")

        def edit_order_preview(self, order_id, **payload):
            raise AssertionError("Not expected in failure test")

        def edit_order(self, order_id, preview_id):
            raise ValueError("broker failure")

    class StubBot:
        def __init__(self):
            self.broker = StubBroker()
            self.shutdown_called = False

        async def shutdown(self):
            self.shutdown_called = True

    bot = StubBot()

    monkeypatch.setattr(orders_cmd.services, "build_config_from_args", lambda *_, **__: "config")
    monkeypatch.setattr(orders_cmd.services, "instantiate_bot", lambda config: bot)

    with pytest.raises(ValueError):
        orders_cmd._handle_apply_edit(make_args())

    assert bot.shutdown_called is True


# -- order history -----------------------------------------------------------


class StubOrdersStore:
    def __init__(self, records: list, *, callback: Callable[..., None] | None = None) -> None:
        self.records = records
        self.callback = callback
        self.called_with: dict[str, object] | None = None

    def list_orders(
        self,
        *,
        limit: int,
        symbol: str | None,
        status: OrderStatus | None,
    ) -> list:
        self.called_with = {"limit": limit, "symbol": symbol, "status": status}
        if self.callback:
            self.callback(limit, symbol, status)
        return self.records


def _use_stub_store(monkeypatch, store: StubOrdersStore) -> None:
    monkeypatch.setattr(
        orders_cmd,
        "_with_orders_store",
        lambda args, callback: callback(store),
    )


def test_history_list_text_output(monkeypatch, capsys):
    record = create_test_order(order_id="text-order", status=OrderStatus.FILLED)
    store = StubOrdersStore([record])
    _use_stub_store(monkeypatch, store)

    args = make_history_args(limit=3, symbol="BTC-USD", status="filled", output_format="text")
    exit_code = orders_cmd._handle_history_list(args)

    assert exit_code == 0
    assert store.called_with == {
        "limit": 3,
        "symbol": "BTC-USD",
        "status": OrderStatus.FILLED,
    }

    output = capsys.readouterr().out
    assert "Order history" in output
    assert "text-order" in output
    assert "symbol=BTC-USD" in output
    assert "status=filled" in output


def test_history_list_json_response(monkeypatch):
    record = create_test_order(order_id="json-order", status=OrderStatus.OPEN)
    store = StubOrdersStore([record])
    _use_stub_store(monkeypatch, store)

    args = make_history_args(limit=2, output_format="json")
    response = orders_cmd._handle_history_list(args)

    assert isinstance(response, CliResponse)
    assert response.success
    assert response.data["count"] == 1
    assert response.data["orders"][0]["order_id"] == "json-order"
    assert response.data["filters"]["limit"] == 2
    assert store.called_with == {
        "limit": 2,
        "symbol": None,
        "status": None,
    }


def test_history_list_reports_empty(monkeypatch, capsys):
    store = StubOrdersStore([])
    _use_stub_store(monkeypatch, store)

    args = make_history_args()
    exit_code = orders_cmd._handle_history_list(args)

    assert exit_code == 0
    assert "No order history records found." in capsys.readouterr().out
    assert store.called_with == {
        "limit": orders_cmd._DEFAULT_HISTORY_LIMIT,
        "symbol": None,
        "status": None,
    }


def test_history_list_invalid_status_returns_error():
    args = make_history_args(status="unknown", output_format="json")
    response = orders_cmd._handle_history_list(args)

    assert isinstance(response, CliResponse)
    assert not response.success
    assert response.errors[0].code == CliErrorCode.INVALID_ARGUMENT.value
    assert response.errors[0].details == {"status": "unknown"}


def test_history_list_limit_validation():
    args = make_history_args(limit=0, output_format="json")
    response = orders_cmd._handle_history_list(args)

    assert isinstance(response, CliResponse)
    assert not response.success
    assert response.errors[0].code == CliErrorCode.INVALID_ARGUMENT.value
    assert response.errors[0].details == {"limit": 0}


def test_history_list_storage_error_returns_failure(monkeypatch):
    def raise_error(args, callback):
        raise RuntimeError("boom")

    monkeypatch.setattr(orders_cmd, "_with_orders_store", raise_error)
    response = orders_cmd._handle_history_list(make_history_args(output_format="json"))

    assert isinstance(response, CliResponse)
    assert not response.success
    assert response.errors[0].code == CliErrorCode.OPERATION_FAILED.value
    assert response.errors[0].details == {"error": "boom"}
