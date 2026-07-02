"""Contract tests: Coinbase Advanced Trade accounts -> core Balance objects.

Recorded ``GET /api/v3/brokerage/accounts`` payloads are served at the HTTP
boundary and translated by the production ``CoinbaseRestService`` stack.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from gpt_trader.core import Balance

pytestmark = pytest.mark.contract

ACCOUNTS_PATH = "/api/v3/brokerage/accounts"


def test_list_balances_translates_advanced_trade_accounts(coinbase_service, transport):
    transport.route_fixture("GET", ACCOUNTS_PATH, "accounts")

    balances = coinbase_service.list_balances()

    assert [b.asset for b in balances] == ["USD", "BTC", "ETH"]
    assert all(isinstance(b, Balance) for b in balances)

    usd = balances[0]
    assert usd.available == Decimal("10500.25")
    assert usd.hold == Decimal("250.75")
    # Advanced Trade omits a top-level total; the adapter must derive it.
    assert usd.total == Decimal("10751.00")

    btc = balances[1]
    assert isinstance(btc.available, Decimal)
    assert btc.available == Decimal("0.52815476")
    assert btc.hold == Decimal("0.001")
    assert btc.total == Decimal("0.52915476")

    eth = balances[2]
    assert eth.total == Decimal("0")
    assert eth.available == Decimal("0")


def test_list_balances_hits_accounts_endpoint_once(coinbase_service, transport):
    transport.route_fixture("GET", ACCOUNTS_PATH, "accounts")

    coinbase_service.list_balances()

    assert len(transport.requests_for("GET", ACCOUNTS_PATH)) == 1
