"""Opt-in Coinbase read/preview smoke test with before/after mutation checks."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.features.brokerages.accounts import PreviewRequest
from gpt_trader.features.brokerages.coinbase.read_preview_access import (
    CoinbaseReadPreviewAccess,
)

pytestmark = [
    pytest.mark.real_api,
    pytest.mark.requires_network,
    pytest.mark.requires_secrets,
]


def _stable_account_state(access: CoinbaseReadPreviewAccess) -> dict[str, Any]:
    observation = access.reader.read_account()
    orders = access.client.list_all_orders().get("orders", [])
    return {
        "balances": tuple(
            (item.asset, item.total, item.available, item.hold) for item in observation.balances
        ),
        "positions": tuple(
            (item.instrument, item.quantity, item.average_cost) for item in observation.positions
        ),
        "orders": tuple(
            sorted(
                (
                    str(item.get("order_id") or ""),
                    str(item.get("product_id") or ""),
                    str(item.get("side") or ""),
                    str(item.get("status") or ""),
                    str(item.get("filled_size") or ""),
                    str(item.get("filled_value") or ""),
                    str(item.get("outstanding_hold_amount") or ""),
                    repr(item.get("order_configuration") or {}),
                )
                for item in orders
            )
        ),
    }


def test_live_preview_does_not_change_account_state() -> None:
    if os.getenv("COINBASE_REAL_READ_PREVIEW_SMOKE") != "1":
        pytest.skip("set COINBASE_REAL_READ_PREVIEW_SMOKE=1 to run")

    instrument = os.getenv("COINBASE_PREVIEW_INSTRUMENT", "BTC-USD")
    quantity = Decimal(os.getenv("COINBASE_PREVIEW_QUANTITY", "0.00000001"))
    access = CoinbaseReadPreviewAccess.from_config(BotConfig.from_env())
    try:
        before = _stable_account_state(access)
        preview = access.preview_provider.preview(
            PreviewRequest(
                instrument=instrument,
                side="buy",
                quantity=quantity,
                order_type="market",
            )
        )
        after = _stable_account_state(access)
    finally:
        access.close()

    assert preview.kind.value == "provider_simulation"
    assert preview.errors == ()
    assert any(
        value is not None
        for value in (
            preview.estimated_price,
            preview.estimated_fee,
            preview.estimated_total,
        )
    )
    assert before == after
