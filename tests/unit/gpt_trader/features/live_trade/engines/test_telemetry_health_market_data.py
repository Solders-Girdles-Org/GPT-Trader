"""Tests for telemetry health market-data helpers."""

from __future__ import annotations

import time
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gpt_trader.features.live_trade.engines import telemetry_health


def _make_runtime_state() -> SimpleNamespace:
    return SimpleNamespace(
        mark_lock=nullcontext(),
        mark_windows={},
        orderbook_lock=nullcontext(),
        orderbook_snapshots={},
        trade_lock=nullcontext(),
        trade_aggregators={},
    )


def _make_ctx(
    *,
    runtime_state: SimpleNamespace | None = None,
    risk_manager: MagicMock | None = None,
    status_interval: object = 60,
) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_state=runtime_state or _make_runtime_state(),
        risk_manager=risk_manager,
        event_store=MagicMock(),
        bot_id="bot-1",
        config=SimpleNamespace(short_ma=2, long_ma=3, status_interval=status_interval),
        strategy_coordinator=None,
    )


@pytest.fixture(autouse=True)
def reset_snapshot_throttles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry_health, "_last_snapshot_times", {})


def test_update_orderbook_snapshot_invalid_product_id(monkeypatch) -> None:
    ctx = _make_ctx()
    event = SimpleNamespace(product_id=None)
    emit_snapshot = MagicMock()
    monkeypatch.setattr(telemetry_health, "emit_orderbook_snapshot", emit_snapshot)

    import gpt_trader.features.brokerages.coinbase.market_data_features as mdf

    snapshot = MagicMock()
    monkeypatch.setattr(mdf, "DepthSnapshot", MagicMock(from_orderbook_update=lambda _: snapshot))

    telemetry_health.update_orderbook_snapshot(ctx, event)

    assert ctx.runtime_state.orderbook_snapshots == {}
    emit_snapshot.assert_not_called()


def test_update_orderbook_snapshot_success_calls_emit(monkeypatch) -> None:
    ctx = _make_ctx()
    event = SimpleNamespace(product_id="BTC-USD")
    emit_snapshot = MagicMock()
    monkeypatch.setattr(telemetry_health, "emit_orderbook_snapshot", emit_snapshot)

    import gpt_trader.features.brokerages.coinbase.market_data_features as mdf

    snapshot = MagicMock()
    snapshot.spread_bps = 1.0
    snapshot.bids = [1]
    snapshot.asks = [1]
    monkeypatch.setattr(mdf, "DepthSnapshot", MagicMock(from_orderbook_update=lambda _: snapshot))

    telemetry_health.update_orderbook_snapshot(ctx, event)

    assert ctx.runtime_state.orderbook_snapshots["BTC-USD"] is snapshot
    emit_snapshot.assert_called_once_with(ctx, "BTC-USD")


def test_update_orderbook_snapshot_logs_on_error(monkeypatch) -> None:
    ctx = _make_ctx()
    event = SimpleNamespace(product_id="BTC-USD")
    error_logger = MagicMock()
    monkeypatch.setattr(telemetry_health.logger, "error", error_logger)

    import gpt_trader.features.brokerages.coinbase.market_data_features as mdf

    class ExplodingDepthSnapshot:
        @staticmethod
        def from_orderbook_update(_event):
            raise ValueError("bad update")

    monkeypatch.setattr(mdf, "DepthSnapshot", ExplodingDepthSnapshot)

    telemetry_health.update_orderbook_snapshot(ctx, event)

    error_logger.assert_called_once()


def test_update_trade_aggregator_creates_and_emits(monkeypatch) -> None:
    ctx = _make_ctx()
    event = SimpleNamespace(
        product_id="BTC-USD",
        price=Decimal("50000"),
        size=Decimal("1"),
        side="buy",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )
    emit_summary = MagicMock()
    monkeypatch.setattr(telemetry_health, "emit_trade_flow_summary", emit_summary)

    import gpt_trader.features.brokerages.coinbase.market_data_features as mdf

    agg = MagicMock()
    monkeypatch.setattr(mdf, "TradeTapeAgg", MagicMock(return_value=agg))

    telemetry_health.update_trade_aggregator(ctx, event)

    assert ctx.runtime_state.trade_aggregators["BTC-USD"] is agg
    agg.add_trade.assert_called_once_with(event.price, event.size, event.side, event.timestamp)
    emit_summary.assert_called_once_with(ctx, "BTC-USD")


def test_update_trade_aggregator_skips_missing_fields(monkeypatch) -> None:
    ctx = _make_ctx()
    event = SimpleNamespace(
        product_id="BTC-USD",
        price=None,
        size=Decimal("1"),
        side="buy",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )
    emit_summary = MagicMock()
    monkeypatch.setattr(telemetry_health, "emit_trade_flow_summary", emit_summary)

    import gpt_trader.features.brokerages.coinbase.market_data_features as mdf

    tape = MagicMock()
    monkeypatch.setattr(mdf, "TradeTapeAgg", tape)

    telemetry_health.update_trade_aggregator(ctx, event)

    assert ctx.runtime_state.trade_aggregators == {}
    tape.assert_not_called()
    emit_summary.assert_not_called()


def test_emit_orderbook_snapshot_throttles(monkeypatch) -> None:
    ctx = _make_ctx()
    snapshot = MagicMock()
    snapshot.spread_bps = 2.0
    snapshot.mid = Decimal("50000")
    snapshot.bids = [1, 2]
    snapshot.asks = [3]
    snapshot.get_depth.return_value = (Decimal("10"), Decimal("11"))
    ctx.runtime_state.orderbook_snapshots["BTC-USD"] = snapshot

    times = iter([100.0, 101.0])
    monkeypatch.setattr(time, "time", lambda: next(times))

    telemetry_health.emit_orderbook_snapshot(ctx, "BTC-USD")
    telemetry_health.emit_orderbook_snapshot(ctx, "BTC-USD")

    assert ctx.event_store.append.call_count == 1


def test_emit_trade_flow_summary_missing_and_present(monkeypatch) -> None:
    ctx = _make_ctx()
    agg = MagicMock()
    agg.get_stats.return_value = {
        "count": 1,
        "volume": Decimal("1"),
        "vwap": Decimal("1"),
        "avg_size": Decimal("1"),
        "aggressor_ratio": 0.5,
    }
    times = iter([100.0, 200.0])
    monkeypatch.setattr(time, "time", lambda: next(times))

    telemetry_health.emit_trade_flow_summary(ctx, "BTC-USD")
    ctx.runtime_state.trade_aggregators["BTC-USD"] = agg
    telemetry_health.emit_trade_flow_summary(ctx, "BTC-USD")

    assert ctx.event_store.append.call_count == 1
