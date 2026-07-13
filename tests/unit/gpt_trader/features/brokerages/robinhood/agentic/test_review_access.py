from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.accounts import PreviewRequest
from gpt_trader.features.brokerages.robinhood.agentic.account_access import (
    RobinhoodAgenticAccountReader,
)
from gpt_trader.features.brokerages.robinhood.agentic.errors import RobinhoodAgenticViolation
from gpt_trader.features.brokerages.robinhood.agentic.models import (
    RobinhoodAgenticOptionReviewRequest,
)
from gpt_trader.features.brokerages.robinhood.agentic.review_access import (
    RobinhoodAgenticEquityReviewProvider,
    RobinhoodAgenticOptionReviewProvider,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def providers(gateway: Any) -> tuple[Any, Any]:
    reader = RobinhoodAgenticAccountReader(
        gateway=gateway,
        expected_account_number="RH-EXPECTED",
        clock=lambda: NOW,
    )
    return (
        RobinhoodAgenticEquityReviewProvider(
            gateway=gateway,
            account_reader=reader,
            expected_account_number="RH-EXPECTED",
            clock=lambda: NOW,
        ),
        RobinhoodAgenticOptionReviewProvider(
            gateway=gateway,
            account_reader=reader,
            expected_account_number="RH-EXPECTED",
            clock=lambda: NOW,
        ),
    )


@pytest.mark.asyncio
async def test_equity_review_is_non_binding_identity_bound_evidence(gateway: Any) -> None:
    gateway.equity_payload = {
        "data": {
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "quantity": "2",
            "limit_price": "190",
            "order_checks": {"alert_type": "BUYING_POWER"},
            "quote_data": {"ask_price": "189.50", "bid_price": "189.40"},
            "market_data_disclosure": "Required disclosure",
        },
        "guide": "",
    }
    equity, _ = providers(gateway)
    request = PreviewRequest(
        instrument="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("190"),
    )

    evidence = await equity.review(request)

    assert evidence.preview.to_dict()["non_binding"] is True
    assert evidence.preview.estimated_price == Decimal("189.50")
    assert evidence.preview.estimated_total == Decimal("379.00")
    assert evidence.preview.errors and "BUYING_POWER" in evidence.preview.errors[0]
    assert evidence.market_data_disclosure == "Required disclosure"
    assert gateway.calls[-1][0] == "review_equity_order"
    assert gateway.calls[-1][1]["account_number"] == "RH-EXPECTED"


@pytest.mark.asyncio
async def test_equity_review_rejects_echo_drift(gateway: Any) -> None:
    gateway.equity_payload = {
        "data": {
            "symbol": "MSFT",
            "side": "buy",
            "type": "market",
            "quantity": "1",
            "order_checks": {},
            "quote_data": None,
        },
        "guide": "",
    }
    equity, _ = providers(gateway)
    with pytest.raises(RobinhoodAgenticViolation, match="does not match"):
        await equity.review(
            PreviewRequest(
                instrument="AAPL",
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                order_type=OrderType.MARKET,
            )
        )


@pytest.mark.asyncio
async def test_option_review_is_typed_single_leg_and_rejected_evidence(gateway: Any) -> None:
    leg = {
        "option_id": "option-1",
        "side": "buy",
        "position_effect": "open",
        "ratio_quantity": 1,
    }
    gateway.option_payload = {
        "data": {
            "account_number": "RH-EXPECTED",
            "type": "limit",
            "quantity": "1",
            "price": "1.50",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
            "legs": [leg],
            "order_checks": {"alertType": "ACCOUNT_RESTRICTION", "details": {}},
            "option_quotes": [],
            "fees": None,
            "collateral": None,
        },
        "guide": "",
    }
    _, options = providers(gateway)
    request = RobinhoodAgenticOptionReviewRequest(
        option_id="option-1",
        side=OrderSide.BUY,
        position_effect="open",
        quantity=1,
        price=Decimal("1.50"),
    )

    evidence = await options.review(request)

    assert evidence.non_binding is True
    assert evidence.errors and "ACCOUNT_RESTRICTION" in evidence.errors[0]
    assert evidence.identity_fingerprint
    assert gateway.calls[-1][0] == "review_option_order"


@pytest.mark.asyncio
async def test_option_review_rejects_observed_account_mismatch(gateway: Any) -> None:
    gateway.option_payload = {
        "data": {
            "account_number": "OTHER",
            "type": "limit",
            "quantity": "1",
            "legs": [],
            "order_checks": {},
            "option_quotes": [],
        },
        "guide": "",
    }
    _, options = providers(gateway)
    with pytest.raises(RobinhoodAgenticViolation, match="does not match"):
        await options.review(
            RobinhoodAgenticOptionReviewRequest(
                option_id="option-1",
                side=OrderSide.BUY,
                position_effect="open",
                quantity=1,
                price=Decimal("1.50"),
            )
        )
