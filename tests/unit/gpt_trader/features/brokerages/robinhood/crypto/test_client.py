from __future__ import annotations

import base64
from decimal import Decimal
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gpt_trader.features.brokerages.robinhood.crypto.client import (
    ROBINHOOD_CRYPTO_BASE_URL,
    RobinhoodCryptoClient,
    RobinhoodCryptoClientViolation,
    RobinhoodCryptoTransportError,
)
from tests.unit.gpt_trader.features.brokerages.robinhood.crypto.client_test_support import (
    ACCOUNT,
    PRIVATE_BYTES,
    Response,
    Session,
    account_row,
    client,
    estimate_row,
    holding_row,
    order_row,
    page,
    pair_row,
)


def test_signs_exact_get_path_and_disables_redirects() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    session = Session([Response(url, page([account_row()]))])

    accounts = client(session).list_accounts()

    assert accounts[0].account_number == ACCOUNT
    called_url, headers, timeout, redirects = session.calls[0]
    assert called_url == url
    assert timeout == 15.0
    assert redirects is False
    message = b"api-key1700000000/api/v2/crypto/trading/accounts/GET"
    signature = base64.b64decode(headers["x-signature"])
    Ed25519PrivateKey.from_private_bytes(PRIVATE_BYTES).public_key().verify(signature, message)


def test_paginates_only_revalidated_exact_urls() -> None:
    first = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/holdings/?account_number={ACCOUNT}"
    second = f"{first}&cursor=next"
    session = Session(
        [
            Response(first, page([holding_row()], next_url=second)),
            Response(second, page([])),
        ]
    )

    holdings = client(session).list_holdings()

    assert holdings[0].available_quantity == Decimal("1.20")
    assert [call[0] for call in session.calls] == [first, second]


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/api/v2/crypto/trading/holdings/?account_number=account-123&cursor=x",
        "https://trading.robinhood.com:443/api/v2/crypto/trading/holdings/?account_number=account-123&cursor=x",
        "http://trading.robinhood.com/api/v2/crypto/trading/holdings/?account_number=account-123&cursor=x",
        "https://trading.robinhood.com/api/v2/crypto/trading/orders/?account_number=account-123&cursor=x",
        "https://trading.robinhood.com/api/v2/crypto/trading/holdings/?account_number=other&cursor=x",
        "https://trading.robinhood.com/api/v2/crypto/trading/holdings/?account_number=account-123&account_number=account-123&cursor=x",
        "https://trading.robinhood.com/api/v2/crypto/trading/holdings/?account_number=account-123&unknown=x",
        "https://trading.robinhood.com/api/v2/crypto/trading/holdings/?account_number=account-123&cursor=",
    ],
)
def test_rejects_pagination_drift_before_second_dispatch(next_url: str) -> None:
    first = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/holdings/?account_number={ACCOUNT}"
    session = Session([Response(first, page([], next_url=next_url))])

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(session).list_holdings()

    assert len(session.calls) == 1


def test_rejects_repeated_page() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    session = Session([Response(url, page([], next_url=url))])

    with pytest.raises(RobinhoodCryptoClientViolation, match="repeated"):
        client(session).list_accounts()


def test_rejects_redirect_and_changed_response_url() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    redirect_session = Session([Response(url, {}, status_code=302)])
    with pytest.raises(RobinhoodCryptoClientViolation, match="redirects"):
        client(redirect_session).list_accounts()

    changed_session = Session([Response(f"{url}?cursor=changed", page([account_row()]))])
    with pytest.raises(RobinhoodCryptoClientViolation, match="response URL"):
        client(changed_session).list_accounts()


def test_no_generic_http_or_session_surface_exists() -> None:
    instance = client(Session([]))

    for name in ("get", "post", "put", "patch", "delete", "request", "session"):
        assert not hasattr(instance, name)


@pytest.mark.parametrize(
    "url",
    [
        "https://trading.robinhood.com/api/v2/crypto/trading/orders/order-1/",
        "https://trading.robinhood.com/api/v2/crypto/trading/orders/order-1/cancel/",
        "https://trading.robinhood.com/api/v2/crypto/trading/orders/",
        "https://trading.robinhood.com/api/v1/crypto/trading/accounts/",
        "https://trading.robinhood.com/api/v2/crypto/trading/estimated_price/?symbol=ETH-EUR&side=ask&quantity=1",
    ],
)
def test_unspecified_or_unbound_targets_fail_before_dispatch(url: str) -> None:
    session = Session([])

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(session)._get_json(url)

    assert session.calls == []


def test_reads_all_typed_operations() -> None:
    orders_url = (
        f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/orders/?account_number={ACCOUNT}"
    )
    pairs_url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/trading_pairs/?symbol=BTC-USD"
    quotes_url = (
        f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/marketdata/best_bid_ask/?symbol=BTC-USD"
    )
    estimate_url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/estimated_price/?symbol=BTC-USD&side=ask&quantity=0.1"
    session = Session(
        [
            Response(orders_url, page([order_row()])),
            Response(pairs_url, page([pair_row()])),
            Response(quotes_url, {"results": [{"symbol": "BTC-USD", "bid": 99, "ask": 101}]}),
            Response(estimate_url, {"results": [estimate_row()]}),
        ]
    )
    instance = client(session)

    assert instance.list_orders()[0].order_id == "order-1"
    assert instance.list_trading_pairs(("btc-usd",))[0].symbol == "BTC-USD"
    assert instance.get_quotes(("BTC-USD",))[0].ask == Decimal("101")
    estimate = instance.get_estimated_price(symbol="BTC-USD", side="ask", quantity=Decimal("0.1"))
    assert estimate.estimated_fee == Decimal("0.10")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"results": "bad", "next": None, "previous": None},
        {"results": ["bad"], "next": None, "previous": None},
        {"results": [account_row()], "next": 1, "previous": None},
        {"results": [account_row()], "next": None},
    ],
)
def test_rejects_malformed_page_payload(payload: Any) -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    session = Session([Response(url, payload)])

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(session).list_accounts()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"schema_drift": True}),
        lambda payload: payload["results"][0].update({"schema_drift": True}),
        lambda payload: payload["results"][0].update({"buying_power": "-1"}),
    ],
)
def test_rejects_account_schema_and_value_drift_before_return(mutate: Any) -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    payload = page([account_row()])
    mutate(payload)
    session = Session([Response(url, payload)])

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(session).list_accounts()


@pytest.mark.parametrize(
    "fee_tier",
    [
        None,
        {
            "fee_ratio": "0.004",
            "thirty_day_volume": "1",
            "next_fee_tier_ratio": None,
            "next_fee_tier_threshold": None,
        },
    ],
)
def test_accepts_official_optional_or_highest_fee_tier(fee_tier: dict[str, Any] | None) -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    row = account_row()
    if fee_tier is None:
        row.pop("fee_tier_status")
    else:
        row["fee_tier_status"] = fee_tier

    result = client(Session([Response(url, page([row]))])).list_accounts()[0]

    assert result.account_number == ACCOUNT
    assert result.next_fee_tier_ratio is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bid", "-1"),
        ("ask", "0"),
        ("fee_ratio", "-0.1"),
        ("est_fee", "-1"),
        ("est_total_cost", "-1"),
        ("est_total_credit", "-1"),
    ],
)
def test_rejects_invalid_estimate_values(field: str, value: str) -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/estimated_price/?symbol=BTC-USD&side=ask&quantity=0.1"
    row = estimate_row()
    row[field] = value
    session = Session([Response(url, {"results": [row]})])

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(session).get_estimated_price(symbol="BTC-USD", side="ask", quantity=Decimal("0.1"))


def test_rejects_estimate_schema_drift_and_inverted_spread() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/estimated_price/?symbol=BTC-USD&side=ask&quantity=0.1"
    drifted = estimate_row()
    drifted["unexpected"] = "field"
    inverted = estimate_row()
    inverted.update({"bid": "102", "ask": "101"})

    for row in (drifted, inverted):
        session = Session([Response(url, {"results": [row]})])
        with pytest.raises(RobinhoodCryptoClientViolation):
            client(session).get_estimated_price(
                symbol="BTC-USD", side="ask", quantity=Decimal("0.1")
            )


def test_rejects_http_error_without_exposing_target() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    session = Session([Response(url, {}, status_code=403)])

    with pytest.raises(RobinhoodCryptoTransportError, match="HTTP 403") as exc_info:
        client(session).list_accounts()

    assert ACCOUNT not in str(exc_info.value)
    assert "api-key" not in str(exc_info.value)


def test_lifecycle_closes_owned_transport_and_blocks_later_use() -> None:
    session = Session([])
    instance = client(session)

    instance.close()
    instance.close()

    assert session.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        instance.list_accounts()


@pytest.mark.parametrize("private_key", ["not-base64", base64.b64encode(b"short").decode()])
def test_rejects_malformed_private_key(private_key: str) -> None:
    with pytest.raises(ValueError, match="private key"):
        RobinhoodCryptoClient(
            api_key="key",
            private_key=private_key,
            expected_account_number=ACCOUNT,
        )


def test_estimate_rejects_response_mismatch() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/estimated_price/?symbol=BTC-USD&side=ask&quantity=0.1"
    row = estimate_row()
    row["side"] = "bid"
    session = Session([Response(url, {"results": [row]})])

    with pytest.raises(RobinhoodCryptoClientViolation, match="does not match"):
        client(session).get_estimated_price(symbol="BTC-USD", side="ask", quantity=Decimal("0.1"))


@pytest.mark.parametrize(
    ("operation", "url", "payload"),
    [
        (
            "accounts",
            f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/",
            page([account_row("wrong")]),
        ),
        (
            "holdings",
            f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/holdings/?account_number={ACCOUNT}",
            page([holding_row("wrong")]),
        ),
        (
            "orders",
            f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/orders/?account_number={ACCOUNT}",
            page([order_row("wrong")]),
        ),
    ],
)
def test_response_account_mismatch_fails_closed(
    operation: str, url: str, payload: dict[str, Any]
) -> None:
    instance = client(Session([Response(url, payload)]))

    with pytest.raises(RobinhoodCryptoClientViolation, match="expected account"):
        getattr(instance, f"list_{operation}")()


def test_previous_page_url_is_validated_even_when_not_followed() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/accounts/"
    payload = page([account_row()])
    payload["previous"] = "https://evil.example/api/v2/crypto/trading/accounts/?cursor=x"
    session = Session([Response(url, payload)])

    with pytest.raises(RobinhoodCryptoClientViolation, match="canonical"):
        client(session).list_accounts()


def test_order_configuration_must_match_order_type() -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/orders/?account_number={ACCOUNT}"
    row = order_row()
    row["market_order_config"] = {"asset_quantity": "1"}
    session = Session([Response(url, page([row]))])

    with pytest.raises(RobinhoodCryptoClientViolation, match="does not match"):
        client(session).list_orders()


@pytest.mark.parametrize(
    "configuration",
    [
        {"asset_quantity": "1", "limit_price": "10", "unexpected": "drift"},
        {"asset_quantity": "-1", "limit_price": "10"},
        {"asset_quantity": "1", "limit_price": "10", "time_in_force": "gtc"},
    ],
)
def test_rejects_order_configuration_schema_or_value_drift(
    configuration: dict[str, str],
) -> None:
    url = f"{ROBINHOOD_CRYPTO_BASE_URL}/api/v2/crypto/trading/orders/?account_number={ACCOUNT}"
    row = order_row()
    row["limit_order_config"] = configuration

    with pytest.raises(RobinhoodCryptoClientViolation):
        client(Session([Response(url, page([row]))])).list_orders()
