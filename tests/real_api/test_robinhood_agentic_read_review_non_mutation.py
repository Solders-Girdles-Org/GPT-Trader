"""Opt-in Agentic account/read reviews with stable before/after evidence."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.accounts import PreviewRequest
from gpt_trader.features.brokerages.robinhood.agentic.models import (
    RobinhoodAgenticOptionReviewRequest,
)
from gpt_trader.features.brokerages.robinhood.agentic.read_review_access import (
    RobinhoodAgenticReadReviewAccess,
)

pytestmark = [
    pytest.mark.real_api,
    pytest.mark.requires_network,
    pytest.mark.requires_secrets,
]


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.fail(f"set {name} explicitly")
    return value


@pytest.mark.asyncio
async def test_live_reviews_do_not_change_account_observation() -> None:
    if os.getenv("ROBINHOOD_AGENTIC_REAL_READ_REVIEW_SMOKE") != "1":
        pytest.skip("set ROBINHOOD_AGENTIC_REAL_READ_REVIEW_SMOKE=1 to run")
    equity_symbol = _required("ROBINHOOD_AGENTIC_EQUITY_SYMBOL")
    equity_price = Decimal(_required("ROBINHOOD_AGENTIC_EQUITY_LIMIT_PRICE"))
    option_id = _required("ROBINHOOD_AGENTIC_OPTION_ID")
    option_price = Decimal(_required("ROBINHOOD_AGENTIC_OPTION_LIMIT_PRICE"))

    access = await RobinhoodAgenticReadReviewAccess.from_config(BotConfig.from_env())
    async with access:
        before = (await access.read_account()).to_dict(show_identifiers=True)
        equity = await access.review_equity_order(
            PreviewRequest(
                instrument=equity_symbol,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                order_type=OrderType.LIMIT,
                limit_price=equity_price,
            )
        )
        option = await access.review_option_order(
            RobinhoodAgenticOptionReviewRequest(
                option_id=option_id,
                side=OrderSide.BUY,
                position_effect="open",
                quantity=1,
                price=option_price,
            )
        )
        after = (await access.read_account()).to_dict(show_identifiers=True)

    before.pop("generated_at")
    after.pop("generated_at")
    assert before == after
    assert equity.preview.kind.value == "provider_simulation"
    assert equity.preview.to_dict()["non_binding"] is True
    assert option.non_binding is True
