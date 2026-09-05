"""Actual durable WS/REST accounting, crash recovery and evidence boundaries."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest

from gpt_trader.core.fill_accounting import AccountingIntegrityError, FillFact, PositionBaseline
from gpt_trader.features.brokerages.coinbase.rest.pnl_service import PnLService
from gpt_trader.features.brokerages.coinbase.rest.position_state_store import PositionStateStore
from gpt_trader.features.brokerages.coinbase.user_event_handler import CoinbaseUserEventHandler
from gpt_trader.persistence.orders_store import OrdersStore, OrderStatus
from tests.unit.gpt_trader.features.brokerages.coinbase.accounting_test_helpers import (
    NOW,
    _rest,
    _setup,
    _ws,
)


def test_cumulative_ws_and_rest_constituents_do_not_overlap(tmp_path):
    store, handler, pnl = _setup(tmp_path)
    _ws(handler, ".05", "100")
    _ws(handler, ".1", "110")
    assert pnl.get_position_pnl("BTC-USD")["realized_pnl"] is None
    _rest(handler, "a", ".05", "100")
    _rest(handler, "b", ".05", "120", 1)
    result = pnl.get_position_pnl("BTC-USD")
    assert result["quantity"] == Decimal(".1")
    assert result["entry"] == Decimal("110")
    assert result["unrealized_pnl"] == Decimal("1")
    assert store.get_order("order").filled_quantity == Decimal(".1")


def test_restart_replays_commit_without_callback_and_duplicate_is_noop(tmp_path):
    store, handler, pnl = _setup(tmp_path)
    _rest(handler, "a", ".1", "110")
    before = pnl.get_position_pnl("BTC-USD")
    store.close()
    restored = OrdersStore(tmp_path)
    restored.initialize()
    handler = CoinbaseUserEventHandler(
        broker=None,
        orders_store=restored,
        event_store=None,
        bot_id="different-bot-not-account",
        market_data_service=None,
        symbols=["BTC-USD"],
    )
    assert _rest(handler, "a", ".1", "110")[0] is False
    projection = restored.accounting.projections()["BTC-USD"]
    assert projection.entry_price == before["entry"]
    assert projection.quantity == before["quantity"]
    assert restored.get_order("order").filled_quantity == Decimal(".1")


def test_old_identified_fill_is_not_dropped_by_watermark(tmp_path):
    store, handler, _ = _setup(tmp_path)
    handler._fill_watermark = NOW + timedelta(days=1)
    assert _rest(handler, "a", ".1", "110")[0] is True
    assert store.accounting.projections()["BTC-USD"].fill_count == 1


def test_same_fill_id_conflict_rolls_back(tmp_path):
    store, handler, _ = _setup(tmp_path)
    _rest(handler, "a", ".05", "100")
    with pytest.raises(AccountingIntegrityError):
        _rest(handler, "a", ".05", "101")
    assert store.get_order("order").filled_quantity == Decimal(".05")
    assert not store._get_connection().in_transaction


def test_inclusive_baseline_does_not_reapply_preexisting_fill(tmp_path):
    store = OrdersStore(tmp_path)
    store.initialize()
    store.accounting.record_baseline(
        PositionBaseline(
            "BTC-USD",
            NOW.isoformat(),
            "1",
            "100",
            "fixture opening inventory includes same-time buy",
            actor_id="fixture",
        )
    )
    store.accounting.record_fill(
        FillFact("before", "buy", "", "BTC-USD", "buy", "1", "100", NOW.isoformat())
    )
    store.accounting.record_fill(
        FillFact(
            "after",
            "sell",
            "",
            "BTC-USD",
            "sell",
            ".4",
            "120",
            (NOW + timedelta(seconds=1)).isoformat(),
        )
    )
    projection = store.accounting.projections()["BTC-USD"]
    assert projection.quantity == Decimal(".6")
    assert projection.realized_pnl == Decimal("8")


def test_unknown_opening_inventory_never_becomes_zero(tmp_path):
    store = OrdersStore(tmp_path)
    store.initialize()
    store.accounting.record_fill(
        FillFact("a", "order", "", "BTC-USD", "sell", "1", "100", NOW.isoformat())
    )
    projection = store.accounting.projections()["BTC-USD"]
    assert projection.quantity is None and projection.realized_pnl is None
    assert projection.reasons == ("opening_inventory_unknown",)


def test_reducer_flip_and_ambiguous_time(tmp_path):
    store = OrdersStore(tmp_path)
    store.initialize()
    store.accounting.record_baseline(
        PositionBaseline(
            "BTC-USD",
            (NOW - timedelta(days=1)).isoformat(),
            "1",
            "100",
            "explicit existing long",
            actor_id="fixture",
        )
    )
    store.accounting.record_fill(
        FillFact("sell", "s", "", "BTC-USD", "sell", "1.5", "120", NOW.isoformat())
    )
    projection = store.accounting.projections()["BTC-USD"]
    assert projection.quantity == Decimal("-.5")
    assert projection.entry_price == Decimal(120)
    assert projection.realized_pnl == Decimal(20)
    assert projection.fee_coverage == "unknown"
    store.accounting.record_fill(
        FillFact("buy", "b", "", "BTC-USD", "buy", ".1", "110", NOW.isoformat())
    )
    projection = store.accounting.projections()["BTC-USD"]
    assert projection.realized_pnl is None
    assert "execution_order_ambiguous" in projection.reasons


def test_semantically_equivalent_duplicate_does_not_conflict(tmp_path):
    store, handler, _ = _setup(tmp_path)
    _rest(handler, "a", ".1", "110")
    assert not _rest(handler, "a", "0.1000", "110.000")[0]
    assert store.accounting.projections()["BTC-USD"].fill_count == 1


def test_known_realized_pnl_survives_missing_mark(tmp_path):
    store, handler, _ = _setup(tmp_path)
    _rest(handler, "a", ".1", "110")
    store.accounting.record_fill(
        FillFact(
            "s",
            "sell",
            "",
            "BTC-USD",
            "sell",
            ".05",
            "120",
            (NOW + timedelta(seconds=1)).isoformat(),
        )
    )
    pnl = PnLService(position_store=PositionStateStore(), market_data=None, orders_store=store)
    result = pnl.get_portfolio_pnl()
    assert result["total_realized_pnl"] == Decimal(".5")
    assert result["total_unrealized_pnl"] is None
    assert result["total_pnl"] is None
    assert result["positions"][0]["accounting_status"] == "complete"
    assert result["positions"][0]["unrealized_status"] == "unavailable"


def test_closed_symbol_remains_in_actual_guard_projection_and_equity_is_venue_based(tmp_path):
    from gpt_trader.features.live_trade.execution.guard_manager import GuardManager

    store, handler, _ = _setup(tmp_path)
    _rest(handler, "a", ".1", "110")
    store.accounting.record_fill(
        FillFact(
            "s",
            "sell",
            "",
            "BTC-USD",
            "sell",
            ".1",
            "120",
            (NOW + timedelta(seconds=1)).isoformat(),
        )
    )
    broker = Mock()
    broker.list_balances.return_value = []
    broker.list_positions.return_value = []
    manager = GuardManager(
        broker=broker,
        risk_manager=Mock(),
        equity_calculator=lambda _: (Decimal(998), [], Decimal(0)),
        open_orders=[],
        invalidate_cache_callback=lambda: None,
    )
    manager.set_pnl_projection_provider(handler.get_accounting_pnl)
    state = manager.collect_runtime_guard_state()
    assert state.positions == []
    assert state.equity == Decimal(998)
    assert state.positions_pnl["BTC-USD"]["realized_pnl"] == Decimal(1)
    assert state.pnl_availability["BTC-USD"]["basis"] == "gross_trade_pnl"


def test_pending_lost_acknowledgment_is_not_a_complete_zero_position(tmp_path):
    from dataclasses import replace

    store, _, _ = _setup(tmp_path)
    record = store.get_order("order")
    store.save_order(replace(record, status=OrderStatus.PENDING))
    projection = store.accounting.projections()["BTC-USD"]
    assert projection.realized_pnl is None
    assert "pending_submission_unresolved:order" in projection.reasons


def test_explicit_covered_terminal_order_does_not_require_prebaseline_constituents(tmp_path):
    store, handler, _ = _setup(tmp_path)
    _ws(handler, ".1", "110")
    # A separate initialized copy represents a legacy DB before any baseline.
    store._get_connection().execute("DELETE FROM accounting_baselines")
    baseline = PositionBaseline(
        "BTC-USD",
        (NOW + timedelta(days=1)).isoformat(),
        ".1",
        "110",
        "operator evidence includes this complete historical order",
        covered_order_ids=("order",),
        actor_id="fixture",
    )
    store.accounting.record_baseline(baseline)
    store.accounting.record_baseline(baseline)  # Canonical serialized duplicate is idempotent.
    projection = store.accounting.projections()["BTC-USD"]
    assert projection.complete and projection.quantity == Decimal(".1")
    with pytest.raises(AccountingIntegrityError, match="baseline coverage"):
        store.accounting.record_fill(
            FillFact(
                "late",
                "order",
                "client",
                "BTC-USD",
                "buy",
                ".1",
                "110",
                (NOW + timedelta(days=2)).isoformat(),
            )
        )


def test_multi_order_websocket_persists_every_cumulative_observation(tmp_path):
    store, handler, _ = _setup(tmp_path)
    handler.handle_user_message(
        {
            "sequence_num": 1,
            "events": [
                {
                    "type": "update",
                    "orders": [
                        {
                            "order_id": "one",
                            "client_order_id": "c-one",
                            "product_id": "BTC-USD",
                            "order_side": "BUY",
                            "status": "FILLED",
                            "filled_size": "1",
                            "avg_price": "100",
                            "creation_time": "2020-01-01T00:00:00Z",
                        },
                        {
                            "order_id": "two",
                            "client_order_id": "c-two",
                            "product_id": "ETH-USD",
                            "order_side": "BUY",
                            "status": "FILLED",
                            "filled_size": "2",
                            "avg_price": "200",
                            "creation_time": "2020-01-01T00:00:00Z",
                        },
                    ],
                }
            ],
        }
    )
    observations = store.accounting._observations()
    assert {item["order_id"] for item in observations} == {"one", "two"}
    assert all(datetime.fromisoformat(item["observed_at"]).year >= 2026 for item in observations)
    assert store.get_order("one").filled_quantity == Decimal(1)
    assert store.get_order("two").filled_quantity == Decimal(2)


def test_conflicting_acknowledged_order_id_cannot_split_fill_identity(tmp_path):
    store, _, _ = _setup(tmp_path)
    with pytest.raises(AccountingIntegrityError, match="venue order ID"):
        store.accounting.record_fill(
            FillFact(
                "a",
                "different-venue-order",
                "client",
                "BTC-USD",
                "buy",
                ".1",
                "110",
                NOW.isoformat(),
            )
        )
    assert store._get_connection().execute("SELECT count(*) FROM fill_facts").fetchone()[0] == 0


def test_unavailable_projection_does_not_publish_fake_zero_pnl(tmp_path):
    from gpt_trader.features.live_trade.execution.guard_manager import GuardManager

    store, handler, _ = _setup(tmp_path)
    _ws(handler, ".1", "110")
    broker = Mock()
    broker.list_balances.return_value = []
    broker.list_positions.return_value = []
    manager = GuardManager(
        broker=broker,
        risk_manager=Mock(),
        equity_calculator=lambda _: (Decimal(998), [], Decimal(0)),
        open_orders=[],
        invalidate_cache_callback=lambda: None,
    )
    manager.set_pnl_projection_provider(handler.get_accounting_pnl)
    state = manager.collect_runtime_guard_state()
    assert state.equity == Decimal(998)
    assert state.positions_pnl == {}
    assert state.pnl_availability["BTC-USD"]["status"] == "incomplete"


def test_explicit_storeless_handler_keeps_legacy_callback():
    handler = CoinbaseUserEventHandler(
        broker=None,
        orders_store=None,
        event_store=None,
        bot_id="legacy",
        market_data_service=None,
        symbols=["BTC-USD"],
    )
    handler._process_fill_for_pnl = Mock()
    _ws(handler, ".1", "110")
    handler._process_fill_for_pnl.assert_called_once()


def test_pure_reducer_refuses_cross_symbol_baseline():
    from gpt_trader.core.fill_accounting import project_position

    baseline = PositionBaseline("ETH-USD", NOW.isoformat(), "1", "100", "fixture", "reviewer")
    with pytest.raises(AccountingIntegrityError, match="symbol mismatch"):
        project_position("BTC-USD", [], baseline)


def test_exact_facts_do_not_conflict_with_their_own_rounded_average(tmp_path):
    from dataclasses import replace

    store, handler, _ = _setup(tmp_path)
    store.save_order(replace(store.get_order("order"), quantity=Decimal(3)))
    _rest(handler, "a", "1", "100")
    _rest(handler, "b", "2", "101", 1)
    projection = store.accounting.projections()["BTC-USD"]
    assert projection.complete
    assert projection.quantity == Decimal(3)
    assert projection.entry_price == Decimal(302) / Decimal(3)
