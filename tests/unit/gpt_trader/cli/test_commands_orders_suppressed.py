"""CLI `orders suppressed` tests: dry-run suppression telemetry consumer (#1109).

Split from test_commands_orders.py to stay under the 400-line test-hygiene cap;
shares the orders_command_test_helpers boundary doubles.
"""

from __future__ import annotations

import gpt_trader.cli.commands.orders as orders_cmd
from gpt_trader.cli.response import CliErrorCode, CliResponse
from gpt_trader.features.brokerages.read_only import ReadOnlyBroker
from gpt_trader.persistence.event_store import EventStore
from tests.unit.gpt_trader.cli.orders_command_test_helpers import make_suppressed_args


def _use_stub_event_store(monkeypatch, store: EventStore) -> None:
    monkeypatch.setattr(
        orders_cmd,
        "_with_event_store",
        lambda args, callback: callback(store),
    )


def test_suppressed_json_summarizes_read_only_broker_events(monkeypatch):
    store = EventStore()
    broker = ReadOnlyBroker(object(), store, bot_id="bot-1", reason="dry_run")
    broker.place_order(symbol="BTC-USD", side="buy", order_type="market", quantity="0.5")
    broker.cancel_order("order-9")
    _use_stub_event_store(monkeypatch, store)

    response = orders_cmd._handle_suppressed(make_suppressed_args(output_format="json"))

    assert isinstance(response, CliResponse)
    assert response.success
    assert response.data["count"] == 2
    assert response.data["counts_by_action"] == {"place_order": 1, "cancel_order": 1}
    assert response.data["filters"]["limit"] == orders_cmd._DEFAULT_HISTORY_LIMIT
    newest, oldest = response.data["events"]
    assert newest["action"] == "cancel_order"
    assert newest["order_id"] == "order-9"
    assert oldest["action"] == "place_order"
    assert oldest["symbol"] == "BTC-USD"
    assert oldest["side"] == "buy"
    assert oldest["quantity"] == "0.5"
    assert oldest["reason"] == "dry_run"
    assert oldest["bot_id"] == "bot-1"
    assert oldest["recorded_at"] is not None


def test_suppressed_text_output(monkeypatch, capsys):
    store = EventStore()
    store.append(
        "order_suppressed",
        {
            "action": "close_position",
            "reason": "dry_run",
            "timestamp": 1_700_000_000.0,
            "symbol": "ETH-USD",
        },
    )
    _use_stub_event_store(monkeypatch, store)

    exit_code = orders_cmd._handle_suppressed(make_suppressed_args(limit=5))

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Suppressed order writes (limit=5)" in output
    assert "Counts by action: close_position=1" in output
    assert "close_position (reason=dry_run) symbol=ETH-USD" in output


def test_suppressed_reports_empty(monkeypatch, capsys):
    _use_stub_event_store(monkeypatch, EventStore())

    exit_code = orders_cmd._handle_suppressed(make_suppressed_args())

    assert exit_code == 0
    assert "No suppressed order writes recorded." in capsys.readouterr().out


def test_suppressed_tolerates_malformed_event(monkeypatch):
    store = EventStore()
    store.append("order_suppressed", {"unexpected": True})
    _use_stub_event_store(monkeypatch, store)

    response = orders_cmd._handle_suppressed(make_suppressed_args(output_format="json"))

    assert isinstance(response, CliResponse)
    assert response.success
    assert response.data["counts_by_action"] == {"unknown": 1}
    assert response.data["events"][0]["recorded_at"] is None


def test_suppressed_limit_validation():
    response = orders_cmd._handle_suppressed(make_suppressed_args(limit=0, output_format="json"))

    assert isinstance(response, CliResponse)
    assert not response.success
    assert response.errors[0].code == CliErrorCode.INVALID_ARGUMENT.value
    assert response.errors[0].details == {"limit": 0}


def test_suppressed_storage_error_returns_failure(monkeypatch):
    def raise_error(args, callback):
        raise RuntimeError("boom")

    monkeypatch.setattr(orders_cmd, "_with_event_store", raise_error)
    response = orders_cmd._handle_suppressed(make_suppressed_args(output_format="json"))

    assert isinstance(response, CliResponse)
    assert not response.success
    assert response.errors[0].code == CliErrorCode.OPERATION_FAILED.value
    assert response.errors[0].details == {"error": "boom"}
