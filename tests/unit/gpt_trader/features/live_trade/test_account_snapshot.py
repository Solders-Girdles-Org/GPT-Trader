"""Read-only account snapshot service (account-snapshot ADR Option A, #1121).

Runs the REAL service and StateCollector; behavior is steered only at the
broker boundary (boundary-double returning real domain objects, per the
engine-fixture idiom from #1113).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.core import Balance, Position
from gpt_trader.features.live_trade.account_snapshot import AccountSnapshotService


@pytest.fixture
def config() -> BotConfig:
    return BotConfig(symbols=["BTC-USD"], interval=1)


@pytest.fixture
def broker() -> MagicMock:
    broker = MagicMock()
    broker.list_balances.return_value = [
        Balance(asset="USD", total=Decimal("1000"), available=Decimal("900")),
        Balance(asset="BTC", total=Decimal("0.5"), available=Decimal("0.5")),
    ]
    broker.list_positions.return_value = [
        Position(
            symbol="BTC-USD",
            quantity=Decimal("0.5"),
            entry_price=Decimal("40000"),
            mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("5000"),
            realized_pnl=Decimal("0"),
            side="long",
        )
    ]
    broker.get_ticker.return_value = {"price": "50100"}
    return broker


def test_snapshot_reads_broker_truth(broker: MagicMock, config: BotConfig) -> None:
    service = AccountSnapshotService(broker, config)

    assert service.supports_snapshots() is True
    snapshot = service.collect_snapshot()

    assert snapshot["broker"] == "MagicMock"
    assert {"asset": "USD", "total": "1000", "available": "900", "hold": "0"} in snapshot[
        "balances"
    ]
    # Collateral is the USD leg; equity adds unrealized PnL on top.
    assert snapshot["collateral_available"] == "900"
    assert snapshot["collateral_total"] == "1000"
    assert snapshot["collateral_assets"] == ["USD"]
    assert snapshot["unrealized_pnl_total"] == "5000"
    assert snapshot["equity"] == "5900"
    (position,) = snapshot["positions"]
    assert position["symbol"] == "BTC-USD"
    assert position["quantity"] == "0.5"
    assert position["mark_price"] == "50000"
    assert position["live_mark"] == "50100"
    assert snapshot["marks"] == {"BTC-USD": "50100"}
    assert "mark_errors" not in snapshot
    broker.place_order.assert_not_called()


def test_ticker_failure_is_reported_not_fatal(broker: MagicMock, config: BotConfig) -> None:
    broker.get_ticker.side_effect = RuntimeError("ticker feed down")
    service = AccountSnapshotService(broker, config)

    snapshot = service.collect_snapshot()

    assert snapshot["marks"] == {"BTC-USD": None}
    assert "ticker feed down" in snapshot["mark_errors"]["BTC-USD"]
    (position,) = snapshot["positions"]
    assert position["live_mark"] is None
    # The balances/positions core still reports.
    assert snapshot["equity"] == "5900"


def test_missing_ticker_price_reads_null(broker: MagicMock, config: BotConfig) -> None:
    broker.get_ticker.return_value = {}
    service = AccountSnapshotService(broker, config)

    snapshot = service.collect_snapshot()

    assert snapshot["marks"] == {"BTC-USD": None}
    assert "mark_errors" not in snapshot


def test_core_broker_failure_raises(broker: MagicMock, config: BotConfig) -> None:
    # A snapshot that cannot reach the broker must fail loudly, not report
    # zeros a responder might mistake for account truth.
    broker.list_balances.side_effect = RuntimeError("auth expired")
    service = AccountSnapshotService(broker, config)

    with pytest.raises(RuntimeError, match="auth expired"):
        service.collect_snapshot()


def test_supports_snapshots_requires_a_broker(config: BotConfig) -> None:
    assert AccountSnapshotService(None, config).supports_snapshots() is False
