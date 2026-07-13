"""Opt-in Robinhood Crypto GET-only smoke with before/after mutation checks."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.accounts import PreviewRequest
from gpt_trader.features.brokerages.robinhood.crypto.read_preview_access import (
    RobinhoodCryptoReadPreviewAccess,
)

pytestmark = [
    pytest.mark.real_api,
    pytest.mark.requires_network,
    pytest.mark.requires_secrets,
]


def _stable_account_state(access: RobinhoodCryptoReadPreviewAccess) -> dict[str, Any]:
    observation = access.reader.read_account()
    order_history = access.read_order_history()
    return {
        "identity_fingerprint": observation.identity.fingerprint,
        "balances": tuple(
            (item.asset, item.total, item.available, item.hold) for item in observation.balances
        ),
        "positions": tuple(
            (item.instrument, item.quantity, item.average_cost) for item in observation.positions
        ),
        "order_identity_fingerprint": order_history.identity_fingerprint,
        "orders": order_history.orders,
    }


def test_live_estimate_does_not_change_account_state() -> None:
    if os.getenv("ROBINHOOD_CRYPTO_REAL_READ_PREVIEW_SMOKE") != "1":
        pytest.skip("set ROBINHOOD_CRYPTO_REAL_READ_PREVIEW_SMOKE=1 to run")
    symbol = os.getenv("ROBINHOOD_CRYPTO_PREVIEW_INSTRUMENT", "").strip()
    quantity_text = os.getenv("ROBINHOOD_CRYPTO_PREVIEW_QUANTITY", "").strip()
    if not symbol or not quantity_text:
        pytest.fail(
            "set ROBINHOOD_CRYPTO_PREVIEW_INSTRUMENT and "
            "ROBINHOOD_CRYPTO_PREVIEW_QUANTITY explicitly"
        )

    access = RobinhoodCryptoReadPreviewAccess.from_config(BotConfig.from_env())
    try:
        before = _stable_account_state(access)
        preview = access.preview_provider.preview(
            PreviewRequest(
                instrument=symbol,
                side=OrderSide.BUY,
                quantity=Decimal(quantity_text),
                order_type=OrderType.MARKET,
            )
        )
        after = _stable_account_state(access)
    finally:
        access.close()

    assert preview.kind.value == "provider_estimate"
    assert preview.errors == ()
    assert preview.estimated_price is not None
    assert preview.estimated_fee is not None
    assert preview.estimated_total is not None
    assert before == after
