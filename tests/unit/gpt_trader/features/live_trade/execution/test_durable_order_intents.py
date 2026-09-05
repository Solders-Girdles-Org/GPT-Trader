"""Durable direct BUY/SELL/CLOSE request identity at the real broker boundary."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import Mock

import pytest

from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.mock import DeterministicBroker
from gpt_trader.features.live_trade.engines.position_formatting import resolve_close_order
from gpt_trader.features.live_trade.execution.order_submission import OrderSubmitter
from gpt_trader.persistence.durability import WriteResult
from gpt_trader.persistence.orders_store import OrdersStore, OrderStatus


def submitter(path, broker, **kwargs):
    return OrderSubmitter(
        broker,
        Mock(),
        "test",
        [],
        orders_store=OrdersStore(path),
        enable_retries=True,
        sleep_fn=lambda _: None,
        **kwargs,
    )


def request(worker, **kwargs):
    return worker.submit_order_with_result(
        **{
            "symbol": "BTC-USD",
            "side": OrderSide.SELL,
            "order_type": OrderType.MARKET,
            "order_quantity": Decimal("0.125"),
            "price": None,
            "effective_price": Decimal("60000"),
            "stop_price": None,
            "tif": None,
            "reduce_only": True,
            "leverage": None,
            "client_order_id": "stable-close",
            **kwargs,
        }
    )


@pytest.mark.parametrize(
    "change",
    [
        {"order_quantity": Decimal("0.25")},
        {"side": OrderSide.BUY},
        {"reduce_only": False},
        {"symbol": "ETH-USD"},
        {"price": Decimal("61000")},
        {"stop_price": Decimal("59000")},
        {"tif": "IOC"},
        {"leverage": 2},
        {"order_type": OrderType.LIMIT},
    ],
)
def test_restart_rejects_changed_payload_before_broker(tmp_path, change):
    broker = DeterministicBroker()
    broker.place_order = Mock(wraps=broker.place_order)
    original = request(submitter(tmp_path, broker))
    assert original.success
    restarted = submitter(tmp_path, broker)
    assert request(restarted, order_quantity=Decimal("0.1250")).success
    conflict = request(restarted, **change)
    assert conflict.rejected and conflict.reason_detail == "client_order_id payload conflict"
    assert broker.place_order.call_count == 1
    assert (
        restarted.orders_store.get_order_by_client_order_id("stable-close").metadata["intent"][
            "reduce_only"
        ]
        is True
    )


@pytest.mark.parametrize("stage", ["lookup", "write", "write_result", "initialize"])
def test_configured_storage_failure_never_submits(tmp_path, monkeypatch, stage):
    broker = DeterministicBroker()
    broker.place_order = Mock(wraps=broker.place_order)
    if stage == "initialize":
        monkeypatch.setattr(OrdersStore, "initialize", Mock(side_effect=OSError("storage down")))
    worker = submitter(tmp_path, broker)
    if stage == "lookup":
        monkeypatch.setattr(
            worker.orders_store,
            "get_order_by_client_order_id",
            Mock(side_effect=OSError("storage down")),
        )
    elif stage == "write":
        monkeypatch.setattr(
            worker.orders_store, "save_order", Mock(side_effect=OSError("storage down"))
        )
    elif stage == "write_result":
        # Production save_order raises on an SQLite error; a failed result must
        # also never be taken as successful reservation by an alternate writer.
        monkeypatch.setattr(
            worker.orders_store, "save_order", Mock(return_value=WriteResult.fail("storage down"))
        )
    result = request(worker)
    assert result.failed and result.reason == "submission_uncertain"
    broker.place_order.assert_not_called()


def test_broker_success_then_final_persistence_failure_remains_pending_on_restart(
    tmp_path, monkeypatch
):
    broker = DeterministicBroker()
    broker.place_order = Mock(wraps=broker.place_order)
    worker = submitter(tmp_path, broker)
    monkeypatch.setattr(
        worker.orders_store,
        "upsert_by_client_id",
        Mock(side_effect=OSError("disk lost after broker")),
    )
    result = request(worker)
    assert result.failed and result.reason == "submission_uncertain"
    restarted = submitter(tmp_path, broker)
    record = restarted.orders_store.get_order_by_client_order_id("stable-close")
    assert record.status is OrderStatus.PENDING
    assert record.metadata["intent"]["side"] == "sell"
    retry = request(restarted)
    assert retry.failed and retry.reason == "submission_uncertain"
    assert broker.place_order.call_count == 1


def test_concurrent_stores_reserve_one_broker_submission(tmp_path):
    broker = DeterministicBroker()
    broker.place_order = Mock(wraps=broker.place_order)
    workers = [submitter(tmp_path, broker), submitter(tmp_path, broker)]
    barrier = Barrier(2)

    def run(worker):
        barrier.wait()
        return request(worker)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, workers))
    assert any(result.success for result in results)
    assert broker.place_order.call_count == 1


@pytest.mark.parametrize(
    "position, expected_side",
    [
        ({"quantity": "0.125", "side": "long"}, OrderSide.SELL),
        ({"quantity": "-0.125", "side": "short"}, OrderSide.BUY),
    ],
)
def test_close_intent_preserves_real_close_resolver_side_quantity_and_reduce_only(
    tmp_path, position, expected_side
):
    side, quantity = resolve_close_order(position)
    broker = DeterministicBroker()
    broker.place_order = Mock(wraps=broker.place_order)
    worker = submitter(tmp_path, broker)
    assert request(worker, side=side, order_quantity=quantity).success
    sent = broker.place_order.call_args.kwargs
    assert sent["side"] is expected_side and sent["quantity"] == Decimal("0.125")
    assert sent["reduce_only"] is True
    intent = worker.orders_store.get_order_by_client_order_id("stable-close").metadata["intent"]
    assert intent["side"] == expected_side.value.lower() and intent["quantity"] == "0.125"


def test_storeless_compatibility_does_not_claim_durable_idempotency():
    broker = DeterministicBroker()
    broker.place_order = Mock(wraps=broker.place_order)
    worker = OrderSubmitter(broker, Mock(), "test", [])
    assert worker.orders_store is None
    assert request(worker).success
    assert request(worker).success
    assert broker.place_order.call_count == 2


def test_new_intent_after_mock_restart_preserves_both_order_receipts(tmp_path):
    first = request(submitter(tmp_path, DeterministicBroker()))
    restarted = submitter(tmp_path, DeterministicBroker())
    second = request(restarted, client_order_id="second-close")
    assert first.success and second.success and first.order_id != second.order_id
    assert (
        restarted.orders_store.get_order_by_client_order_id("stable-close").status
        is OrderStatus.FILLED
    )
    assert (
        restarted.orders_store.get_order_by_client_order_id("second-close").status
        is OrderStatus.FILLED
    )


@pytest.mark.parametrize("corruption", ["duplicate_client", "checksum"])
def test_cached_retry_refuses_ambiguous_or_corrupt_stored_order(tmp_path, corruption):
    import sqlite3

    broker = DeterministicBroker()
    broker.place_order = Mock(wraps=broker.place_order)
    worker = submitter(tmp_path, broker)
    assert request(worker).success
    if corruption == "duplicate_client":
        from dataclasses import replace

        record = worker.orders_store.get_order_by_client_order_id("stable-close")
        worker.orders_store.save_order(
            replace(record, order_id="foreign-order"), raise_on_error=True
        )
    else:
        with sqlite3.connect(tmp_path / "orders.db") as connection:
            connection.execute("UPDATE orders SET checksum = 'corrupt'")
    result = request(submitter(tmp_path, broker))
    assert result.failed and result.reason == "submission_uncertain"
    assert broker.place_order.call_count == 1


@pytest.mark.parametrize(
    "field, value",
    [
        ("side", OrderSide.BUY),
        ("client_id", "foreign-client"),
        ("symbol", "ETH-USD"),
        ("quantity", Decimal("1")),
    ],
)
def test_conflicting_broker_receipt_stays_uncertain_without_retry(tmp_path, field, value):
    from dataclasses import replace

    broker = DeterministicBroker()
    original = broker.place_order

    def conflict(**kwargs):
        return replace(original(**kwargs), **{field: value})

    broker.place_order = Mock(side_effect=conflict)
    worker = submitter(tmp_path, broker)
    outcome = request(worker)
    assert outcome.failed and outcome.reason == "submission_uncertain"
    assert "conflicts with intent" in outcome.reason_detail
    assert (
        worker.orders_store.get_order_by_client_order_id("stable-close").status
        is OrderStatus.PENDING
    )
    assert not request(submitter(tmp_path, broker)).success
    assert broker.place_order.call_count == 1


@pytest.mark.parametrize(
    "field, value",
    [
        ("filled_quantity", Decimal("0")),
        ("filled_quantity", Decimal("0.01")),
        ("filled_quantity", "not-a-number"),
        ("avg_fill_price", Decimal("NaN")),
        ("avg_fill_price", Decimal("0")),
    ],
)
def test_invalid_terminal_fill_facts_never_become_cached_success(tmp_path, field, value):
    from dataclasses import replace

    broker = DeterministicBroker()
    original = broker.place_order

    def conflict(**kwargs):
        return replace(original(**kwargs), **{field: value})

    broker.place_order = Mock(side_effect=conflict)
    worker = submitter(tmp_path, broker)
    assert request(worker).reason == "submission_uncertain"
    assert request(submitter(tmp_path, broker)).reason == "submission_uncertain"
    assert broker.place_order.call_count == 1
    assert (
        worker.orders_store.get_order_by_client_order_id("stable-close").status
        is OrderStatus.PENDING
    )


@pytest.mark.parametrize(
    "field, value",
    [
        ("status", "filled"),
        ("client_order_id", "changed"),
        ("filled_quantity", "0.125"),
        ("average_fill_price", "123"),
        ("metadata", "{}"),
    ],
)
def test_new_record_checksum_binds_submission_and_fill_semantics(tmp_path, field, value):
    import sqlite3

    broker = DeterministicBroker()
    broker.place_order = Mock(side_effect=SystemExit("no receipt"))
    worker = submitter(tmp_path, broker)
    with pytest.raises(SystemExit):
        request(worker)
    record = worker.orders_store.get_order_by_client_order_id("stable-close")
    assert record.metadata["record_checksum_version"] == 2
    with sqlite3.connect(tmp_path / "orders.db") as connection:
        connection.execute(f"UPDATE orders SET {field} = ?", (value,))
    # Query the actual persisted identity, including when that identity drifted.
    lookup_id = value if field == "client_order_id" else "stable-close"
    from gpt_trader.persistence.durability import WriteError

    with pytest.raises(WriteError, match="checksum"):
        worker.orders_store.get_order_by_client_order_id(lookup_id)


def test_supported_status_update_preserves_complete_record_checksum(tmp_path):
    broker = DeterministicBroker()
    broker.place_order = Mock(side_effect=SystemExit("no receipt"))
    worker = submitter(tmp_path, broker)
    with pytest.raises(SystemExit):
        request(worker)
    result = worker.orders_store.update_status(
        "stable-close",
        OrderStatus.FILLED,
        filled_quantity=Decimal("0.125"),
        average_fill_price=Decimal("60000"),
    )
    assert result.success
    assert request(submitter(tmp_path, broker)).success
    assert worker.orders_store.verify_integrity() == (1, [])


@pytest.mark.parametrize("checksum, version", [(None, 2), ("", 2), (None, 99), ("", 1)])
def test_v2_or_unsupported_intent_record_requires_valid_checksum(tmp_path, checksum, version):
    import json
    import sqlite3

    broker = DeterministicBroker()
    broker.place_order = Mock(side_effect=SystemExit("no receipt"))
    worker = submitter(tmp_path, broker)
    with pytest.raises(SystemExit):
        request(worker)
    record = worker.orders_store.get_order_by_client_order_id("stable-close")
    metadata = {**record.metadata, "record_checksum_version": version}
    with sqlite3.connect(tmp_path / "orders.db") as connection:
        connection.execute(
            "UPDATE orders SET status='open',checksum=?,metadata=?",
            (checksum, json.dumps(metadata)),
        )
    result = request(submitter(tmp_path, broker))
    assert result.reason == "submission_uncertain" and not result.success
    assert worker.orders_store.verify_integrity() == (0, ["stable-close"])
    assert broker.place_order.call_count == 1


def test_malformed_status_update_rolls_back_and_releases_writer(tmp_path):
    import sqlite3
    from decimal import InvalidOperation

    broker = DeterministicBroker()
    worker = submitter(tmp_path, broker)
    result = request(worker)
    with sqlite3.connect(tmp_path / "orders.db") as connection:
        connection.execute("UPDATE orders SET filled_quantity='broken'")
    with pytest.raises(InvalidOperation):
        worker.orders_store.update_status(result.order_id, OrderStatus.OPEN)
    assert not worker.orders_store._get_connection().in_transaction
    with sqlite3.connect(tmp_path / "orders.db", timeout=0.1) as foreign:
        foreign.execute("UPDATE orders SET filled_quantity='0.125'")
