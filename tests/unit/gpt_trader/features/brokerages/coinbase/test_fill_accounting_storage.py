"""Actual durable WS/REST accounting, crash recovery and evidence boundaries."""

from datetime import timedelta
from decimal import Decimal

import pytest

from gpt_trader.core.fill_accounting import AccountingIntegrityError, FillFact, PositionBaseline
from gpt_trader.features.brokerages.coinbase.ws_events import OrderUpdateEvent
from gpt_trader.persistence.orders_store import OrdersStore, OrderStatus
from tests.unit.gpt_trader.features.brokerages.coinbase.accounting_test_helpers import (
    NOW,
    _rest,
    _setup,
    _ws,
)


def test_backup_includes_baseline_facts_and_order_receipt(tmp_path):
    store, handler, _ = _setup(tmp_path / "source")
    _rest(handler, "a", ".1", "110")
    destination = tmp_path / "copy" / "orders.db"
    store.backup_to(destination)
    restored = OrdersStore(destination.parent)
    restored.initialize()
    assert restored.accounting.projections() == store.accounting.projections()
    assert restored.get_order("order").to_dict() == store.get_order("order").to_dict()
    with pytest.raises(Exception, match="already exists"):
        store.backup_to(destination)


@pytest.mark.parametrize("table", ["fill_facts", "fill_observations", "accounting_baselines"])
def test_checksum_missing_fails_read_and_backup(tmp_path, table):
    store, handler, _ = _setup(tmp_path / "source")
    _ws(handler, ".1", "110")
    _rest(handler, "a", ".1", "110")
    store._get_connection().execute(f"UPDATE {table} SET checksum = ''")
    with pytest.raises(AccountingIntegrityError):
        store.accounting.projections()
    with pytest.raises(AccountingIntegrityError):
        store.backup_to(tmp_path / "bad-copy" / "orders.db")
    assert not (tmp_path / "bad-copy" / "orders.db").exists()


def test_missing_schema_marker_is_not_reinitialized(tmp_path):
    store, _, _ = _setup(tmp_path)
    store._get_connection().execute("DELETE FROM accounting_schema")
    store.close()
    with pytest.raises(AccountingIntegrityError, match="lacks schema marker"):
        OrdersStore(tmp_path).initialize()


def test_real_sqlite_failed_fill_write_retries_without_lost_dedupe(tmp_path):
    from gpt_trader.persistence.durability import WriteError

    store, handler, _ = _setup(tmp_path)
    connection = store._get_connection()
    connection.execute("PRAGMA query_only=ON")
    with pytest.raises(WriteError):
        _rest(handler, "a", ".1", "110")
    assert not connection.in_transaction
    assert store.get_order("order").filled_quantity == 0
    connection.execute("PRAGMA query_only=OFF")
    assert _rest(handler, "a", ".1", "110")[0]
    assert store.accounting.projections()["BTC-USD"].quantity == Decimal(".1")


def test_crash_after_fact_before_order_write_rolls_back(tmp_path, monkeypatch):
    store, handler, _ = _setup(tmp_path)
    original = store.upsert_by_client_id

    def crash(*args, **kwargs):
        raise SystemExit("injected crash before order write")

    monkeypatch.setattr(store, "upsert_by_client_id", crash)
    with pytest.raises(SystemExit):
        _rest(handler, "a", ".1", "110")
    assert store._get_connection().execute("SELECT count(*) FROM fill_facts").fetchone()[0] == 0
    assert not store._get_connection().in_transaction
    monkeypatch.setattr(store, "upsert_by_client_id", original)
    assert _rest(handler, "a", ".1", "110")[0]


def test_two_connections_admit_one_fact(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store, _, _ = _setup(tmp_path)
    fact = FillFact("a", "order", "client", "BTC-USD", "buy", ".1", "110", NOW.isoformat())

    def admit():
        separate = OrdersStore(tmp_path)
        try:
            separate.initialize()
            return separate.accounting.record_fill(fact)
        finally:
            separate.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: admit(), range(2)))
    assert sorted(outcomes) == [False, True]
    assert store.accounting.projections()["BTC-USD"].fill_count == 1


def test_schema_install_rolls_back_all_new_tables_and_retries(tmp_path):
    import sqlite3

    store = OrdersStore(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    connection = store._get_connection()
    connection.set_authorizer(
        lambda action, name, *args: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == "fill_observations"
            else sqlite3.SQLITE_OK
        )
    )
    with pytest.raises(sqlite3.DatabaseError):
        store.initialize()
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "accounting_schema" not in tables and "fill_facts" not in tables
    assert not connection.in_transaction
    connection.set_authorizer(None)
    store.initialize()
    assert connection.execute("SELECT version FROM accounting_schema").fetchone()[0] == 1


def test_baseline_rejects_foreign_receipt_drift_and_ws_cannot_mutate_it(tmp_path):
    from dataclasses import replace

    store, handler, _ = _setup(tmp_path)
    _ws(handler, ".1", "110")
    store._get_connection().execute("DELETE FROM accounting_baselines")
    store.accounting.record_baseline(
        PositionBaseline(
            "BTC-USD",
            (NOW + timedelta(days=1)).isoformat(),
            ".1",
            "110",
            "covers legacy receipt",
            "fixture",
            ("order",),
        )
    )
    handler.handle_order_update(
        OrderUpdateEvent(
            "order",
            "client",
            "BTC-USD",
            "FILLED",
            "BUY",
            "MARKET",
            Decimal(".2"),
            Decimal(".2"),
            None,
            Decimal(110),
            NOW,
        )
    )
    assert store.accounting.projections()["BTC-USD"].quantity == Decimal(".1")
    # A legacy external writer bypassing the guarded update API still cannot
    # silently invalidate the baseline's bound historical receipt.
    store.save_order(
        replace(store.get_order("order"), quantity=Decimal(".2"), filled_quantity=Decimal(".2"))
    )
    with pytest.raises(AccountingIntegrityError, match="covered receipt changed"):
        store.accounting.projections()


def test_future_baseline_and_fill_are_not_current_facts(tmp_path):
    store = OrdersStore(tmp_path)
    store.initialize()
    with pytest.raises(AccountingIntegrityError, match="Future"):
        store.accounting.record_baseline(
            PositionBaseline(
                "BTC-USD", "2099-01-01T00:00:00+00:00", "0", None, "future assertion", "fixture"
            )
        )
    with pytest.raises(AccountingIntegrityError, match="Future"):
        store.accounting.record_fill(
            FillFact("a", "order", "", "BTC-USD", "buy", "1", "100", "2099-01-01T00:00:00+00:00")
        )


@pytest.mark.parametrize(
    "status", [OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED]
)
def test_identified_fill_cannot_reopen_terminal_receipt(tmp_path, status):
    from dataclasses import replace

    store, handler, _ = _setup(tmp_path)
    _rest(handler, "a", ".05", "100")
    record = store.get_order("order")
    store.save_order(replace(record, status=status))
    with pytest.raises(AccountingIntegrityError, match="terminal receipt"):
        _rest(handler, "b", ".01", "100", 1)
    assert store.get_order("order").status is status
    assert store.get_order("order").filled_quantity == Decimal(".05")
    assert store.accounting.projections()["BTC-USD"].quantity == Decimal(".05")


@pytest.mark.parametrize("status", [OrderStatus.CANCELLED, OrderStatus.EXPIRED])
def test_stale_ws_cannot_erase_terminal_bound_before_late_fill(tmp_path, status):
    from dataclasses import replace

    store, handler, _ = _setup(tmp_path)
    _rest(handler, "a", ".05", "100")
    receipt = replace(store.get_order("order"), status=status)
    store.save_order(receipt)
    stored = store.get_order("order")
    handler.handle_order_update(
        OrderUpdateEvent(
            "order",
            "client",
            "BTC-USD",
            "OPEN",
            "BUY",
            "MARKET",
            Decimal(".1"),
            Decimal(".05"),
            None,
            Decimal(100),
            NOW,
        )
    )
    assert store.get_order("order") == stored
    with pytest.raises(AccountingIntegrityError, match="terminal receipt"):
        _rest(handler, "b", ".01", "100", 1)
    assert store.get_order("order") == stored
    assert store.accounting.projections()["BTC-USD"].quantity == Decimal(".05")
