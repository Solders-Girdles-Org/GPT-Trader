from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from gpt_trader.core import OrderSide, OrderType
from gpt_trader.features.brokerages.accounts import (
    AccountIdentity,
    AccountProvider,
    PreviewKind,
    PreviewRequest,
)
from gpt_trader.features.brokerages.robinhood.crypto.account_access import (
    RobinhoodCryptoAccountReader,
)
from gpt_trader.features.brokerages.robinhood.crypto.models import RobinhoodCryptoEstimate
from gpt_trader.features.brokerages.robinhood.crypto.preview_access import (
    RobinhoodCryptoPreviewProvider,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Reader:
    def __init__(self, client: object) -> None:
        self.client = client
        self.calls = 0

    def uses_client(self, client: object) -> bool:
        return self.client is client

    def observe_identity(self) -> AccountIdentity:
        self.calls += 1
        return AccountIdentity(
            provider=AccountProvider.ROBINHOOD_CRYPTO,
            account_id="account-123",
            interface="robinhood-crypto-v2",
        )


class Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Decimal]] = []

    def get_estimated_price(
        self, *, symbol: str, side: str, quantity: Decimal
    ) -> RobinhoodCryptoEstimate:
        self.calls.append((symbol, side, quantity))
        return RobinhoodCryptoEstimate(
            symbol=symbol,
            side=side,
            quantity=quantity,
            timestamp=NOW,
            bid=Decimal("99"),
            ask=Decimal("101"),
            fee_ratio=Decimal("0.01"),
            estimated_fee=Decimal("0.1"),
            estimated_total_cost=Decimal("10.2"),
            estimated_total_credit=Decimal("9.8"),
        )


def provider(client: Client, reader: Reader) -> RobinhoodCryptoPreviewProvider:
    return RobinhoodCryptoPreviewProvider(
        client=client,
        account_reader=cast(RobinhoodCryptoAccountReader, cast(Any, reader)),
        clock=lambda: NOW,
    )


def request(
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
) -> PreviewRequest:
    kwargs: dict[str, Any] = {}
    if order_type is OrderType.LIMIT:
        kwargs["limit_price"] = Decimal("100")
    return PreviewRequest(
        instrument="BTC-USD",
        side=side,
        quantity=Decimal("0.1"),
        order_type=order_type,
        **kwargs,
    )


def test_buy_maps_to_ask_and_non_binding_cost_estimate() -> None:
    estimate_client = Client()
    identity_reader = Reader(estimate_client)

    result = provider(estimate_client, identity_reader).preview(request())

    assert identity_reader.calls == 1
    assert estimate_client.calls == [("BTC-USD", "ask", Decimal("0.1"))]
    assert result.provider is AccountProvider.ROBINHOOD_CRYPTO
    assert result.kind is PreviewKind.PROVIDER_ESTIMATE
    assert result.estimated_price == Decimal("101")
    assert result.estimated_fee == Decimal("0.1")
    assert result.estimated_total == Decimal("10.2")
    assert result.errors == ()
    assert "non-binding" in result.warnings[0].lower()


def test_sell_maps_to_bid_and_credit_estimate() -> None:
    estimate_client = Client()
    result = provider(estimate_client, Reader(estimate_client)).preview(request(OrderSide.SELL))

    assert estimate_client.calls == [("BTC-USD", "bid", Decimal("0.1"))]
    assert result.estimated_price == Decimal("99")
    assert result.estimated_total == Decimal("9.8")


def test_limit_preview_is_identity_bound_rejection_without_estimate_dispatch() -> None:
    estimate_client = Client()
    identity_reader = Reader(estimate_client)

    result = provider(estimate_client, identity_reader).preview(request(order_type=OrderType.LIMIT))

    assert identity_reader.calls == 1
    assert estimate_client.calls == []
    assert result.kind is PreviewKind.PROVIDER_ESTIMATE
    assert result.estimated_price is None
    assert "no provider estimate was dispatched" in result.errors[0]


def test_requires_shared_attested_client() -> None:
    estimate_client = Client()

    try:
        provider(estimate_client, Reader(object()))
    except ValueError as exc:
        assert "must match" in str(exc)
    else:
        raise AssertionError("expected shared-client validation")
