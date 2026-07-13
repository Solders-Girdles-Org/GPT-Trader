from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest

from gpt_trader.app.config import BotConfig
from gpt_trader.features.brokerages.accounts import AccountIdentity, AccountProvider
from gpt_trader.features.brokerages.robinhood.crypto import read_preview_access as access_module
from gpt_trader.features.brokerages.robinhood.crypto.account_access import (
    RobinhoodCryptoAccountReader,
    RobinhoodCryptoViolation,
)
from gpt_trader.features.brokerages.robinhood.crypto.credentials import (
    RobinhoodCryptoCredentials,
)
from gpt_trader.features.brokerages.robinhood.crypto.models import (
    RobinhoodCryptoOrder,
    RobinhoodCryptoQuote,
    RobinhoodCryptoTradingPair,
)
from gpt_trader.features.brokerages.robinhood.crypto.preview_access import (
    RobinhoodCryptoPreviewProvider,
)
from gpt_trader.features.brokerages.robinhood.crypto.read_preview_access import (
    RobinhoodCryptoReadPreviewAccess,
)


class Reader:
    def __init__(self) -> None:
        self.calls = 0

    def observe_identity(self) -> AccountIdentity:
        self.calls += 1
        return AccountIdentity(
            provider=AccountProvider.ROBINHOOD_CRYPTO,
            account_id="account-123",
            interface="robinhood-crypto-v2",
        )


def order(account_number: str = "account-123") -> RobinhoodCryptoOrder:
    return RobinhoodCryptoOrder(
        order_id="order-1",
        account_number=account_number,
        symbol="BTC-USD",
        client_order_id="client-1",
        side="buy",
        order_type="limit",
        state="open",
        average_price=None,
        filled_asset_quantity=Decimal("0"),
        fee_charged=Decimal("0"),
        estimated_fee_remaining=Decimal("0.1"),
        created_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
        executions_json="[]",
        configuration_json='{"limit_order_config":{"limit_price":"10"}}',
    )


def pair() -> RobinhoodCryptoTradingPair:
    return RobinhoodCryptoTradingPair(
        symbol="BTC-USD",
        asset_code="BTC",
        quote_code="USD",
        asset_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        max_order_size=Decimal("100"),
        min_order_amount=Decimal("1"),
        status="tradable",
        is_api_tradable=True,
    )


def quote() -> RobinhoodCryptoQuote:
    return RobinhoodCryptoQuote(symbol="BTC-USD", bid=Decimal("99"), ask=Decimal("101"))


def access(
    reader: Reader,
    *,
    orders: tuple[RobinhoodCryptoOrder, ...] = (),
    closed: list[bool] | None = None,
) -> RobinhoodCryptoReadPreviewAccess:
    def close() -> None:
        if closed is not None:
            closed.append(True)

    return RobinhoodCryptoReadPreviewAccess(
        reader=cast(RobinhoodCryptoAccountReader, cast(Any, reader)),
        preview_provider=cast(RobinhoodCryptoPreviewProvider, cast(Any, object())),
        _list_orders=lambda: orders,
        _list_trading_pairs=lambda symbols: (pair(),),
        _get_quotes=lambda symbols: (quote(),),
        _close_client=close,
    )


def test_exposes_only_typed_capabilities_and_closes_lifecycle() -> None:
    closed: list[bool] = []
    instance = access(Reader(), closed=closed)

    assert not hasattr(instance, "client")
    assert not hasattr(instance, "session")
    assert not hasattr(instance, "request")
    instance.close()

    assert closed == [True]


def test_construction_failure_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class FakeClient:
        def close(self) -> None:
            closed.append(True)

    fake_client = FakeClient()
    monkeypatch.setattr(
        access_module,
        "resolve_robinhood_crypto_credentials",
        lambda: RobinhoodCryptoCredentials(api_key="api-key", private_key="private-key"),
    )
    monkeypatch.setattr(access_module, "RobinhoodCryptoClient", lambda **_: fake_client)

    def fail_preview(**_: object) -> None:
        raise RuntimeError("preview construction failed")

    monkeypatch.setattr(access_module, "RobinhoodCryptoPreviewProvider", fail_preview)
    config = BotConfig(robinhood_crypto_expected_account_number="account-123")

    with pytest.raises(RuntimeError, match="preview construction failed"):
        RobinhoodCryptoReadPreviewAccess.from_config(config)

    assert closed == [True]


def test_order_history_is_identity_bound_and_immutable() -> None:
    identity_reader = Reader()
    evidence = access(identity_reader, orders=(order(),)).read_order_history()

    assert identity_reader.calls == 1
    assert evidence.orders[0].order_id == "order-1"
    assert evidence.identity_fingerprint
    with pytest.raises(AttributeError):
        evidence.orders[0].state = "filled"  # type: ignore[misc]


def test_order_history_rejects_other_account() -> None:
    with pytest.raises(RobinhoodCryptoViolation, match="order account"):
        access(Reader(), orders=(order("wrong"),)).read_order_history()


def test_pair_and_quote_reads_re_attest_identity() -> None:
    identity_reader = Reader()
    instance = access(identity_reader)

    assert instance.list_trading_pairs(("BTC-USD",)) == (pair(),)
    assert instance.get_quotes(("BTC-USD",)) == (quote(),)
    assert identity_reader.calls == 2
